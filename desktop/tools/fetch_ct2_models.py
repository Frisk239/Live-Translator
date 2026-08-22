"""下载 OPUS-MT 的 CTranslate2 int8 转换（jiangzhuo9357 系列，三对齐全）。

CT2 用与 ONNX 相同的权重做 int8 推理，单句延迟比 Xenova ONNX 每步全量重算
低一个量级（Argos Translate 生产同款）。装到 <models>/<pair>-ct2/：
Translator 检测到该目录即走 CT2，否则回退现有 ONNX 路径。

直连 HF 失败自动走 hf-mirror（与本仓其它下载一致）。
用法：python tools/fetch_ct2_models.py [--models-dir <dir>] [--only en-zh,ja-en,ko-en]
"""
from __future__ import annotations

import argparse
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODELS = Path(os.environ.get("APPDATA", "")) / "com.livetranslator.desktop" / "models"

REPOS = {
    "opus-en-zh": "jiangzhuo9357/opus-mt-en-zh-ct2",
    "opus-ja-en": "jiangzhuo9357/opus-mt-ja-en-ct2",
    "opus-ko-en": "jiangzhuo9357/opus-mt-ko-en-ct2",
}
FILES = ("config.json", "model.bin", "shared_vocabulary.json")
HOSTS = ("https://huggingface.co", "https://hf-mirror.com")


def fetch(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=60) as resp, open(tmp, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while chunk := resp.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            if total:
                pct = done * 100 // total
                print(f"\r    {done/1e6:7.1f}/{total/1e6:.1f} MB ({pct}%)", end="", flush=True)
    print()
    tmp.replace(dest)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models-dir", type=Path, default=DEFAULT_MODELS)
    parser.add_argument("--only", default="en-zh,ja-en,ko-en", help="逗号分隔的方向后缀")
    args = parser.parse_args()
    wanted = [f"opus-{s.strip()}" for s in args.only.split(",") if s.strip()]

    for pair in wanted:
        repo = REPOS.get(pair)
        if repo is None:
            print(f"未知方向：{pair}", file=sys.stderr)
            return 2
        dest_dir = args.models_dir / f"{pair}-ct2"
        dest_dir.mkdir(parents=True, exist_ok=True)
        for name in FILES:
            dest = dest_dir / name
            if dest.is_file() and dest.stat().st_size > 100:
                print(f"[{pair}] 已有 {name}")
                continue
            ok = False
            for host in HOSTS:
                url = f"{host}/{repo}/resolve/main/{name}"
                try:
                    print(f"[{pair}] {name} ← {host}")
                    fetch(url, dest)
                    ok = True
                    break
                except Exception as exc:  # noqa: BLE001
                    print(f"    失败：{exc}")
            if not ok:
                print(f"[{pair}] {name} 两个源都失败", file=sys.stderr)
                return 1
        print(f"[{pair}] 完成 → {dest_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
