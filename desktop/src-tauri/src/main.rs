//! 直播同传工具 · 壳。三件事：托盘、控制面板、字幕窗（PRD 已定）。
//! 壳把选定的音源交给听译，听译回「草稿 / 定稿 / 提示」事件（见 listen.rs 的缝）。

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod capture;
mod cursor_watch;
mod listen;
mod llm;
mod models;
mod sources;

use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use tauri::menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Emitter, Manager, PhysicalPosition, PhysicalSize, RunEvent, WindowEvent};
use tauri_plugin_autostart::MacosLauncher;
use tauri_plugin_store::StoreExt;

use listen::{ListenMutex, ListenState, Phase};

#[derive(Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase", default)]
struct Settings {
    source: Option<String>,
    mode: String, // both | orig | trans
    font: String, // s | m | l
    face: String,  // yahei | hei | song
    style: String, // outline | yellow | plate
    ink: String,   // #rrggbb
    edge: String,  // none | thin | thick
    plate: String, // none | soft | hard
    weight: String, // regular | bold
    autostart: bool,
    model_ready: bool,
    listen_way: String, // local | local_llm | hosted
    subtitle_rect: Option<RectSaved>,
}

#[derive(Clone, Copy, Serialize, Deserialize)]
struct RectSaved {
    x: i32,
    y: i32,
    width: u32,
    height: u32,
}

struct AppState {
    settings: Mutex<Settings>,
    listen: ListenMutex,
    child: Mutex<Option<std::process::Child>>,
    tray_toggle: Mutex<Option<MenuItem<tauri::Wry>>>,
    tray_autostart: Mutex<Option<CheckMenuItem<tauri::Wry>>>,
    models_dir: std::path::PathBuf,
    resource_dir: Option<std::path::PathBuf>,
    capture: Mutex<Option<capture::CaptureHandle>>,
    /// 采音实际绑定的 pid（进程音源）；系统混音为 None
    capture_pid: std::sync::atomic::AtomicU32,
}

fn current_phase(app: &AppHandle) -> Phase {
    app.state::<AppState>().listen.lock().unwrap().phase
}

fn notify(app: &AppHandle, title: &str, body: &str) {
    use tauri_plugin_notification::NotificationExt;
    let _ = app.notification().builder().title(title).body(body).show();
}

/// phase 的唯一写口：同步托盘菜单文字、字幕窗显隐，再广播给前端。
fn set_phase(app: &AppHandle, phase: Phase) {
    {
        let st = app.state::<AppState>();
        st.listen.lock().unwrap().phase = phase;
    }
    if let Some(sub) = app.get_webview_window("subtitle") {
        match phase {
            Phase::Listening => {
                let _ = sub.show();
                let _ = sub.set_ignore_cursor_events(true);
            }
            _ => {
                let _ = sub.hide();
            }
        }
    }
    update_tray(app);
    let source_label = app
        .state::<AppState>()
        .settings
        .lock()
        .unwrap()
        .source
        .clone()
        .map(|s| sources::label(&s))
        .unwrap_or_default();
    let _ = app.emit(
        "app://phase",
        serde_json::json!({ "phase": phase, "sourceLabel": source_label }),
    );
}

fn update_tray(app: &AppHandle) {
    let st = app.state::<AppState>();
    let phase = st.listen.lock().unwrap().phase;
    let listening = phase == Phase::Listening;
    let item = st.tray_toggle.lock().unwrap().clone();
    if let Some(item) = item {
        let _ = item.set_text(if listening { "停止" } else { "开听" });
    }
}

fn save_settings(app: &AppHandle) {
    let st = app.state::<AppState>();
    let settings = st.settings.lock().unwrap().clone();
    if let Ok(store) = app.store("settings.json") {
        let _ = store.set(
            "settings",
            serde_json::to_value(&settings).unwrap_or_default(),
        );
        let _ = store.save();
    }
    let _ = app.emit(
        "settings://changed",
        serde_json::to_value(&settings).unwrap_or_default(),
    );
}

