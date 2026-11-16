"""写入后做一次本地 git 提交（不推送）。

用法：
    python3 commit.py [--root <路径>] -m "填充后：写入 N 条，待确认"

  - 上下文根目录自己没有 .git（或本机没装 git）就跳过，退出码 0。
  - 上下文文件夹在别人更大的 git 仓库里（自己不是仓库顶层）：一律不提交，打印一句说明，退出码 0。
  - 只添加、只提交上下文文件夹自己的文件（模板里的顶层文件和文件夹、99-附录、报告、已读清单），
    不用 git add -A；备份文件夹和进度文件永远不进提交；用户自己暂存的别的改动不会被捎进来。没有改动就跳过。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ctx_lib import git_commit, load_root, run_main  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="上下文写入后本地 git 提交")
    ap.add_argument("--root", help="上下文根目录，不给就读 config.json")
    ap.add_argument("-m", "--message", required=True, help="提交信息")
    args = ap.parse_args()
    status, msg = git_commit(load_root(args.root), args.message)
    if status == "error":
        print(msg, file=sys.stderr)
        return 1
    print(msg)
    return 0


if __name__ == "__main__":
    run_main(main)
