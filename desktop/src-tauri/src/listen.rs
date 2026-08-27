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
    /// 托管听译 WSS；本机为 None
    pub remote_url: Option<String>,
    pub auth_token: Option<String>,
    /// 连接代号：每建一条新缝自增。旧缝断掉后的残余事件不认新缝的状态（被顶误伤防线）
    pub conn_gen: u64,
    /// 这次开听用的 start 载荷；托管闪断时用它再开一路（ADR 0019）
    pub last_start: Option<serde_json::Value>,
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

/// 托管听译不占本机 Python 引擎、不拉本机模型。
pub fn should_spawn_local(listen_way: &str, fake: bool, models_on_disk: bool) -> bool {
    if listen_way == "hosted" {
        return false;
    }
    fake || models_on_disk
}

pub fn should_download_local(listen_way: &str, model_ready: bool) -> bool {
    listen_way != "hosted" && !model_ready
}

/// 起听译进程（假 = fake-listen/fake_listen.py，真 = engine/real_listen.py）。
/// 返回 (绑定端口, 子进程, 失败原因)：READY 没等到时 port 为 None，原因进第三项（开听时面板显示）。
pub fn spawn_listen(
    fake: bool,
    models_dir: Option<&Path>,
    resource_dir: Option<&Path>,
) -> (Option<u16>, Option<std::process::Child>, Option<String>) {
    let Some((python, script_path)) = resolve_engine(fake, resource_dir) else {
        return (None, None, Some("找不到 Python 或引擎脚本（安装包请重装）".into()));
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
        .stderr(std::process::Stdio::piped())
        .spawn()
    {
        Ok(c) => c,
        Err(e) => return (None, None, Some(format!("引擎进程起不来（{}）：{e}", python.display()))),
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

    // Python 的 traceback 打到 stderr：转发到壳的 stderr（开发形态可见），不再一丢了之
    if let Some(stderr) = child.stderr.take() {
        std::thread::spawn(move || {
            use std::io::BufRead;
            for line in std::io::BufReader::new(stderr).lines().map_while(|l| l.ok()) {
                eprintln!("[engine] {line}");
            }
        });
    }

    // READY 在 WS 绑定后立刻打印（模型加载是引擎的后台任务），等待只需盖住解释器启动；
    // 读线程拿到 READY 后继续 drain，不退出——引擎后续输出写满管道会把孩子堵死
    let (tx, rx) = std::sync::mpsc::channel::<u16>();
    if let Some(stdout) = child.stdout.take() {
        std::thread::spawn(move || {
            use std::io::BufRead;
            for line in std::io::BufReader::new(stdout).lines().map_while(|l| l.ok()) {
                if let Some(rest) = line.strip_prefix("READY ") {
                    if let Ok(port) = rest.trim().parse::<u16>() {
                        let _ = tx.send(port);
                        continue;
                    }
                }
                eprintln!("[engine] {line}");
            }
        });
    }
    match rx.recv_timeout(std::time::Duration::from_secs(30)) {
        Ok(port) => (Some(port), Some(child), None),
        Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {
            let _ = child.kill();
            let _ = child.wait();
            (None, None, Some("引擎 30 秒内没就绪，已把它收掉。稍后重开应用会再试".into()))
        }
        // 输出流先结束：进程退出（多为缺依赖秒崩），traceback 已在壳的 stderr（[engine] 开头）
        Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => {
            let _ = child.wait();
            (None, None, Some("引擎起来了又立刻退出，开发形态看壳的 stderr 日志查缺什么".into()))
        }
    }
}

fn connection_state<R: tauri::Runtime>(
    app: &tauri::AppHandle<R>,
) -> (Option<u16>, bool, Option<String>, Option<String>) {
    let state = app.state::<crate::AppState>();
    {
        let g = state.listen.lock().unwrap();
        let need = g.cmd_tx.as_ref().map_or(true, |tx| tx.is_closed());
        (g.port, need, g.remote_url.clone(), g.auth_token.clone())
    }
}

/// 确保 WS 连着；断了就重连。返回是否刚建了新缝。
pub async fn ensure_connected(app: &AppHandle) -> Result<bool, String> {
    let (port, need, remote, auth) = connection_state(app);
    if !need {
        return Ok(false);
    }
    let url = if let Some(remote) = remote.clone() {
        remote
    } else {
        let port = port.ok_or_else(|| "听译进程没起来".to_string())?;
        format!("ws://127.0.0.1:{port}")
    };
    let ws = tokio::time::timeout(
        std::time::Duration::from_secs(5),
        connect_async(url.clone()),
    )
    .await
    .map_err(|_| format!("连听译超时（{url}）"))?
    .map_err(|e| format!("连不上听译：{e}"))?;
    let (ws, _) = ws;
    let (mut write, mut read) = ws.split();
    let my_gen = {
        let state = app.state::<crate::AppState>();
        let mut g = state.listen.lock().unwrap();
        g.conn_gen += 1;
        g.conn_gen
    };
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
    let was_remote = remote.is_some();
    tauri::async_runtime::spawn(async move {
        while let Some(Ok(msg)) = read.next().await {
            if let Message::Text(txt) = msg {
                route_event(&app2, my_gen, &txt);
            }
        }
        on_reader_end(&app2, my_gen, was_remote);
    });
    let state = app.state::<crate::AppState>();
    state.listen.lock().unwrap().cmd_tx = Some(tx.clone());
    if let Some(token) = auth {
        tx.send(WsOut::Text(
            serde_json::json!({ "type": "auth", "token": token }).to_string(),
        ))
        .await
        .map_err(|e| format!("没能把登录交给听译：{e}"))?;
    }
    Ok(true)
}

/// 缝断了：终态提示（被顶 / 满员 / 登录失效 / 崩了）已把 phase 打成 Failed，
/// 这里只兜底其余情况；托管闪断则自动再开一路（ADR 0019 / 0020）。
fn on_reader_end(app: &AppHandle, gen: u64, remote: bool) {
    {
        let st = app.state::<crate::AppState>();
        let mut g = st.listen.lock().unwrap();
        if g.conn_gen != gen {
            return; // 新缝已接手，旧缝的收尾静默
        }
        g.cmd_tx = None; // 这条缝作废，下一次发送会重连
    }
    let phase = app.state::<crate::AppState>().listen.lock().unwrap().phase;
    if phase != Phase::Listening {
        return;
    }
    if !remote {
        crate::set_phase(app, Phase::Failed);
        crate::notify(app, "听译停了", "字幕先撤了。点托盘图标回控制面板重试。");
        return;
    }
    // 独立任务里再开：不与 ensure_connected 的连接建立互相 await（Send 判定的环）
    tauri::async_runtime::spawn(retry_reopen(app.clone(), gen));
}

/// 托管闪断 / 计划内重启：同登录同音源再开一路；重试用尽算真断网，
/// 停并说明，不自动切本机（spec 故事 23 / 25 / 35）。
async fn retry_reopen(app: AppHandle, gen: u64) {
    const BACKOFF_MS: [u64; 5] = [600, 1200, 2400, 4800, 9600];
    for wait in BACKOFF_MS {
        tokio::time::sleep(std::time::Duration::from_millis(wait)).await;
        let cmd = {
            let st = app.state::<crate::AppState>();
            let g = st.listen.lock().unwrap();
            if g.phase != Phase::Listening || g.conn_gen != gen {
                return; // 观众按了停，或新缝已接手
            }
            g.last_start.clone()
        };
        let Some(cmd) = cmd else { break };
        if send(&app, cmd).await.is_ok() {
            return; // 再开成功：新缝自己的读循环接手
        }
    }
    let still = {
        let st = app.state::<crate::AppState>();
        let g = st.listen.lock().unwrap();
        g.phase == Phase::Listening && g.conn_gen == gen
    };
    if still {
        crate::set_phase(&app, Phase::Failed);
        crate::notify(&app, "托管听译停了", "重试没能连上。等网络恢复后重新开听，或改用本机。");
    }
}

pub async fn send(app: &AppHandle, cmd: serde_json::Value) -> Result<(), String> {
    let reconnected = ensure_connected(app).await?;
    // 记下「这次开的什么」，闪断再开要用；switch 只换音源
    if cmd["type"] == "start" || cmd["type"] == "switch" {
        let state = app.state::<crate::AppState>();
        let mut g = state.listen.lock().unwrap();
        if cmd["type"] == "start" {
            g.last_start = Some(cmd.clone());
        } else {
            let payload = match g.last_start.as_mut() {
                Some(prev) => {
                    prev["source"] = cmd["source"].clone();
                    prev.clone()
                }
                None => serde_json::json!({ "type": "start", "source": cmd["source"], "translate": "ct2" }),
            };
            g.last_start = Some(payload);
        }
    }
    let state = app.state::<crate::AppState>();
    let tx = state.listen.lock().unwrap().cmd_tx.clone();
    let tx = tx.ok_or_else(|| "听译连接还没建立".to_string())?;
    // 重连窗口里换音源：新缝还没 start 过，switch 要升级成 start
    let out = if cmd["type"] == "switch" && reconnected {
        state
            .listen
            .lock()
            .unwrap()
            .last_start
            .clone()
            .unwrap_or_else(|| cmd.clone())
    } else {
        cmd
    };
    tx.send(WsOut::Text(out.to_string()))
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

/// 终态提示：收到即停开听、撤字幕窗；其余（no_speech / not_lang）只转发给面板。
pub fn is_terminal_notice(kind: &str) -> bool {
    matches!(kind, "no_audio" | "crashed" | "kicked" | "full" | "auth")
}

fn route_event(app: &AppHandle, gen: u64, raw: &str) {
    let Ok(v) = serde_json::from_str::<serde_json::Value>(raw) else {
        return;
    };
    {
        let st = app.state::<crate::AppState>();
        if st.listen.lock().unwrap().conn_gen != gen {
            return; // 旧缝的残余事件：新缝已接手，不进面板也不改状态
        }
    }
    if v["type"] == "notice" {
        match v["kind"].as_str().unwrap_or("") {
            "no_audio" => {
                crate::set_phase(app, Phase::Failed);
            }
            "crashed" => {
                crate::set_phase(app, Phase::Failed);
                crate::notify(app, "听译停了", "字幕先撤了。点托盘图标回控制面板重试。");
            }
            // 被顶：后开的挤掉先开的，这边停听撤条，不自动开回来（ADR 0020）
            "kicked" => {
                crate::set_phase(app, Phase::Failed);
                crate::notify(
                    app,
                    "已在别处开听",
                    "同一账号在别处开了托管听译，这边停了。要在这台继续，回面板重新开听。",
                );
            }
            // 满员：新开被拒，已开的不受影响（ADR 0016）
            "full" => {
                crate::set_phase(app, Phase::Failed);
                crate::notify(
                    app,
                    "现在满了",
                    "这台机器同时开的听译已到上限，已开的不受影响。稍后再试。",
                );
            }
            // 登录失效（别处改了密码 / 退出）：清掉这台的登录，面板回登录页
            "auth" => {
                crate::set_phase(app, Phase::Failed);
                drop_saved_hosted(app);
                crate::notify(app, "登录已失效", "重新登录后才能再开托管听译。");
            }
            _ => {}
        }
    }
    let _ = app.emit("listen://event", v);
}

fn drop_saved_hosted(app: &AppHandle) {
    let st = app.state::<crate::AppState>();
    crate::hosted::clear_remembered();
    *st.hosted.lock().unwrap() = None;
    let _ = app.emit("hosted://account", serde_json::Value::Null);
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
                remote_url: None,
                auth_token: None,
                conn_gen: 0,
                last_start: None,
            }),
            child: Mutex::new(None),
            tray_toggle: Mutex::new(None),
            tray_autostart: Mutex::new(None),
            models_dir: std::env::temp_dir(),
            resource_dir: None,
            capture: Mutex::new(None),
            capture_pid: std::sync::atomic::AtomicU32::new(0),
            hosted: Mutex::new(None),
        }
    }

    #[test]
    fn terminal_notices_stop_listening() {
        for kind in ["no_audio", "crashed", "kicked", "full", "auth"] {
            assert!(is_terminal_notice(kind), "{kind} 应为终态");
        }
        for kind in ["no_speech", "not_lang", "unknown"] {
            assert!(!is_terminal_notice(kind), "{kind} 不是终态");
        }
    }

    #[test]
    fn hosted_skips_local_spawn_and_download() {
        assert!(!should_spawn_local("hosted", false, true));
        assert!(!should_spawn_local("hosted", true, true));
        assert!(!should_download_local("hosted", false));
        assert!(should_spawn_local("local", false, true));
        assert!(!should_spawn_local("local", false, false));
        assert!(should_spawn_local("local", true, false));
        assert!(should_download_local("local", false));
        assert!(!should_download_local("local", true));
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

        assert_eq!(connection_state(app.handle()), (None, true, None, None));
    }
}