/// 托盘始终负责「唤起」控制面板，不做显隐切换：
/// 这样左键的 Down / Up 两类事件即使都到达，也不会把面板又藏回去。
fn show_panel(app: &AppHandle) {
    if let Some(panel) = app.get_webview_window("panel") {
        let _ = panel.show();
        let _ = panel.unminimize();
        // 浮到最上层但不霸占置顶：TOPMOST 一瞬再取消，
        // 否则被别的置顶窗口盖住时「点了也没反应」。
        let _ = panel.set_always_on_top(true);
        let _ = panel.set_focus();
        let p = panel.clone();
        std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(250));
            let _ = p.set_always_on_top(false);
        });
    }
}

/// 把选中的音源解析成采音目标：系统混音走默认设备环回，进程走 Application Loopback
fn resolve_capture_source(source: &str) -> Result<capture::CaptureSource, String> {
    if source == "system" {
        return Ok(capture::CaptureSource::System);
    }
    let pid = sources::audible_pid(source).or_else(|| sources::any_pid(source));
    pid.map(capture::CaptureSource::Process)
        .ok_or_else(|| format!("{source}：进程没找到"))
}

fn start_capture(app: &AppHandle, source: &str) -> Result<(), String> {
    stop_capture(app);
    let cs = resolve_capture_source(source)?;
    let bound_pid = match &cs {
        capture::CaptureSource::Process(pid) => *pid,
        capture::CaptureSource::System => 0,
    };
    app.state::<AppState>()
        .capture_pid
        .store(bound_pid, std::sync::atomic::Ordering::Relaxed);
    let (tx, rx) = std::sync::mpsc::channel::<Vec<f32>>();
    let app2 = app.clone();
    std::thread::spawn(move || {
        while let Ok(chunk) = rx.recv() {
            listen::push_pcm(&app2, chunk);
        }
    });
    let handle = capture::spawn_capture(cs, tx)?;
    *app.state::<AppState>().capture.lock().unwrap() = Some(handle);
    Ok(())
}

fn stop_capture(app: &AppHandle) {
    let old = app.state::<AppState>().capture.lock().unwrap().take();
    if let Some(h) = old {
        h.stop();
    }
    app.state::<AppState>()
        .capture_pid
        .store(0, std::sync::atomic::Ordering::Relaxed);
}

async fn do_start(app: AppHandle, source: String) -> Result<(), String> {
    {
        let st = app.state::<AppState>();
        if !st.settings.lock().unwrap().model_ready {
            return Err("模型还没装好，装完自动能听".into());
        }
    }
    let (fake, script, translate) = {
        let st = app.state::<AppState>();
        let ls = st.listen.lock().unwrap();
        (ls.fake, ls.script.clone(), "ct2")
    };
    if fake {
        listen::send(
            &app,
            serde_json::json!({
                "type": "start",
                "source": source,
                "translate": translate,
                "playback": { "script": script, "speed": 1 }
            }),
        )
        .await?;
    } else {
        // 真采音：激活（同步等 READY，最多 10s）放阻塞线程池，
        // 别钉死 async command 的 worker——WS 连接与它同一个运行时
        let app_c = app.clone();
        let src_c = source.clone();
        let cap = tauri::async_runtime::spawn_blocking(move || start_capture(&app_c, &src_c))
            .await
            .map_err(|e| format!("采音线程崩了：{e}"))?;
        // 激活失败 = 音源抓不到，走 no_audio 出口
        if let Err(e) = cap {
            let _ = app.emit(
                "listen://event",
                serde_json::json!({ "type": "notice", "kind": "no_audio" }),
            );
            set_phase(&app, Phase::Failed);
            return Err(e);
        }
        if let Err(e) = listen::send(
            &app,
            serde_json::json!({ "type": "start", "source": source, "translate": translate }),
        )
        .await
        {
            stop_capture(&app); // 采音已起但听译连不上：别留活采音
            return Err(e);
        }
    }
    set_phase(&app, Phase::Listening);
    Ok(())
}

async fn do_stop(app: AppHandle) {
    stop_capture(&app);
    let _ = listen::send(&app, serde_json::json!({ "type": "stop" })).await;
    set_phase(&app, Phase::Idle);
}

