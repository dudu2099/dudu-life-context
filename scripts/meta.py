"""写完条目后更新两处登记：卷首章节状态表、规范末尾标签清单。

用法：
    python3 meta.py [--root <路径>] --chapter 02-素材/观点库.md [--chapter ...] [--tag "#写作" ...]

  --chapter  卷首「# 章节状态」表里这个文件那一行改成「有内容」、最后更新改成当前年月（配置时区）；
             文件里除了说明行还没内容的，写成「空」「—」。表里没有这一行（比如新开的项目）就在表末补一行，
             这是登记，不改任何内容。可以给文件夹（04-项目/新项目/），登记里面每个 .md。
  --tag      `_规范.md`「标签清单」里没有的标签，加一行 `- #标签`；已有的跳过。
只改这两处的行，文件其他内容不动。打印改了什么。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ctx_lib import (  # noqa: E402
    COVER_NAME, SPEC_NAME, content_lines, load_root, now, read_text, run_main, write_text,
)


def expand_chapters(root: Path, chapters: list[str]) -> list[str]:
    """--chapter 可以给文件夹（如 04-项目/新项目/）：展开成里面不以 _ 开头的 .md。"""
    out = []
    for c in chapters:
        c = c.replace("\\", "/").strip().lstrip("./")
        d = root / c
        if d.is_dir():
            out += sorted(f.relative_to(root).as_posix() for f in d.glob("*.md") if not f.name.startswith("_"))
        else:
            out.append(c)
    return list(dict.fromkeys(out))


def update_status(root: Path, chapters: list[str]) -> list[str]:
    p = root / COVER_NAME
    if not p.is_file():
        return [f"缺少 {COVER_NAME}，章节状态没改（先跑 scaffold.py）"]
    lines = read_text(p).split("\n")
    ym = now().strftime("%Y-%m")
    msgs, inside, found, last_row = [], False, set(), None

    def row_for(rel: str) -> tuple[str, str]:
        f = root / rel
        has = f.is_file() and bool(content_lines(read_text(f)))
        return (f"| `{rel}` | 有内容 | {ym} |", f"{rel} → 有内容 {ym}") if has else \
            (f"| `{rel}` | 空 | — |", f"{rel} → 空（文件里除了说明行还没有内容）")

    for i, line in enumerate(lines):
        if line.startswith("# "):
            inside = line[2:].strip() == "章节状态"
            continue
        if not inside or not line.strip().startswith("|"):
            continue
        last_row = i
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        m = re.fullmatch(r"`([^`]+)`", cells[0]) if cells else None
        if m and m.group(1) in chapters:
            lines[i], msg = row_for(m.group(1))
            found.add(m.group(1))
            msgs.append(f"章节状态：{msg}")
    missing = [c for c in chapters if c not in found]
    if missing and last_row is None:
        msgs.append(f"{COVER_NAME} 里找不到「# 章节状态」表，没加：{'、'.join(missing)}")
    elif missing:
        add = []
        for c in missing:
            if not (root / c).is_file():
                msgs.append(f"{c} 这个文件不存在，没登记")
                continue
            row, msg = row_for(c)
            add.append(row)
            msgs.append(f"章节状态：表里原来没有，补了一行（只是登记，不改内容）：{msg}")
        lines[last_row + 1:last_row + 1] = add
    write_text(p, "\n".join(lines))
    return msgs


def add_tags(root: Path, tags: list[str]) -> list[str]:
    p = root / SPEC_NAME
    if not p.is_file():
        return [f"缺少 {SPEC_NAME}，没加标签"]
    lines = read_text(p).split("\n")
    start = next((i for i, l in enumerate(lines) if re.match(r"^## .*标签清单", l)), None)
    if start is None:
        return [f"{SPEC_NAME} 里找不到「标签清单」一节，没加标签"]
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    existing = {l.strip()[2:].strip() for l in lines[start + 1:end] if l.strip().startswith("- #")}
    new = []
    for t in tags:
        t = t.strip()
        if not t.startswith("#"):
            t = "#" + t
        if t not in existing and t not in new:
            new.append(t)
    if not new:
        return ["标签清单：没有新标签"]
    last = end - 1
    while last > start and lines[last].strip() == "":
        last -= 1
    has_items = any(l.strip().startswith("- #") for l in lines[start + 1:end])
    block = [f"- {t}" for t in new]
    lines[last + 1:last + 1] = block if has_items else ["", *block]
    write_text(p, "\n".join(lines))
    return [f"标签清单：新增 {' '.join(new)}"]


def main() -> int:
    ap = argparse.ArgumentParser(description="更新卷首章节状态表和标签清单")
    ap.add_argument("--root", help="上下文根目录，不给就读 config.json")
    ap.add_argument("--chapter", action="append", default=[], help="写了内容的章节文件，相对根目录")
    ap.add_argument("--tag", action="append", default=[], help="这次用到的标签")
    args = ap.parse_args()
    root = load_root(args.root)
    msgs = []
    if args.chapter:
        msgs += update_status(root, expand_chapters(root, args.chapter))
    if args.tag:
        msgs += add_tags(root, args.tag)
    for m in msgs or ["什么也没改"]:
        print(m)
    return 0


if __name__ == "__main__":
    run_main(main)
