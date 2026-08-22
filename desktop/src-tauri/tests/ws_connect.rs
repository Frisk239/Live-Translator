//! 必红复现环：壳连不上引擎 WS（上一窗钉死的卡点）。
//! 用与壳 ensure_connected 完全相同的调用（tokio_tungstenite::connect_async）
//! 在裸 tokio 环境里连一个已 READY 的引擎，发 start、等它被引擎收下。
//! 红 = tokio-tungstenite / 本机环境问题；绿 = 问题在 tauri 上下文。

use std::io::BufRead;
use std::process::{Command, Stdio};

fn models_dir() -> std::path::PathBuf {
    std::path::PathBuf::from(std::env::var("APPDATA").expect("APPDATA"))
        .join("com.livetranslator.desktop")
        .join("models")
}

fn spawn_engine_ready() -> (u16, std::process::Child) {
    #[cfg(windows)]
    let mut cmd = {
        use std::os::windows::process::CommandExt;
        let mut c = Command::new("python");
        c.creation_flags(0x0800_0000);
        c
    };
    #[cfg(not(windows))]
    let mut cmd = Command::new("python");
    let script = concat!(env!("CARGO_MANIFEST_DIR"), "/../engine/real_listen.py");
    let mut child = cmd
        .arg(script)
        .arg("--port")
        .arg("0")
        .arg("--models-dir")
        .arg(models_dir())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .expect("spawn engine");
    let stdout = child.stdout.take().expect("stdout");
    for line in std::io::BufReader::new(stdout).lines() {
        let line = line.expect("read line");
        if let Some(rest) = line.strip_prefix("READY ") {
            let port: u16 = rest.trim().parse().expect("parse port");
            return (port, child);
        }
    }
    panic!("engine never said READY");
}

#[tokio::test(flavor = "multi_thread")]
async fn connect_and_start_like_shell() {
    let (port, mut child) = spawn_engine_ready();
    eprintln!("engine READY at {port}");

    // 与壳 ensure_connected 相同的调用
    let fut = tokio_tungstenite::connect_async(format!("ws://127.0.0.1:{port}"));
    let (ws, _resp) = tokio::time::timeout(std::time::Duration::from_secs(5), fut)
        .await
        .expect("connect within 5s")
        .expect("connect ok");
    eprintln!("connected");

    let (mut write, mut read) = ws.split();
    use futures_util::{SinkExt, StreamExt};
    write
        .send(tokio_tungstenite::tungstenite::Message::Text(
            r#"{"type":"start","source":"probe.exe"}"#.into(),
        ))
        .await
        .expect("send start");
    eprintln!("start sent");

    // 引擎对无音频的 start 不回事件；能安静地等 1 秒不挂即算通
    let _ = tokio::time::timeout(std::time::Duration::from_secs(1), read.next()).await;
    let _ = write.close().await;
    let _ = child.kill();
}