// ---------- 前端命令 ----------

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct BootInfo {
    settings: Settings,
    sources: Vec<sources::AudioSource>,
    source_dead: bool,
    weak: bool,
    phase: Phase,
    download_pct: f64,
}

#[tauri::command]
fn get_boot(app: AppHandle) -> Result<BootInfo, String> {
    boot_info(&app)
}

fn boot_info<R: tauri::Runtime>(app: &AppHandle<R>) -> Result<BootInfo, String> {
    let Some(st) = app.try_state::<AppState>() else {
        return Err("控制面板正在启动".into());
    };
    let settings = st.settings.lock().unwrap().clone();
    let list = sources::list_with_remembered(settings.source.as_deref());
    let source_dead = match &settings.source {
        Some(s) => s != "system" && !list.iter().any(|x| x.id == *s),
        None => false,
    };
    let ls = st.listen.lock().unwrap();
    Ok(BootInfo {
        settings,
        sources: list,
        source_dead,
        weak: std::env::var("LT_FAKE_WEAK").is_ok(),
        phase: ls.phase,
        download_pct: match ls.phase {
            Phase::Downloading => 1.0,
            _ => 0.0,
        },
    })
}

#[tauri::command]
fn refresh_sources(app: AppHandle) -> Vec<sources::AudioSource> {
    let remembered = {
        let st = app.state::<AppState>();
        let guard = st.settings.lock().unwrap();
        guard.source.clone()
    };
    sources::list_with_remembered(remembered.as_deref())
}

#[tauri::command]
async fn listen_start(app: AppHandle, source: String) -> Result<(), String> {
    do_start(app, source).await
}

#[tauri::command]
async fn listen_stop(app: AppHandle) {
    do_stop(app).await
}

/// 选音源：记住选择；开听中 = 立刻切到新音源不停（PRD 建议 6）。
#[tauri::command]
async fn select_source(app: AppHandle, source: String) -> Result<(), String> {
    {
        let st = app.state::<AppState>();
        st.settings.lock().unwrap().source = Some(source.clone());
    }
    save_settings(&app);
    if current_phase(&app) == Phase::Listening {
        let fake = app.state::<AppState>().listen.lock().unwrap().fake;
        if !fake {
            let app_c = app.clone();
            let src_c = source.clone();
            let cap = tauri::async_runtime::spawn_blocking(move || start_capture(&app_c, &src_c))
                .await
                .map_err(|e| format!("采音线程崩了：{e}"))?;
            // 换音源 = 立刻切到新一路 PCM（激活失败走 no_audio 出口）
            if let Err(e) = cap {
                let _ = app.emit(
                    "listen://event",
                    serde_json::json!({ "type": "notice", "kind": "no_audio" }),
                );
                set_phase(&app, Phase::Failed);
                return Err(e);
            }
        }
        listen::send(
            &app,
            serde_json::json!({ "type": "switch", "source": source }),
        )
        .await?;
        let _ = app.emit(
            "app://source_switched",
            serde_json::json!({ "sourceLabel": sources::label(&source), "now": now_ms() }),
        );
    }
    Ok(())
}

#[tauri::command]
fn set_mode(app: AppHandle, mode: String) {
    app.state::<AppState>().settings.lock().unwrap().mode = mode;
    save_settings(&app);
}

#[tauri::command]
fn set_font(app: AppHandle, font: String) {
    app.state::<AppState>().settings.lock().unwrap().font = font;
    save_settings(&app);
}

#[tauri::command]
fn set_face(app: AppHandle, face: String) {
    let face = match face.as_str() {
        "hei" | "song" => face,
        _ => "yahei".into(),
    };
    app.state::<AppState>().settings.lock().unwrap().face = face;
    save_settings(&app);
}

#[tauri::command]
fn set_style(app: AppHandle, style: String) {
    let (style, ink, edge, plate) = match style.as_str() {
        "yellow" => ("yellow", "#ffe566", "thick", "none"),
        "plate" => ("plate", "#ffffff", "none", "hard"),
        _ => ("outline", "#ffffff", "thick", "none"),
    };
    {
        let st = app.state::<AppState>();
        let mut g = st.settings.lock().unwrap();
        g.style = style.into();
        g.ink = ink.into();
        g.edge = edge.into();
        g.plate = plate.into();
        g.weight = "bold".into();
    }
    save_settings(&app);
}

