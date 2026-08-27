# 一键全量检查：五套测试 + 两处静态检查，逐段报告，最后汇总。
# 用法：powershell -ExecutionPolicy Bypass -File scripts/check-all.ps1
# 两套 Python 各司其职（本机实况，勿混）：
#   - pytest / py_compile 跑在 PATH 的 python 上（装 fastapi 等）；换机用 LIVE_TRANSLATOR_PYTHON 覆盖。
#   - 缝测试 spawn 的引擎进程走 $env:PYTHON（装 sherpa-onnx 的 3.12）；换机用 LIVE_TRANSLATOR_SEAM_PYTHON 覆盖。
# android 单测需 JAVA_HOME 指 JDK 17+；本机全局 JAVA_HOME 是 JDK 8（gradle 必挂），
# 所以无条件指到 21，要换机器用 LIVE_TRANSLATOR_JAVA_HOME 覆盖。
# cargo 用独立 target-test 目录：真机占用主 exe 时 tauri dev 与 cargo test 抢锁（见仓库根 .gitignore）；
# 并在 src-tauri 目录里跑，让本机 .cargo/config.toml（E 盘 MSVC linker）生效。

$ErrorActionPreference = "Continue"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location (Join-Path $repo "desktop")

$Python = if ($env:LIVE_TRANSLATOR_PYTHON) { $env:LIVE_TRANSLATOR_PYTHON } else { "python" }
$env:PYTHON = if ($env:LIVE_TRANSLATOR_SEAM_PYTHON) { $env:LIVE_TRANSLATOR_SEAM_PYTHON } else { "C:\Users\a2691\AppData\Local\Programs\Python\Python312\python.exe" }
if (-not $env:LIVE_TRANSLATOR_JAVA_HOME) { $env:JAVA_HOME = "C:\Program Files\Java\jdk-21.0.10" } else { $env:JAVA_HOME = $env:LIVE_TRANSLATOR_JAVA_HOME }

$results = [ordered]@{}

function Run-Step($name, $scriptBlock) {
    Write-Host "`n=== $name ===" -ForegroundColor Cyan
    & $scriptBlock 2>&1 | Tee-Object -Variable out | Select-Object -Last 3
    $script:results[$name] = if ($LASTEXITCODE -eq 0 -or $null -eq $LASTEXITCODE) { "PASS" } else { "FAIL($LASTEXITCODE)" }
}

Run-Step "py_compile 探针与引擎" { & $Python -m py_compile tools/quality_probe.py engine/real_listen.py }
Run-Step "listen 包 pytest（含切条）" { & $Python -m pytest ..\listen\tests -q }
Run-Step "desktop sidecar pytest" { & $Python -m pytest engine\tests -q }
Run-Step "server pytest" { & $Python -m pytest ..\server\tests -q }
Run-Step "tsc typecheck" { pnpm typecheck }
Run-Step "vitest（含缝测试）" { pnpm test }
Run-Step "cargo test（target-test 隔离）" {
    $env:CARGO_TARGET_DIR = "$repo\desktop\src-tauri\target-test"
    Push-Location (Join-Path $repo "desktop\src-tauri")
    try { cargo test } finally { Pop-Location }
}
Run-Step "android 单测（gradle）" {
    Push-Location (Join-Path $repo "android")
    try { .\gradlew.bat testDebugUnitTest --console=plain } finally { Pop-Location }
}

Write-Host "`n=== 汇总 ===" -ForegroundColor Cyan
$failed = 0
foreach ($k in $results.Keys) {
    $color = if ($results[$k] -eq "PASS") { "Green" } else { $failed++; "Red" }
    Write-Host ("{0,-28} {1}" -f $k, $results[$k]) -ForegroundColor $color
}
exit $failed
