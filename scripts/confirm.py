"""全部转正：把条目里的「待确认」翻成「已确认」。用户看过、删改完不对的之后用。

用法：
    python3 confirm.py [--root <路径>] [--file 01-我是谁/5-表达.md ...] [--dry-run]
    python3 confirm.py [--root <路径>] --list-inferred      列出所有待确认的「归纳自」条目，写报告「拿不准的」时直接贴

  --file     只转这个文件（相对根目录，可给多个）。不给就转整个上下文。
  --dry-run  只列出每个文件会转几条、是哪几行，不改文件。

认哪些（写法见 `_规范.md`「七、待确认和已确认」）：任何一行里，前面是中文逗号、后面是中文逗号/右括号/行尾的
「待确认」——短条目行尾括号、素材条目「来源：」、样例「——出处：」、卷首末尾那行起草说明都是这种写法。
`> 说明：` 行、`_规范.md`、`_填充报告.md`、`_填充进度.md`、`_已读清单.md`、`99-附录/`、`.git/` 不动；
`03-流水/` 整个文件夹和 `04-项目/*/记录.md` 也不动、不计数（那里是原话，不是条目）。
来源写法不挑：提炼自、归纳自《文件名》、归纳自访谈、带括号备注的出处，只要状态位置是「待确认」都转。
私密条目（行里有「私密」二字的、素材条目「公开：」里有「私密」的）照样转，但打印时不显示内容。
--list-inferred 只读：短条目写条目那一行的 `文件:行号`，素材条目写 `### 标题` 那一行的行号；私密的只给位置。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ctx_lib import (  # noqa: E402
    PENDING_TOKEN_RE, entry_md_files, is_note_line, load_root, normalize_newlines,
    read_text, run_main, write_text,
)


def private_line_numbers(text: str) -> set[int]:
    """属于私密条目的行号：含「私密」二字的行，和「公开：」里有「私密」的素材条目整条（写歪了也算）。"""
    nums: set[int] = set()
    lines = normalize_newlines(text).split("\n")
    for i, l in enumerate(lines, 1):
        if "私密" in l:
            nums.add(i)
    start = None
    for i, l in enumerate(lines + ["### "], 1):  # 末尾补一个哨兵标题，收最后一条
        if l.startswith("### "):
            if start is not None:
                block = lines[start - 1:i - 1]
                if "私密" in block[0] or any(
                        re.match(r"^\s*-\s*公开\s*[:：]", b) and "私密" in b for b in block):
                    nums.update(range(start, i))
            start = i
    return nums


def convert(text: str) -> tuple[str, list[tuple[int, str, str]]]:
    out, changes = [], []
    for no, line in enumerate(normalize_newlines(text).split("\n"), 1):
        if not is_note_line(line) and PENDING_TOKEN_RE.search(line):
            new = PENDING_TOKEN_RE.sub("，已确认", line)
            changes.append((no, line, new))
            line = new
        out.append(line)
    return "\n".join(out), changes


_INFERRED_RE = re.compile(r"归纳自")


def list_inferred(root: Path, targets: list[tuple[str, Path]]) -> int:
    """待确认的「归纳自」条目：文件:行号 + 内容（私密的不显示内容）。"""
    rows = []
    for rel, p in targets:
        text = read_text(p)
        lines = normalize_newlines(text).split("\n")
        privates = private_line_numbers(text)
        heading_no = None
        for no, line in enumerate(lines, 1):
            if line.startswith("### "):
                heading_no = no
            if is_note_line(line) or not PENDING_TOKEN_RE.search(line) or not _INFERRED_RE.search(line):
                continue
            if line.strip().startswith("- 来源：") and heading_no is not None:
                where, shown = heading_no, lines[heading_no - 1][4:].strip()
            else:
                where, shown = no, line.strip()
            if where in privates or no in privates:
                shown = "（私密，不显示内容）"
            rows.append(f"`{rel}:{where}` {shown}")
    if not rows:
        print("没有待确认的「归纳自」条目。")
        return 0
    print(f"待确认的「归纳自」条目 {len(rows)} 条（报告「拿不准的」里一条不落地列出）：")
    for r in rows:
        print(f"- {r}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="把「待确认」转成「已确认」")
    ap.add_argument("--root", help="上下文根目录，不给就读 config.json")
    ap.add_argument("--file", action="append", default=[], help="只转这个文件，相对根目录")
    ap.add_argument("--dry-run", action="store_true", help="只列出，不改")
    ap.add_argument("--list-inferred", action="store_true", help="只读：列出待确认的「归纳自」条目")
    args = ap.parse_args()
    root = load_root(args.root)
    if not root.is_dir():
        print(f"上下文根目录不存在：{root}", file=sys.stderr)
        return 1

    allowed = {p.relative_to(root).as_posix(): p for p in entry_md_files(root)}
    if args.file:
        targets = []
        for f in args.file:
            rel = f.replace("\\", "/").strip().lstrip("./")
            if rel not in allowed:
                print(f"「{f}」不在可转的范围里（不存在，或是规范/报告/进度/附录文件，或是流水、项目记录这类放原话的文件）。", file=sys.stderr)
                return 1
            targets.append((rel, allowed[rel]))
    else:
        targets = sorted(allowed.items())

    if args.list_inferred:
        return list_inferred(root, targets)

    total = 0
    for rel, p in targets:
        text = read_text(p)
        new, changes = convert(text)
        if not changes:
            continue
        total += len(changes)
        privates = private_line_numbers(text)
        print(f"{rel}：{len(changes)} 条")
        for no, old, newl in changes:
            if no in privates:
                print(f"  {no}: （私密，不显示内容）")
            else:
                print(f"  {no}: {old.strip()}")
                print(f"   → {newl.strip()}")
        if not args.dry_run:
            write_text(p, new)
    verb = "会转" if args.dry_run else "已转"
    print(f"\n{verb} {total} 条「待确认」→「已确认」" + ("（--dry-run：没改文件）" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    run_main(main)
