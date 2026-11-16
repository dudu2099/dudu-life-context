"""撤回一次填充：把上下文文件夹恢复成某份「填充前」备份的样子。撤回本身也能撤回。

用法：
    python3 undo.py [--root <路径>] [--dry-run]            恢复到最近一次填充前（见下面「选哪份备份」）
    python3 undo.py [--root <路径>] --list                 列出所有备份（名字、时间、文件数）
    python3 undo.py [--root <路径>] --backup <备份文件夹名> [--dry-run]   恢复到指定的那份
    python3 undo.py [--root <路径>] --delete-backup <备份文件夹名> [--yes]  删掉一份备份
                                                           （不带 --yes 只打印会删什么，带了才真删）

选哪份备份（不给 --backup 时）：
  - 备份的先后按清单文件里记的 UTC 时间排（不受时区设置改动影响）；老备份没有这个时间，退回按文件夹名。
  - `_填充进度.md` 显示有一次填充「进行中」：就用它记的那份。那份不在了（被删了），不退到更早的备份
    （那会连上一次填充一起撤掉），直接说明没有可恢复到这次填充前的备份，退出码 1。
  - 否则取最新的「-填充前」备份。
  - 连续撤回不悄悄越过上一次：上一次撤回已经恢复到某份「填充前」备份，而默认选出来的不比它新，
    就拒绝（退出码 1）并说明。真要再往前撤，用 --list 看，再 --backup <名字> 明确指定。
    上一次是「反悔」（--backup 恢复到一份「撤回前」备份）的，这条保护重置，不拦。

做的事：
  1. 先把现在的样子另存一份备份（标签「撤回前」），所以撤错了还能用 --backup 换回来。
  2. 备份里有的文件，按备份内容写回（现在的版本被盖掉）；现在有、备份时没有的文件（备份之后才出现的文件，
     包括用户自己后来新建的），删掉。删掉和盖掉的东西都在第 1 步那份备份里，不会真丢。
  3. 是 git 仓库的，提交一次「撤回填充」（只提交上下文文件夹自己的文件）。
  4. 记一行撤回记录（99-附录/_填充前备份/_撤回记录.md），下次连续撤回时用。
  .git 和 99-附录/_填充前备份/ 本身不动。--dry-run 只列出会改什么，不动文件：
  「会删掉」「会写回旧版（盖掉现在的版本）」「会补回来」分开列，用户原话区（03-流水/、04-项目/*/记录.md）的标出来。
"""
from __future__ import annotations

import argparse
import filecmp
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ctx_lib import (  # noqa: E402
    BACKUP_DIR, PY, append_undo_log, backup_sort_key, backup_time, git_commit, is_git_repo,
    is_raw_record, list_backups, load_root, make_backup, pre_fill_backups, read_manifest,
    read_undo_log, run_main, snapshot_files,
)
from progress import load as load_progress  # noqa: E402

RAW_MARK = "【你的原话区】"


def by_name(root: Path, name: str) -> Path | None:
    key = name.strip().strip("/").replace("\\", "/").split("/")[-1]
    if not key:
        return None
    for b in list_backups(root):
        if b.name == key:
            return b
    return None


def default_backup(root: Path) -> tuple[Path | None, str]:
    """返回 (备份, 为什么选它)。备份为 None 时第二项是给用户的说明（可能为空）。"""
    pre = pre_fill_backups(root)
    latest = pre[-1] if pre else None
    prog = load_progress(root)
    if prog and prog["fields"].get("填充前备份"):
        named = prog["fields"]["填充前备份"].rstrip("/")
        p = root / named
        valid = p.is_dir() and (p / "_清单.txt").is_file()
        if prog["fields"].get("状态") == "进行中":
            if valid:
                return p, "进度文件显示有一次填充正在进行，用它开工前的那份"
            return None, (f"进度文件显示有一次填充正在进行，它开工前的备份 {named}/ 不在了（被删了），"
                          "没有可恢复到这次填充前的备份。更早的备份会连以前的填充一起撤掉，所以默认不用。"
                          f"真要退到更早的，先 {PY} undo.py --list 看，和用户说清楚，再 --backup <名字>。")
        if valid and latest is not None and p.resolve() == latest.resolve():
            return p, "最新一份填充前备份（也是进度文件里记的那份）"
    if latest is None:
        return None, ""
    return latest, "最新一份填充前备份"


