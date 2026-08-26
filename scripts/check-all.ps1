# 一键全量检查：四套测试 + 两处静态检查，逐段报告，最后汇总。
# 用法：powershell -ExecutionPolicy Bypass -File scripts/check-all.ps1
# 缝测试（vitest 里真听译用例）需 PYTHON 指向装了 sherpa-onnx 的 Python 3.12，缺模型自动跳过。
# cargo 用独立 target-test 目录：真机占用主 exe 时 tauri dev 与 cargo test 抢锁（见仓库根 .gitignore）。

$ErrorActionPreference = "Continue"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location (Join-Path $repo "desktop")

$env:PYTHON = "C:\Users\a2691\AppData\Local\Programs\Python\Python312\python.exe"

$results = [ordered]@{}

function Run-Step($name, $scriptBlock) {
    Write-Host "`n=== $name ===" -ForegroundColor Cyan
    & $scriptBlock 2>&1 | Tee-Object -Variable out | Select-Object -Last 3
    $script:results[$name] = if ($LASTEXITCODE -eq 0 -or $null -eq $LASTEXITCODE) { "PASS" } else { "FAIL($LASTEXITCODE)" }
}

Run-Step "py_compile 探针与引擎" { python -m py_compile tools/quality_probe.py engine/real_listen.py }
Run-Step "listen 包 pytest（含切条）" { python -m pytest ..\listen\tests -q }
Run-Step "desktop sidecar pytest" { python -m pytest engine\tests -q }
Run-Step "server pytest" { python -m pytest ..\server\tests -q }
Run-Step "tsc typecheck" { pnpm typecheck }
Run-Step "vitest（含缝测试）" { pnpm test }
Run-Step "cargo test（target-test 隔离）" { $env:CARGO_TARGET_DIR = "$repo\desktop\src-tauri\target-test"; cargo test --manifest-path src-tauri\Cargo.toml }

Write-Host "`n=== 汇总 ===" -ForegroundColor Cyan
$failed = 0
foreach ($k in $results.Keys) {
    $color = if ($results[$k] -eq "PASS") { "Green" } else { $failed++; "Red" }
    Write-Host ("{0,-28} {1}" -f $k, $results[$k]) -ForegroundColor $color
}
exit $failed
