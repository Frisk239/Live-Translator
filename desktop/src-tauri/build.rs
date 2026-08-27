fn main() {
    let runtime = std::path::Path::new("runtime");
    let _ = std::fs::create_dir_all(runtime.join("python"));
    let _ = std::fs::create_dir_all(runtime.join("engine"));
    // CI 干净 checkout 没有 runtime/listen（本地是 prepare_runtime.py 生成的），
    // tauri resources 校验会卡 cargo test——建个空目录放行，正式打包前脚本自会填满
    let _ = std::fs::create_dir_all(runtime.join("listen"));
    tauri_build::build()
}
