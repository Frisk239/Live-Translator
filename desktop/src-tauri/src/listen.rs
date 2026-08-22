//! 听译桥：spawn Python 进程（假听译回放 / 真听译推理）、维护本机 WebSocket、
//! 把缝事件转发给前端。真听译与假听译共用同一条缝（ADR 0008）：
//! JSON 命令（start/switch/stop）走文本帧，PCM 块走二进制帧（f32le/mono/16kHz）。

use std::path::{Path, PathBuf};
use std::sync::Mutex;

use futures_util::{SinkExt, StreamExt};
use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager};
use tokio::sync::mpsc;
use tokio_tungstenite::{connect_async, tungstenite::Message};

#[derive(Clone, Copy, PartialEq, Eq, Debug, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Phase {
    Idle,
    Downloading,
    Listening,
    Failed,
}

/// WS 出站帧：JSON 控制消息（文本）或 PCM 块（二进制）
#[derive(Clone)]
pub enum WsOut {
    Text(String),
    /// f32le / mono / 16kHz，按到达顺序即时间序
    Pcm(Vec<f32>),
}

pub struct ListenState {
    pub phase: Phase,
    pub port: Option<u16>,
    pub(crate) cmd_tx: Option<mpsc::Sender<WsOut>>,
    /// 假听译回放哪个脚本（FAKE_SCRIPT 环境变量；真听译忽略）
    pub script: String,
    pub fake: bool,
}

/// 构造 ListenState 用的空出站通道占位
pub fn none_channel() -> Option<mpsc::Sender<WsOut>> {
    None
}

pub type ListenMutex = Mutex<ListenState>;

/// 父进程的 Job（KILL_ON_JOB_CLOSE）：壳崩溃 / 被杀 / panic 时
/// OS 自动把引擎子进程一并收走，不留孤儿（上一窗崩溃循环的教训）。
fn ensure_job_object() -> Option<windows::Win32::Foundation::HANDLE> {
    use std::sync::OnceLock;
    use windows::Win32::Foundation::HANDLE;
    use windows::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };
    // HANDLE 是裸指针：包 newtype 给它 Sync（内核句柄跨线程用是安全的）
    struct SyncHandle(HANDLE);
    unsafe impl Send for SyncHandle {}
    unsafe impl Sync for SyncHandle {}
    static JOB: OnceLock<SyncHandle> = OnceLock::new();
    if let Some(h) = JOB.get() {
        return Some(h.0);
    }
    unsafe {
        let job = match CreateJobObjectW(None, None) {
            Ok(h) => h,
            Err(_) => return None,
        };
        let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        let ok = SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            &info as *const _ as *const core::ffi::c_void,
            std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        );
        if ok.is_err() {
            return None;
        }
        let _ = JOB.set(SyncHandle(job));
        Some(job)
    }
}

fn first_file(paths: impl IntoIterator<Item = PathBuf>) -> Option<PathBuf> {
    paths.into_iter().find(|p| p.is_file())
}

/// 安装包用内置 Python；开发形态退回 PATH 上的 python + 源码目录脚本。
pub fn resolve_engine(fake: bool, resource_dir: Option<&Path>) -> Option<(PathBuf, PathBuf)> {
    let exe_dir = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.to_path_buf()));
    let dev_root = Path::new(env!("CARGO_MANIFEST_DIR")).join("..");
    let mut python_cands = Vec::new();
    for rel in ["python/python.exe", "runtime/python/python.exe"] {
        if let Some(dir) = resource_dir {
            python_cands.push(dir.join(rel));
        }
        if let Some(dir) = &exe_dir {
            python_cands.push(dir.join(rel));
        }
    }
    let bundled_python = first_file(python_cands);

    let script_rel = if fake {
        "fake-listen/fake_listen.py"
    } else {
        "engine/real_listen.py"
    };
    let mut bundled_scripts = Vec::new();
    if let Some(dir) = resource_dir {
        bundled_scripts.push(dir.join(script_rel));
        if !fake {
            bundled_scripts.push(dir.join("engine").join("real_listen.py"));
        }
    }
    if let Some(dir) = &exe_dir {
        bundled_scripts.push(dir.join(script_rel));
    }
    let dev_script = dev_root.join(script_rel);

    if let Some(python) = bundled_python {
        let script = first_file(bundled_scripts).or_else(|| dev_script.is_file().then_some(dev_script))?;
        return Some((python, script));
    }
    let script = first_file(std::iter::once(dev_script).chain(bundled_scripts))?;
    Some((PathBuf::from("python"), script))
}

