"""打印按配置时区的现在。写条目要填日期、年月时用它，不要自己推算，也不要看系统日期。

用法：python3 now.py [--root <路径>]   （--root 用不上，接受后忽略）
输出一行：现在：YYYY-MM-DD HH:MM（年月 YYYY-MM；时区 …）
时区认不出来时，标准错误里有一行提醒，并改用系统本地时间。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ctx_lib import now, run_main, tz_label  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="打印按配置时区的现在")
    ap.add_argument("--root", help="接受但用不上（和别的脚本保持同一套参数），忽略")
    ap.parse_args()
    n = now()
    off = n.strftime("%z")
    off = f"UTC{off[:3]}:{off[3:]}" if off else "UTC?"
    print(f"现在：{n:%Y-%m-%d %H:%M}（年月 {n:%Y-%m}；时区 {tz_label()}，{off}）")
    return 0


if __name__ == "__main__":
    run_main(main)