#[tauri::command]
fn set_ink(app: AppHandle, ink: String) {
    if !ink.starts_with('#') || ink.len() != 7 {
        return;
    }
    app.state::<AppState>().settings.lock().unwrap().ink = ink;
    save_settings(&app);
}

#[tauri::command]
fn set_edge(app: AppHandle, edge: String) {
    let edge = match edge.as_str() {
        "none" | "thin" => edge,
        _ => "thick".into(),
    };
    app.state::<AppState>().settings.lock().unwrap().edge = edge;
    save_settings(&app);
}

#[tauri::command]
fn set_plate(app: AppHandle, plate: String) {
    let plate = match plate.as_str() {
        "soft" | "hard" => plate,
        _ => "none".into(),
    };
    app.state::<AppState>().settings.lock().unwrap().plate = plate;
    save_settings(&app);
}

#[tauri::command]
fn set_weight(app: AppHandle, weight: String) {
    let weight = if weight == "regular" { "regular" } else { "bold" };
    app.state::<AppState>().settings.lock().unwrap().weight = weight.into();
    save_settings(&app);
}

#[tauri::command]
fn set_listen_way(app: AppHandle, way: String) {
    if current_phase(&app) == Phase::Listening {
        return;
    }
    let way = match way.as_str() {
        "hosted" => "hosted",
        _ => "local",
    };
    app.state::<AppState>().settings.lock().unwrap().listen_way = way.into();
    save_settings(&app);
}

#[tauri::command]
fn get_llm_config(app: AppHandle) -> llm::LlmConfig {
    let dir = app.state::<AppState>().models_dir.clone();
    llm::load(&dir)
}

#[tauri::command]
fn set_llm_config(app: AppHandle, config: llm::LlmConfig) -> Result<(), String> {
    let dir = app.state::<AppState>().models_dir.clone();
    llm::save(&dir, &config)
}

#[tauri::command]
async fn test_llm_config(config: llm::LlmConfig) -> llm::LlmProbe {
    llm::probe(&config).await
}

#[tauri::command]
async fn list_llm_models(config: llm::LlmConfig) -> llm::LlmModelList {
    llm::list_models(&config).await
}

#[tauri::command]
async fn probe_llm_thinking(config: llm::LlmConfig) -> llm::LlmThinkProbe {
    llm::probe_thinking(&config).await
}

#[tauri::command]
async fn set_autostart(app: AppHandle, on: bool) -> Result<(), String> {
    use tauri_plugin_autostart::ManagerExt;
    app.state::<AppState>().settings.lock().unwrap().autostart = on;
    if on {
        app.autolaunch()
            .enable()
            .map_err(|e| format!("写自启失败：{e}"))?;
    } else {
        let _ = app.autolaunch().disable();
    }
    {
        let st = app.state::<AppState>();
        let item = st.tray_autostart.lock().unwrap().clone();
        if let Some(item) = item {
            let _ = item.set_checked(on);
        }
    }
    save_settings(&app);
    Ok(())
}

fn now_ms() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as f64)
        .unwrap_or(0.0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn get_boot_does_not_panic_before_startup_state_is_managed() {
        let app = tauri::test::mock_app();
        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            boot_info(app.handle())
        }));

        assert!(matches!(
            result.expect("early control-panel boot must not crash the shell"),
            Err(message) if message == "控制面板正在启动"
        ));
    }
}

