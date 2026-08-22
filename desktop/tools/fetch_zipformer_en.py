"""下载 sherpa-onnx 英一流式 Zipformer 2023-06-26 int8（压缩包约 310MB）。

实测（.build_mats/onset_zipformer_probe.py）：en 历史朗读两实词 2100ms 媒体时，
慢于 SenseVoice 整段 1718ms，未接入热路径。脚本留给以后换模型再 spike。
落盘：<models>/zipformer-en-online/
"""
from __future__ import annotations

import argparse
import os
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

DEFAULT_MODELS = Path(os.environ.get("APPDATA", "")) / "com.livetranslator.desktop" / "models"
ARCHIVE = "sherpa-onnx-streaming-zipformer-en-2023-06-26.tar.bz2"
URLS = (
    f"https://ghfast.top/https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{ARCHIVE}",
    f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{ARCHIVE}",
)
NEED = (
    "encoder-epoch-99-avg-1-chunk-16-left-128.int8.onnx",
    "decoder-epoch-99-avg-1-chunk-16-left-128.onnx",
    "joiner-epoch-99-avg-1-chunk-16-left-128.int8.onnx",
    "tokens.txt",
)


def fetch(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=120) as resp, open(tmp, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while chunk := resp.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r    {done/1e6:7.1f}/{total/1e6:.1f} MB ({done * 100 // total}%)", end="", flush=True)
    print()
    tmp.replace(dest)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models-dir", type=Path, default=DEFAULT_MODELS)
    args = parser.parse_args()
    dest_dir = args.models_dir / "zipformer-en-online"
    dest_dir.mkdir(parents=True, exist_ok=True)
    if all((dest_dir / name).is_file() and (dest_dir / name).stat().st_size > 100 for name in NEED):
        print(f"已有 {dest_dir}")
        return 0
    archive = dest_dir / ARCHIVE
    ok = False
    for url in URLS:
        try:
            print(f"下载 {url}")
            fetch(url, archive)
            ok = True
            break
        except Exception as exc:
            print(f"    失败：{exc}")
    if not ok:
        return 1
    with tarfile.open(archive, "r:bz2") as tar:
        tar.extractall(path=dest_dir.parent)
    extracted = dest_dir.parent / ARCHIVE.replace(".tar.bz2", "")
    if extracted.is_dir() and extracted != dest_dir:
        for name in NEED:
            src = extracted / name
            if src.is_file():
                src.replace(dest_dir / name)
    archive.unlink(missing_ok=True)
    missing = [n for n in NEED if not (dest_dir / n).is_file()]
    if missing:
        print("缺文件：" + ", ".join(missing), file=sys.stderr)
        return 1
    print(f"完成 → {dest_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
