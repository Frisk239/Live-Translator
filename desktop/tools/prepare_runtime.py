"""准备安装包用的内置 Python：观众不用装 Python、不用 pip.

`pnpm build` / tauri beforeBuildCommand 会跑这个脚本。
模型不打进包，只带解释器与听译依赖（含 ctranslate2）。
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQ = ROOT / "engine" / "requirements.txt"
ENGINE_SRC = ROOT / "engine" / "real_listen.py"
LISTEN_SRC = ROOT.parent / "listen"
RUNTIME = ROOT / "src-tauri" / "runtime"
OUT = RUNTIME / "python"
ENGINE_OUT = RUNTIME / "engine"
LISTEN_OUT = RUNTIME / "listen"
PY_VER = "3.12.10"
ZIP_NAME = f"python-{PY_VER}-embed-amd64.zip"
PY_URLS = (
    f"https://mirrors.huaweicloud.com/python/{PY_VER}/{ZIP_NAME}",
    f"https://www.python.org/ftp/python/{PY_VER}/{ZIP_NAME}",
)
PIP_INDEXES = (
    "https://pypi.tuna.tsinghua.edu.cn/simple",
    "https://pypi.org/simple",
)


def stamp() -> str:
    raw = PY_VER.encode() + b"\n" + REQ.read_bytes()
    return hashlib.sha256(raw).hexdigest()[:16]


def ready() -> bool:
    marker = OUT / ".bundle-stamp"
    return (OUT / "python.exe").is_file() and marker.is_file() and marker.read_text(encoding="ascii") == stamp()


def download(urls: tuple[str, ...], dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    last_err: Exception | None = None
    for url in urls:
        try:
            print(f"下载 {url}")
            with urllib.request.urlopen(url, timeout=60) as resp, open(dest, "wb") as out:
                shutil.copyfileobj(resp, out)
            if dest.stat().st_size > 1000:
                return
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            print(f"  失败：{exc}")
    raise RuntimeError(f"下载失败：{last_err}")


def patch_pth() -> None:
    matches = list(OUT.glob("python*._pth"))
    if not matches:
        raise RuntimeError("内置 Python 缺少 ._pth")
    matches[0].write_text("python312.zip\n.\nLib\\site-packages\nimport site\n", encoding="utf-8")


def pip_install() -> None:
    site = OUT / "Lib" / "site-packages"
    site.mkdir(parents=True, exist_ok=True)
    last_err: Exception | None = None
    for index in PIP_INDEXES:
        try:
            print(f"pip install --target {site}  ← {index}")
            env = dict(**os.environ)
            env["PYTHONNOUSERSITE"] = "1"
            subprocess.check_call(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--isolated",
                    "--upgrade",
                    "--no-warn-script-location",
                    "--python-version",
                    "312",
                    "--only-binary",
                    ":all:",
                    "--platform",
                    "win_amd64",
                    "--implementation",
                    "cp",
                    "--abi",
                    "cp312",
                    "--target",
                    str(site),
                    "-r",
                    str(REQ),
                    "-i",
                    index,
                ],
                cwd=str(ROOT),
                env=env,
            )
            return
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            print(f"  失败：{exc}")
    raise RuntimeError(f"pip 安装失败：{last_err}")


def sync_engine() -> None:
    ENGINE_OUT.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ENGINE_SRC, ENGINE_OUT / "real_listen.py")
    if LISTEN_OUT.exists():
        shutil.rmtree(LISTEN_OUT)
    shutil.copytree(
        LISTEN_SRC,
        LISTEN_OUT,
        ignore=shutil.ignore_patterns("__pycache__", "tests", "*.pyc"),
    )


def main() -> int:
    if sys.platform != "win32":
        print("本产品只打 Windows 包，跳过内置 Python。")
        return 0
    sync_engine()
    if ready():
        print(f"内置 Python 已就绪：{OUT}")
        return 0

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True, exist_ok=True)

    cache = RUNTIME / "cache" / ZIP_NAME
    if not cache.is_file() or cache.stat().st_size < 1_000_000:
        download(PY_URLS, cache)
    print(f"解压 {cache} → {OUT}")
    with zipfile.ZipFile(cache) as zf:
        zf.extractall(OUT)
    patch_pth()
    pip_install()

    (OUT / ".bundle-stamp").write_text(stamp(), encoding="ascii")
    print(f"内置 Python 准备完成：{OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
