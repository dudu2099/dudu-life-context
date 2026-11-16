"""生成本技能的 config.json：上下文文件夹放哪、用哪个时区。

用法：
    python3 setup.py --root "<上下文文件夹>" [--timezone Asia/Shanghai] [--force]

  --root      上下文文件夹位置。写成绝对路径存进去；文件夹不存在也没关系，scaffold 会建。
  --timezone  IANA 时区名（如 Asia/Shanghai、America/New_York），或 UTC+8 这种固定偏移；
              不给就留空，用系统本地时间。
  --force     config.json 已存在时覆盖（先问过用户再加）。

config.json 只在本机，不进仓库（.gitignore 已忽略）。环境变量 LIFE_CONTEXT_CONFIG 可以指向别的位置。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ctx_lib import config_path, resolve_timezone, run_main, write_text  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 config.json")
    ap.add_argument("--root", required=True, help="上下文文件夹位置")
    ap.add_argument("--timezone", default="", help="IANA 时区名或 UTC+8；留空用系统本地时间")
    ap.add_argument("--force", action="store_true", help="已存在时覆盖")
    args = ap.parse_args()

    p = config_path()
    if p.exists() and not args.force:
        print(f"config.json 已存在，没改：{p}\n内容：{p.read_text(encoding='utf-8-sig').strip()}\n"
              "要换位置或时区，问过用户后加 --force 重跑。", file=sys.stderr)
        return 1
    root = Path(args.root).expanduser().resolve()
    _, label, warn = resolve_timezone(args.timezone)
    if warn:
        print(warn, file=sys.stderr)
        print("config.json 没写。换一个时区写法再跑，或者不给 --timezone。", file=sys.stderr)
        return 1
    cfg = {"context_root": str(root), "timezone": args.timezone.strip()}
    write_text(p, json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
    print(f"已写 {p}")
    print(f"  上下文文件夹：{root}")
    print(f"  时区：{label}")
    return 0


if __name__ == "__main__":
    run_main(main)