/// 起听译进程（假 = fake-listen/fake_listen.py，真 = engine/real_listen.py）。
/// 返回它绑定的端口；READY 行没等到就放弃（开听时面板会显示失败原因）。
pub fn spawn_listen(
    fake: bool,
    models_dir: Option<&Path>,
    resource_dir: Option<&Path>,
) -> (Option<u16>, Option<std::process::Child>) {
    let Some((python, script_path)) = resolve_engine(fake, resource_dir) else {
        return (None, None);
    };

    #[cfg(windows)]
    let mut cmd = {
        let mut c = std::process::Command::new(&python);
        use std::os::windows::process::CommandExt;
        c.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
        c
    };
    #[cfg(not(windows))]
    let mut cmd = std::process::Command::new(&python);

    if let Some(dir) = python.parent() {
        if dir.as_os_str().is_empty() {
            // PATH 上的 `python`，不要改环境
        } else if let Ok(old) = std::env::var("PATH") {
            cmd.env("PATH", format!("{};{}", dir.display(), old));
        }
        cmd.env("PYTHONUTF8", "1");
        cmd.env("PYTHONNOUSERSITE", "1");
    }

    cmd.arg(&script_path).arg("--port").arg("0");
    if let Some(dir) = models_dir {
        cmd.arg("--models-dir").arg(dir);
    }
    let mut child = match cmd
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .spawn()
    {
        Ok(c) => c,
        Err(_) => return (None, None),
    };
    // 崩溃兜底：进 Job，壳死了 OS 连带孩子一起收
    #[cfg(windows)]
    use std::os::windows::io::AsRawHandle;
    if let Some(job) = ensure_job_object() {
        let raw = child.as_raw_handle();
        unsafe {
            let _ = windows::Win32::System::JobObjects::AssignProcessToJobObject(
                job,
                windows::Win32::Foundation::HANDLE(raw),
            );
        }
    }

    use std::io::BufRead;
    if let Some(stdout) = child.stdout.take() {
        let reader = std::io::BufReader::new(stdout);
        for line in reader.lines() {
            let Ok(line) = line else { break };
            if let Some(rest) = line.strip_prefix("READY ") {
                if let Ok(port) = rest.trim().parse::<u16>() {
                    return (Some(port), Some(child));
                }
            }
        }
    }
    (None, Some(child))
}

fn connection_state<R: tauri::Runtime>(app: &tauri::AppHandle<R>) -> (Option<u16>, bool) {
    let state = app.state::<crate::AppState>();
    {
        let g = state.listen.lock().unwrap();
        let need = g.cmd_tx.as_ref().map_or(true, |tx| tx.is_closed());
        (g.port, need)
    }
}

/// 确保 WS 连着；断了就重连。
pub async fn ensure_connected(app: &AppHandle) -> Result<(), String> {
    let (port, need) = connection_state(app);
    if !need {
        return Ok(());
    }
    let port = port.ok_or_else(|| "听译进程没起来".to_string())?;
    let ws = tokio::time::timeout(
        std::time::Duration::from_secs(3),
        connect_async(format!("ws://127.0.0.1:{port}")),
    )
    .await
    .map_err(|_| format!("连听译超时（127.0.0.1:{port}）"))?
    .map_err(|e| format!("连不上听译：{e}"))?;
    let (ws, _) = ws;
    let (mut write, mut read) = ws.split();
    let (tx, mut rx) = mpsc::channel::<WsOut>(64);
    tauri::async_runtime::spawn(async move {
        while let Some(msg) = rx.recv().await {
            let out = match msg {
                WsOut::Text(s) => Message::Text(s.into()),
                WsOut::Pcm(chunk) => {
                    let mut bytes = Vec::with_capacity(chunk.len() * 4);
                    for f in chunk {
                        bytes.extend_from_slice(&f.to_le_bytes());
                    }
                    Message::Binary(bytes.into())
                }
            };
            if write.send(out).await.is_err() {
                break;
            }
        }
        let _ = write.close().await;
    });
    let app2 = app.clone();
    tauri::async_runtime::spawn(async move {
        while let Some(Ok(msg)) = read.next().await {
            if let Message::Text(txt) = msg {
                route_event(&app2, &txt);
            }
        }
        // 连接断了：还在听就当听译挂了处理
        let phase = app2.state::<crate::AppState>().listen.lock().unwrap().phase;
        if phase == Phase::Listening {
            crate::set_phase(&app2, Phase::Failed);
            crate::notify(&app2, "听译停了", "字幕先撤了。点托盘图标回控制面板重试。");
        }
    });
    let state = app.state::<crate::AppState>();
    state.listen.lock().unwrap().cmd_tx = Some(tx);
    Ok(())
}