/// 开听中音源进程退出监测：进程没了就停止开听（撤字幕窗），
/// 面板黄字等再选，不改成系统混音（PRD User Story 20 / 原型 sourcegone）。
/// 标签页关了但进程还在的场景由下一刀真采音的「还没听到人声」提示兜住。
fn spawn_source_watch(app: AppHandle) {
    std::thread::spawn(move || loop {
        std::thread::sleep(Duration::from_secs(3));
        let phase = current_phase(&app);
        if phase != Phase::Listening {
            continue;
        }
        let source = {
            let st = app.state::<AppState>();
            let guard = st.settings.lock().unwrap();
            guard.source.clone()
        };
        let Some(source) = source else { continue };
        let bound = app
            .state::<AppState>()
            .capture_pid
            .load(std::sync::atomic::Ordering::Relaxed);
        let alive = if bound > 0 {
            sources::pid_exists(bound)
        } else if source == "system" {
            true
        } else {
            // fake 模式 / 未绑 pid 时退回按 exe 名
            sources::process_exists(&source)
        };
        if alive {
            continue;
        }
        let app2 = app.clone();
        tauri::async_runtime::spawn(async move {
            stop_capture(&app2);
            let _ = listen::send(&app2, serde_json::json!({ "type": "stop" })).await;
            set_phase(&app2, Phase::Idle);
            let _ = app2.emit("app://source_gone", ());
        });
    });
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            show_panel(app);
        }))
        .plugin(tauri_plugin_store::Builder::default().build())
        .plugin(tauri_plugin_autostart::init(
            MacosLauncher::LaunchAgent,
            None,
        ))
        .plugin(tauri_plugin_notification::init())
        .setup(|app| {
            // 存档
            let mut settings: Settings = app
                .store("settings.json")
                .ok()
                .and_then(|s| s.get("settings"))
                .and_then(|v| serde_json::from_value(v).ok())
                .unwrap_or_default();
            if std::env::var("FAKE_FIRSTRUN").is_ok() {
                settings.model_ready = false; // 演示第一次打开的下载进度
            }

            // 引擎：FAKE_SCRIPT=… 走假听译回放；默认真听译（采音 + 识别 + 翻译）
            let fake = std::env::var("FAKE_SCRIPT").is_ok();
            let models_dir = app
                .path()
                .app_data_dir()
                .map(|d| d.join("models"))
                .unwrap_or_else(|_| std::env::temp_dir().join("lt-models"));
            let resource_dir = app.path().resource_dir().ok();
            let models_ready_on_disk = !fake && models::all_present(&models_dir);
            if !fake && !models_ready_on_disk {
                settings.model_ready = false;
            }

            // 真听译的进程等模型就绪后再起；假听译立刻起
            let (port, child) = if fake || models_ready_on_disk {
                let (port, child) = listen::spawn_listen(
                    fake,
                    if fake { None } else { Some(&models_dir) },
                    resource_dir.as_deref(),
                );
                (port, child)
            } else {
                (None, None) // 下载完成后在下载任务里 spawn
            };
            let script = std::env::var("FAKE_SCRIPT").unwrap_or_else(|_| "en".into());

            app.manage(AppState {
                settings: Mutex::new(settings.clone()),
                listen: Mutex::new(ListenState {
                    phase: Phase::Idle,
                    port,
                    cmd_tx: listen::none_channel(),
                    script,
                    fake,
                }),
                child: Mutex::new(child),
                tray_toggle: Mutex::new(None),
                tray_autostart: Mutex::new(None),
                models_dir,
                resource_dir,
                capture: Mutex::new(None),
                capture_pid: std::sync::atomic::AtomicU32::new(0),
            });

            // 托盘菜单
            let open_item = MenuItem::with_id(app, "open", "打开控制面板", true, None::<&str>)?;
            let toggle_item = MenuItem::with_id(app, "toggle", "开听", true, None::<&str>)?;
            let autostart_item = CheckMenuItem::with_id(
                app,
                "autostart",
                "开机自启",
                true,
                settings.autostart,
                None::<&str>,
            )?;
            let quit_item = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
            let menu = Menu::with_items(
                app,
                &[
                    &open_item,
                    &toggle_item,
                    &autostart_item,
                    &PredefinedMenuItem::separator(app)?,
                    &quit_item,
                ],
            )?;
            let _tray = TrayIconBuilder::with_id("main")
                .icon(app.default_window_icon().unwrap().clone())
                .tooltip("直播同传工具")
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "open" => show_panel(app),
                    "toggle" => {
                        let app = app.clone();
                        tauri::async_runtime::spawn(async move {
                            if current_phase(&app) == Phase::Listening {
                                do_stop(app.clone()).await;
                            } else {
                                let source = {
                                    let st = app.state::<AppState>();
                                    let guard = st.settings.lock().unwrap();
                                    guard.source.clone()
                                };
                                if let Some(source) = source {
                                    let _ = do_start(app.clone(), source).await;
                                } else {
                                    show_panel(&app);
                                }
                            }
                        });
                    }
                    "autostart" => {
                        let app = app.clone();
                        tauri::async_runtime::spawn(async move {
                            let on = !app.state::<AppState>().settings.lock().unwrap().autostart;
                            let _ = set_autostart(app.clone(), on).await;
                        });
                    }
                    "quit" => app.exit(0),
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let tauri::tray::TrayIconEvent::Click {
                        button: tauri::tray::MouseButton::Left,
                        ..
                    } = event
                    {
                        show_panel(tray.app_handle());
                    }
                })
                .build(app)?;
            app.state::<AppState>()
                .tray_toggle
                .lock()
                .unwrap()
                .replace(toggle_item);
            app.state::<AppState>()
                .tray_autostart
                .lock()
                .unwrap()
                .replace(autostart_item);

            // 窗口摆位：字幕窗用记忆或默认（靠下居中、离底一截）；面板靠右下（像从托盘飞出）
            if let Some(sub) = app.get_webview_window("subtitle") {
                let (mon_w, mon_h, scale) = app
                    .primary_monitor()
                    .ok()
                    .flatten()
                    .map(|m| {
                        (
                            m.size().width as i32,
                            m.size().height as i32,
                            m.scale_factor(),
                        )
                    })
                    .unwrap_or((1920, 1080, 1.0));
                match settings.subtitle_rect {
                    Some(r) => {
                        let _ = sub.set_size(PhysicalSize::new(r.width, r.height));
                        let _ = sub.set_position(PhysicalPosition::new(r.x, r.y));
                    }
                    None => {
                        let w = (mon_w as f64 * 0.44) as i32;
                        let h = (240.0 * scale).round() as i32;
                        let x = (mon_w - w) / 2;
                        let y = mon_h - (96.0 * scale).round() as i32 - h;
                        let _ = sub.set_size(PhysicalSize::new(w as u32, h as u32));
                        let _ = sub.set_position(PhysicalPosition::new(x, y));
                    }
                }
                let _ = sub.set_always_on_top(true);
            }
            if let Some(panel) = app.get_webview_window("panel") {
                let (mon_w, mon_h, scale) = app
                    .primary_monitor()
                    .ok()
                    .flatten()
                    .map(|m| {
                        (
                            m.size().width as i32,
                            m.size().height as i32,
                            m.scale_factor(),
                        )
                    })
                    .unwrap_or((1920, 1080, 1.0));
                let w = 352;
                let visible_h = (mon_h as f64 / scale) as i32;
                let visible_w = (mon_w as f64 / scale) as i32;
                let _ =
                    panel.set_position(PhysicalPosition::new(visible_w - w - 12, visible_h - 640));
            }

            cursor_watch::spawn(app.handle().clone());
            spawn_source_watch(app.handle().clone());

            // 第一次打开：面板先能用，下载进度占开听键位置。
            // 假听译 / FAKE_FIRSTRUN 演示走假进度；真听译下载真模型（ModelScope/HF 镜像优先）。
            let app_handle = app.handle().clone();
            if !settings.model_ready {
                set_phase(&app_handle, Phase::Downloading);
                let demo_tick = fake || std::env::var("FAKE_FIRSTRUN").is_ok();
                tauri::async_runtime::spawn(async move {
                    if demo_tick {
                        let mut pct = 0.0f64;
                        loop {
                            tokio::time::sleep(Duration::from_millis(260)).await;
                            pct += 1.4 + rand::random::<f64>() * 1.4;
                            if pct >= 100.0 {
                                break;
                            }
                            let _ = app_handle.emit("app://download", pct);
                        }
                    } else {
                        let dir = app_handle.state::<AppState>().models_dir.clone();
                        let ah = app_handle.clone();
                        let res = models::download_all(&dir, move |pct| {
                            let _ = ah.emit("app://download", (pct * 100.0).min(99.9));
                        })
                        .await;
                        if let Err(e) = res {
                            set_phase(&app_handle, Phase::Idle);
                            notify(
                                &app_handle,
                                "直播同传工具",
                                &format!("听译模型下载失败：{e}。下次打开会重试。"),
                            );
                            return;
                        }
                        // 模型齐了，起真听译进程
                        let resource_dir = app_handle.state::<AppState>().resource_dir.clone();
                        let (port, child) =
                            listen::spawn_listen(false, Some(&dir), resource_dir.as_deref());
                        if let Some(port) = port {
                            app_handle.state::<AppState>().listen.lock().unwrap().port = Some(port);
                            *app_handle.state::<AppState>().child.lock().unwrap() = child;
                        } else {
                            notify(
                                &app_handle,
                                "直播同传工具",
                                "听译没起来。安装包请重装；开发形态请检查 Python。",
                            );
                        }
                    }
                    {
                        let st = app_handle.state::<AppState>();
                        st.settings.lock().unwrap().model_ready = true;
                    }
                    save_settings(&app_handle);
                    let _ = app_handle.emit("app://download", 100.0f64);
                    notify(
                        &app_handle,
                        "直播同传工具",
                        "听译模型装好了，随时可以开听。",
                    );
                    set_phase(&app_handle, Phase::Idle);
                });
            }

            Ok(())
        })
        .on_window_event(|app, event| match event {
            // 面板 × = 只藏（开听不停）；字幕窗没有 ×（无按钮）
            WindowEvent::CloseRequested { api, .. } if app.label() == "panel" => {
                api.prevent_close();
                let _ = app.hide();
            }
            // 字幕窗位置 / 大小记忆（节流）
            WindowEvent::Moved(pos) if app.label() == "subtitle" => {
                maybe_save_rect(app, Some(*pos), None);
            }
            WindowEvent::Resized(size) if app.label() == "subtitle" => {
                maybe_save_rect(app, None, Some(*size));
            }
            _ => {}
        })
        .invoke_handler(tauri::generate_handler![
            get_boot,
            refresh_sources,
            listen_start,
            listen_stop,
            select_source,
            set_mode,
            set_font,
            set_face,
            set_style,
            set_ink,
            set_edge,
            set_plate,
            set_weight,
            set_listen_way,
            set_autostart,
            get_llm_config,
            set_llm_config,
            test_llm_config,
            list_llm_models,
            probe_llm_thinking
        ])
        .build(tauri::generate_context!())
        .expect("壳起不来")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                stop_capture(app);
                if let Some(child) = app.state::<AppState>().child.lock().unwrap().as_mut() {
                    let _ = child.kill();
                }
            }
        });
}