def plan(root: Path, backup: Path) -> tuple[list[str], list[str], list[str], list[str]]:
    """返回 (会删掉, 会写回旧版——现在的版本被盖掉, 会补回来——现在没有, 没变)。"""
    manifest = read_manifest(backup)
    keep = set(manifest)
    current = {p.relative_to(root).as_posix() for p in snapshot_files(root)}
    # 根目录的 .gitignore / .gitattributes 是骨架设施，不算这次填充写的，不删
    delete = sorted(r for r in current - keep if not (r.startswith(".") and "/" not in r))
    overwrite, readd, unchanged = [], [], []
    for rel in manifest:
        src, dst = backup / rel, root / rel
        if not dst.is_file():
            readd.append(rel)
        elif filecmp.cmp(src, dst, shallow=False):
            unchanged.append(rel)
        else:
            overwrite.append(rel)
    return delete, overwrite, readd, unchanged


def mark(rel: str) -> str:
    return f"  {RAW_MARK}" if is_raw_record(rel) else ""


def dir_size(p: Path) -> tuple[int, int]:
    files = [f for f in p.rglob("*") if f.is_file()]
    return len(files), sum(f.stat().st_size for f in files)


def delete_backup(root: Path, name: str, yes: bool) -> int:
    b = by_name(root, name)
    if b is None:
        print(f"找不到备份「{name}」。用 --list 看 {BACKUP_DIR}/ 里有哪些。", file=sys.stderr)
        return 1
    n, size = dir_size(b)
    rel = b.relative_to(root).as_posix()
    print(f"{'会删掉' if not yes else '删掉'}：{rel}/（{backup_time(b)}，{n} 个文件，约 {size / 1024:.1f} KB）")
    prog = load_progress(root)
    if prog and prog["fields"].get("状态") == "进行中" and \
            prog["fields"].get("填充前备份", "").rstrip("/") == rel:
        print("注意：这是正在进行的这次填充开工前的备份，删了就没法撤回这次填充。")
    if not yes:
        print("（没加 --yes：什么都没删。删了不能恢复，确认要删再加 --yes。）")
        return 0
    shutil.rmtree(b)
    print("已删。" + ("用了 git 的话，git 历史里可能还有这些内容，要用户自己处理。" if is_git_repo(root) else ""))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="撤回一次填充")
    ap.add_argument("--root", help="上下文根目录，不给就读 config.json")
    ap.add_argument("--list", action="store_true", help="列出所有备份")
    ap.add_argument("--backup", default=None, help="恢复到指定的备份文件夹名")
    ap.add_argument("--delete-backup", metavar="名字", default=None, help="删掉一份备份（要加 --yes 才真删）")
    ap.add_argument("--yes", action="store_true", help="和 --delete-backup 一起用：真的删")
    ap.add_argument("--dry-run", action="store_true", help="只列出会改什么")
    args = ap.parse_args()
    root = load_root(args.root)

    if args.list:
        bs = list_backups(root)
        if not bs:
            print("没有任何备份。")
        for b in bs:
            print(f"  {b.name}（{backup_time(b)}，{len(read_manifest(b))} 个文件）")
        log = read_undo_log(root)
        if log:
            print(f"上一次撤回：{log[-1]['time']} 恢复到 {log[-1]['restored']}")
        return 0

    if args.delete_backup is not None:
        if not args.delete_backup.strip():
            print("--delete-backup 要给备份文件夹名（--list 看有哪些），不能是空的。", file=sys.stderr)
            return 2
        return delete_backup(root, args.delete_backup, args.yes)

    if args.backup is not None:
        if not args.backup.strip():
            print("--backup 要给备份文件夹名（--list 看有哪些），不能是空的。", file=sys.stderr)
            return 2
        backup = by_name(root, args.backup)
        why = "按 --backup 指定"
        if backup is None:
            print(f"找不到备份「{args.backup}」。用 --list 看 {BACKUP_DIR}/ 里有哪些。", file=sys.stderr)
            return 1
    else:
        backup, why = default_backup(root)
        if backup is None:
            print(why or f"找不到可用的填充前备份。用 --list 看 {BACKUP_DIR}/ 里有哪些。", file=sys.stderr)
            return 1
        log = read_undo_log(root)
        last = log[-1] if log else None
        # 上一次是「反悔」（恢复到一份撤回前备份）：保护重置，不拦
        if last and "-撤回前" not in last["restored"]:
            last_b = by_name(root, last["restored"])
            last_key = backup_sort_key(last_b) if last_b else backup_sort_key(Path(last["restored"]))
            if backup_sort_key(backup) <= last_key:
                print(f"上一次撤回（{last['time']}）已经恢复到 {last['restored']}"
                      f"（{backup_time(last_b) if last_b else backup_time(Path(last['restored']))}）。"
                      f"默认选出来的是 {backup.name}，不比它新，再撤就会越过上一次撤回，所以默认不做。", file=sys.stderr)
                print(f"真要再往前撤：先 {PY} undo.py --list 看有哪些备份、各是什么时间，"
                      f"和用户说清楚恢复到哪一份，再用 --backup <名字> 明确指定。", file=sys.stderr)
                if by_name(root, last["safety"]):
                    print("想撤销上一次撤回（回到撤回前的样子）：--backup " + last["safety"], file=sys.stderr)
                else:
                    print(f"上一次撤回前的样子存在 {last['safety']}，那份备份已经删了，换不回去了。", file=sys.stderr)
                return 1

    delete, overwrite, readd, unchanged = plan(root, backup)
    print(f"恢复到：{backup.relative_to(root).as_posix()}/（备份时间 {backup_time(backup)}；{why}）")
    print(f"会删掉 {len(delete)} 个（备份之后才出现的文件，包括你自己后来新建的）：")
    for r in delete:
        print(f"  - {r}{mark(r)}")
    print(f"会写回旧版 {len(overwrite)} 个（备份之后被改过；现在的版本会被盖掉，包括你自己后来的改动）：")
    for r in overwrite:
        print(f"  ~ {r}{mark(r)}")
    print(f"会补回来 {len(readd)} 个（备份之后被删了的）：")
    for r in readd:
        print(f"  + {r}{mark(r)}")
    print(f"没变 {len(unchanged)} 个。")
    if any(is_raw_record(r) for r in delete + overwrite):
        print(f"标了{RAW_MARK}的是 03-流水/ 或 04-项目/*/记录.md：那是你自己记的原话，不是这次填充写的，撤回前请你确认。")
    if args.dry_run:
        print("（--dry-run：什么都没动。「会删掉」和「会写回旧版」两张清单都要原样告诉用户再撤。）")
        return 0
    if not delete and not overwrite and not readd:
        print("现在和备份一样，不用撤回。")
        return 0

    safety = make_backup(root, "撤回前")
    for rel in delete:
        (root / rel).unlink()
    for rel in overwrite + readd:
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup / rel, dst)
    # 删文件后留下的空文件夹（备份时不存在的）清掉
    kept_dirs = {str(Path(r).parent.as_posix()) for r in read_manifest(backup)}
    for rel in delete:
        d = (root / rel).parent
        while d != root and d.is_dir() and not any(d.iterdir()) and \
                d.relative_to(root).as_posix() not in kept_dirs and d.name != "99-附录":
            d.rmdir()
            d = d.parent
    append_undo_log(root, backup.name, safety.name)
    status, msg = git_commit(root, f"撤回填充：恢复到 {backup.name}")
    srel = safety.relative_to(root).as_posix()
    print(f"已撤回到 {backup.name}（{backup_time(backup)}）。")
    print(f"撤回前的样子存在了 {srel}/，里面仍然有这批内容（包括刚删掉、盖掉的文件）。想反悔就 --backup {safety.name}。")
    print(f"要彻底删掉这批内容：{PY} undo.py --delete-backup {safety.name} --yes")
    print(f"git：{msg}")
    if status in ("ok", "nothing"):
        print("用了 git：这批内容在 git 历史里还有，要彻底清掉需要用户自己处理 git 历史。")
    return 0


if __name__ == "__main__":
    run_main(main)