pub async fn send(app: &AppHandle, cmd: serde_json::Value) -> Result<(), String> {
    ensure_connected(app).await?;
    let state = app.state::<crate::AppState>();
    let tx = state.listen.lock().unwrap().cmd_tx.clone();
    let tx = tx.ok_or_else(|| "听译连接还没建立".to_string())?;
    tx.send(WsOut::Text(cmd.to_string()))
        .await
        .map_err(|e| e.to_string())
}

/// PCM 块入缝（采音线程调用，非阻塞；满了就丢——实时流丢帧好过背压）。
/// 未连接 / 假听译时静默丢弃（假听译对二进制帧一律忽略）。
pub fn push_pcm(app: &AppHandle, chunk: Vec<f32>) {
    let state = app.state::<crate::AppState>();
    let (fake, tx) = {
        let g = state.listen.lock().unwrap();
        (g.fake, g.cmd_tx.clone())
    };
    if fake {
        return;
    }
    if let Some(tx) = tx {
        let _ = tx.try_send(WsOut::Pcm(chunk));
    }
}

fn route_event(app: &AppHandle, raw: &str) {
    let Ok(v) = serde_json::from_str::<serde_json::Value>(raw) else {
        return;
    };
    // 音源抓不到 / 听译挂了：壳要停开听、撤字幕窗；no_speech / not_lang 只转发
    if v["type"] == "notice" && (v["kind"] == "no_audio" || v["kind"] == "crashed") {
        crate::set_phase(app, Phase::Failed);
        if v["kind"] == "crashed" {
            crate::notify(app, "听译停了", "字幕先撤了。点托盘图标回控制面板重试。");
        }
    }
    let _ = app.emit("listen://event", v);
}

#[cfg(test)]
mod tests {
    use super::*;

    fn app_state() -> crate::AppState {
        crate::AppState {
            settings: Mutex::new(crate::Settings::default()),
            listen: Mutex::new(ListenState {
                phase: Phase::Idle,
                port: None,
                cmd_tx: None,
                script: "en".into(),
                fake: false,
            }),
            child: Mutex::new(None),
            tray_toggle: Mutex::new(None),
            tray_autostart: Mutex::new(None),
            models_dir: std::env::temp_dir(),
            resource_dir: None,
            capture: Mutex::new(None),
            capture_pid: std::sync::atomic::AtomicU32::new(0),
        }
    }

    #[test]
    fn resolve_engine_prefers_bundled_python() {
        let tmp = std::env::temp_dir().join(format!("lt-engine-{}", std::process::id()));
        let py_dir = tmp.join("python");
        let engine_dir = tmp.join("engine");
        std::fs::create_dir_all(&py_dir).unwrap();
        std::fs::create_dir_all(&engine_dir).unwrap();
        std::fs::write(py_dir.join("python.exe"), b"").unwrap();
        std::fs::write(engine_dir.join("real_listen.py"), b"").unwrap();
        let (python, script) = resolve_engine(false, Some(&tmp)).expect("bundled runtime");
        assert_eq!(python, py_dir.join("python.exe"));
        assert_eq!(script, engine_dir.join("real_listen.py"));
        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn connection_state_uses_the_managed_app_state() {
        let app = tauri::test::mock_builder()
            .manage(app_state())
            .build(tauri::test::mock_context(tauri::test::noop_assets()))
            .unwrap();

        assert_eq!(connection_state(app.handle()), (None, true));
    }
}