fn maybe_save_rect(
    win: &tauri::Window,
    pos: Option<PhysicalPosition<i32>>,
    size: Option<PhysicalSize<u32>>,
) {
    use std::sync::OnceLock;
    static LAST_SAVE: OnceLock<Mutex<Instant>> = OnceLock::new();
    let last = LAST_SAVE.get_or_init(|| Mutex::new(Instant::now() - Duration::from_secs(10)));
    let app = win.app_handle().clone();
    let (Ok(cur_pos), Ok(cur_size)) = (win.outer_position(), win.outer_size()) else {
        return;
    };
    let (p, s) = (pos.unwrap_or(cur_pos), size.unwrap_or(cur_size));
    let mut guard = last.lock().unwrap();
    if guard.elapsed() < Duration::from_millis(600) {
        // 拖动过程中只记内存，600ms 后落盘一次
        let st = app.state::<AppState>();
        st.settings.lock().unwrap().subtitle_rect = Some(RectSaved {
            x: p.x,
            y: p.y,
            width: s.width,
            height: s.height,
        });
        return;
    }
    *guard = Instant::now();
    drop(guard);
    let st = app.state::<AppState>();
    st.settings.lock().unwrap().subtitle_rect = Some(RectSaved {
        x: p.x,
        y: p.y,
        width: s.width,
        height: s.height,
    });
    save_settings(&app);
}
