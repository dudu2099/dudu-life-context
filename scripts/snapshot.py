"""填充前做一次备份（撤回就靠它），是 git 仓库的话顺手提交一次。

用法：
    python3 snapshot.py [--root <路径>] [--label 填充前]

  把整个上下文文件夹（除 .git 和备份文件夹本身）复制到
  `99-附录/_填充前备份/<时间>-<label>/`，并写一份 `_清单.txt`（第一行记 UTC 备份时间，排先后用）。
  上下文文件夹自己是 git 仓库的，先提交一次「<label>快照」（只提交上下文文件夹自己的文件）；
  它在别人更大的仓库里的，不提交。
  label 是「填充前」时，备份之后把已有的 `_填充报告.md` 挪到 `99-附录/_历次报告/<时间>.md`，
  给这次的新报告腾地方（撤回时它会被挪回原处）。
  打印备份位置（相对根目录），progress.py init 已经替你调过它，方式一不用单独跑；方式二写入前跑。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ctx_lib import archive_report, git_commit, load_root, make_backup, read_manifest, run_main  # noqa: E402


def do_snapshot(root: Path, label: str) -> tuple[Path, str]:
    status, msg = git_commit(root, f"{label}快照")
    if status == "error":
        msg = "git 提交失败（备份照做）：" + msg
    dest = make_backup(root, label)
    if label == "填充前":
        moved = archive_report(root)
        if moved:
            print(f"上一次的填充报告已挪到 {moved}（这次的新报告写回 _填充报告.md）")
    return dest, msg


def main() -> int:
    ap = argparse.ArgumentParser(description="填充前备份")
    ap.add_argument("--root", help="上下文根目录，不给就读 config.json")
    ap.add_argument("--label", default="填充前", help="备份名里的标签")
    args = ap.parse_args()
    root = load_root(args.root)
    if not root.is_dir():
        print(f"上下文根目录不存在：{root}（先跑 scaffold.py）", file=sys.stderr)
        return 1
    dest, msg = do_snapshot(root, args.label)
    n = len(read_manifest(dest))
    print(f"已备份 {n} 个文件 → {dest.relative_to(root).as_posix()}/")
    print(f"git：{msg}")
    return 0


if __name__ == "__main__":
    run_main(main)
