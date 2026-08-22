//! 字幕窗的「字默认穿透，悬停才出手柄」：
//! 后台线程轮询全局光标位置，落在字幕窗矩形里就关穿透并通知前端亮把手；
//! 离开就恢复穿透。手比把门牌摸到哪儿，门就开到哪儿。

use std::time::Duration;

use tauri::{AppHandle, Emitter, Manager};
use windows::Win32::Foundation::POINT;
use windows::Win32::UI::WindowsAndMessaging::GetCursorPos;

pub fn spawn(app: AppHandle) {
    std::thread::spawn(move || {
        let mut inside_last = false;
        loop {
            std::thread::sleep(Duration::from_millis(120));
            let Some(sub) = app.get_webview_window("subtitle") else {
                continue;
            };
            if !sub.is_visible().unwrap_or(false) {
                inside_last = false;
                continue;
            }
            let (Ok(pos), Ok(size)) = (sub.outer_position(), sub.outer_size()) else {
                continue;
            };
            let mut pt = POINT::default();
            if unsafe { GetCursorPos(&mut pt) }.is_err() {
                continue;
            }
            let inside = pt.x >= pos.x
                && pt.x < pos.x + size.width as i32
                && pt.y >= pos.y
                && pt.y < pos.y + size.height as i32;
            if inside != inside_last {
                inside_last = inside;
                let _ = sub.set_ignore_cursor_events(!inside);
                let _ = app.emit("subtitle://hover", inside);
            }
        }
    });
}
