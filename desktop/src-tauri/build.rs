fn main() {
    let runtime = std::path::Path::new("runtime");
    let _ = std::fs::create_dir_all(runtime.join("python"));
    let _ = std::fs::create_dir_all(runtime.join("engine"));
    tauri_build::build()
}
