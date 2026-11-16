"""导出：把卷首「一页纸的我」加上点名的章节，合成一段能直接粘给别的 AI 的文本。只读。

用法：
    python3 export.py [--root <路径>] --chapter 01-我是谁/5-表达.md [--chapter 02-素材/观点库.md ...]
                     [--out <上下文外的文件>] [--force] [--limit 1000] [--skip "<字>"] [--include-pending]

  --chapter          相对根目录的章节文件，可给多个；以 / 结尾的是文件夹（导出里面的 .md，跳过 _ 开头的）。
                     不给就只导出卷首。卷首永远带上。
  --out              另外写一份到这个文件（UTF-8 无 BOM）。必须在上下文文件夹之外；已存在要加 --force。
  --limit            字数上限，默认 1000。
  --skip             用户点名不要的条目：短条目正文或素材标题里含这段字就不导出，可给多个。
  --include-pending  连「待确认」的条目也导出。默认不导：用户还没认过的东西不往外带。

做的事（规则见 `_规范.md`「六、条目格式」「八、公开边界」）：
  - 开头加一句「以下是关于我的背景，请据此回答。」；卷首只取「一页纸的我」。
  - 去掉 `> 说明：` 行、短条目行尾的（来源，年月）和 [可公开]、素材条目的四个字段行和「标题：」、
    样例的「出处」行、卷首末尾的起草说明行。
  - 剔除一切「私密」：一行里只要出现「私密」二字，这一行就不导（全角括号、位置不对、带标点、带备注都算）；
    素材条目的标题或「公开」字段里出现「私密」（写成「公开:私密」「公开：私密（备注）」也算），整条不导；
    素材条目缺「公开」字段的也不导（宁可漏，不可泄）；指向私密条目的链接去掉。
  - 白名单：素材条目「公开」字段不是「可公开」或「仅助理」的整条不导；短条目行尾方括号标记不是 [可公开] 的整行不导
    （[隐私]、[private]、[私 密] 都不导）。
  - 默认剔除「待确认」：短条目、素材条目、样例段落。卷首照样导出，但提醒它还没转正。

输出：导出文本在 stdout；字数、剔除了什么、提醒在 stderr。不往上下文里写任何东西。
退出码：0 正常；2 超过字数上限（文本照样输出，删哪块由用户定）；1 出错。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ctx_lib import (  # noqa: E402
    COVER_NAME, PENDING_TOKEN_RE, SPEC_NAME, load_root, normalize_newlines,
    parse_material_entries, parse_short_entry, read_text, run_main, section_lines,
    short_source_ok,
)

HEADER = "以下是关于我的背景，请据此回答。"
MATERIAL_DIR = "02-素材"
EXTRA_SOURCES = ("通用初稿，未经验证",)
_LINK_RE = re.compile(r"(\s*)\[\[([^\[\]]+)\]\]")
_MARKER_RE = re.compile(r"^\s*(#+|-|>)\s*")
_DRAFT_NOTE_RE = re.compile(r"^（[^（）]*(待确认|已确认|说过)[^（）]*）$")
_PUBLIC_FIELD_RE = re.compile(r"^\s*-\s*公开\s*[:：]")


def entry_is_private(e: dict) -> bool:
    """素材条目算不算私密：标题或任何一行「公开」字段里出现「私密」二字就算（写歪了也算）。"""
    if "私密" in e["title"] or "私密" in (e["fields"].get("公开") or ""):
        return True
    lv = e["fields"].get("公开")
    if lv is not None and lv not in ("可公开", "仅助理"):
        return True
    return any(_PUBLIC_FIELD_RE.match(l) and "私密" in l for l in e["lines"])


class Stats:
    def __init__(self, include_pending: bool) -> None:
        self.include_pending = include_pending
        self.private_dropped: list[str] = []
        self.no_level_dropped: list[str] = []
        self.pending_dropped: list[str] = []
        self.assistant_only = 0
        self.warnings: list[str] = []
        self.skips: list[str] = []
        self.skipped: list[str] = []

    def skip(self, text: str) -> bool:
        return any(k in text for k in self.skips)


def strip_title(t: str) -> str:
    return t[3:].strip() if t.startswith("标题：") else t.strip()


def private_titles(root: Path) -> set[str]:
    out = set()
    d = root / MATERIAL_DIR
    for p in sorted(d.glob("*.md")) if d.is_dir() else []:
        _, entries = parse_material_entries(read_text(p))
        for e in entries:
            if entry_is_private(e):
                out.add(strip_title(e["title"]))
    return out


def looks_like_source(e: dict) -> bool:
    return bool(e["ym"]) or short_source_ok(e["source"]) or e["source"] in EXTRA_SOURCES


def fix_links(line: str, privates: set[str], where: str, st: Stats) -> str:
    def repl(m: re.Match) -> str:
        space, inner = m.group(1), m.group(2)
        target, _, alias = inner.partition("|")
        fname, _, heading = target.partition("#")
        if heading and strip_title(heading) in privates:
            st.warnings.append(f"{where} 链到了私密条目，链接已去掉，看看这一行读起来通不通")
            return "\x00"
        if alias.strip():
            return space + alias.strip()
        if heading:
            return f"「{strip_title(heading)}」"
        return space + fname.strip()

    out = _LINK_RE.sub(repl, line)
    if "\x00" in out:
        out = re.sub(r"[，,、]?\s*(详见|见)?\s*\x00", "", out)
    return out


def drop_pending_samples(lines: list[str], rel: str, st: Stats) -> list[str]:
    """引用块（样例）以块为单位：块里有一行出现「私密」，整块不导；出处还是待确认的，整块不导（除非 --include-pending）。"""
    out, block = [], []

    def flush() -> None:
        if not block:
            return
        src = [b for b in block if "出处" in b]
        if any("私密" in b for b in block):
            st.private_dropped.append(f"{rel}：样例一段（含「私密」，不显示）")
        elif not st.include_pending and src and any(PENDING_TOKEN_RE.search(b) for b in src):
            st.pending_dropped.append(f"{rel}：样例「{block[0].lstrip('> ').strip()[:20]}」")
        else:
            out.extend(block)
        block.clear()

    for line in lines:
        s = line.strip()
        if s.startswith(">") and not s.startswith("> 说明："):
            block.append(line)
            continue
        flush()
        out.append(line)
    flush()
    return out


def process_lines(lines: list[str], rel: str, privates: set[str], st: Stats,
                  default_assistant: bool) -> list[str]:
    out = []
    for line in drop_pending_samples(lines, rel, st):
        s = line.strip()
        if s.startswith("> 说明："):
            continue
        if "私密" in s:
            st.private_dropped.append(f"{rel}：（含「私密」的一行，不显示）")
            continue
        if s.startswith(">") and "出处" in s:
            continue
        if _DRAFT_NOTE_RE.match(s):
            continue
        if s.startswith("#"):
            out.append("#" + s)
            continue
        if line.startswith("- "):
            e = parse_short_entry(line)
            if st.skip(e["body"]):
                st.skipped.append(f"{rel}：{e['body'][:30]}")
                continue
            if not st.include_pending and (e["source"] or "").endswith("，待确认"):
                st.pending_dropped.append(f"{rel}：{e['body'][:30]}")
                continue
            if e["paren"] is not None and looks_like_source(e):
                body = e["body"]
            elif e["paren"] is not None:
                body = f"{e['body']}（{e['paren']}）"
            else:
                body = e["body"]
            if e["mark"] not in (None, "可公开"):
                # 白名单：行尾标记只认 [可公开]（没标就是仅助理）。[隐私] [private] [私 密] 这类认不出的一律不导
                st.private_dropped.append(f"{rel}：（行尾标记不是 [可公开]，按私密处理，不显示）")
                continue
            if e["mark"] != "可公开" and default_assistant:
                st.assistant_only += 1
            line = "- " + body
        out.append(fix_links(line, privates, rel, st))
    return out


def process_file(root: Path, rel: str, privates: set[str], st: Stats) -> list[str]:
    pre, entries = parse_material_entries(read_text(root / rel))
    is_material = rel.startswith(MATERIAL_DIR + "/")
    out = process_lines(pre, rel, privates, st, True)
    for e in entries:
        level = e["fields"].get("公开")
        title = strip_title(e["title"])
        if entry_is_private(e):
            st.private_dropped.append(f"{rel}：（私密，不显示）")
            continue
        if st.skip(title):
            st.skipped.append(f"{rel}：{title[:30]}")
            continue
        if level is None and is_material:
            st.no_level_dropped.append(f"{rel}：{title[:30]}")
            continue
        if level is None:
            out += process_lines(e["lines"], rel, privates, st, True)
            continue
        if level not in ("可公开", "仅助理"):
            # 白名单：「公开」只认 可公开 / 仅助理；保密、私 密 这类写法一律当私密，整条不导
            st.private_dropped.append(f"{rel}：（「公开」不是 可公开 / 仅助理，按私密处理，不显示）")
            continue
        if not st.include_pending and "待确认" in (e["fields"].get("来源") or ""):
            st.pending_dropped.append(f"{rel}：{title[:30]}")
            continue
        if level == "仅助理":
            st.assistant_only += 1
        out.append("#### " + title)
        out += process_lines(e["body"], rel, privates, st, False)
    return tidy(out)


def tidy(lines: list[str]) -> list[str]:
    """去掉下面没有内容的标题，合并连续空行。"""
    def level(l: str) -> int:
        m = re.match(r"^(#+) ", l)
        return len(m.group(1)) if m else 0

    keep: list[str] = []
    for i, l in enumerate(lines):
        lv = level(l)
        if not lv:
            keep.append(l)
            continue
        has_body = False
        for nxt in lines[i + 1:]:
            nlv = level(nxt)
            if nlv and nlv <= lv:
                break
            if not nlv and nxt.strip():
                has_body = True
                break
        if has_body:
            if keep and keep[-1].strip():
                keep.append("")
            keep.append(l)
    out: list[str] = []
    for l in keep:
        if not l.strip() and (not out or not out[-1].strip()):
            continue
        out.append(l.rstrip())
    while out and not out[-1].strip():
        out.pop()
    return out


def count_chars(lines: list[str]) -> int:
    return sum(len(re.sub(r"\s", "", _MARKER_RE.sub("", l))) for l in lines)


def cover_part(root: Path, privates: set[str], st: Stats) -> list[str]:
    p = root / COVER_NAME
    if not p.exists():
        st.warnings.append(f"缺少 {COVER_NAME}")
        return []
    body = section_lines(read_text(p), "一页纸的我")
    if any(PENDING_TOKEN_RE.search(l) for l in body if not l.strip().startswith("> 说明：")):
        st.warnings.append("卷首「一页纸的我」还是待确认（用户还没转正），照样导出了；请用户先看一眼")
    keep_pending, st.include_pending = st.include_pending, True  # 卷首整段导出，不按条剔
    out = tidy(process_lines(body, COVER_NAME, privates, st, True))
    st.include_pending = keep_pending
    if not count_chars(out):
        st.warnings.append("卷首「一页纸的我」还是空的，导出里没有它")
        return []
    return ["## 一页纸的我", ""] + out


def expand(root: Path, chapters: list[str]) -> tuple[list[str], list[str]]:
    rels, errors = [], []
    root_r = root.resolve()
    for c in chapters:
        c = c.replace("\\", "/").strip()
        p = (root / c).resolve()
        try:
            p.relative_to(root_r)
        except ValueError:
            errors.append(f"{c} 不在上下文文件夹里")
            continue
        if not p.exists():
            errors.append(f"{c} 不存在")
            continue
        files = [p] if p.is_file() else sorted(
            x for x in p.rglob("*.md") if not x.name.startswith("_"))
        for f in files:
            rel = f.relative_to(root_r).as_posix()
            if rel in (COVER_NAME, SPEC_NAME) or (rel.startswith("_") and "/" not in rel):
                errors.append(f"{rel} 不用点名（卷首总会带上，规范、报告、进度不导出），跳过")
                continue
            if rel.startswith(".git/") or "/_填充前备份/" in "/" + rel:
                continue
            if rel not in rels:
                rels.append(rel)
    return rels, errors


def main() -> int:
    ap = argparse.ArgumentParser(description="导出上下文（卷首 + 点名章节），只读")
    ap.add_argument("--root", help="上下文根目录，不给就读 config.json")
    ap.add_argument("--chapter", action="append", default=[], help="要导出的章节，相对根目录")
    ap.add_argument("--out", help="另外写到这个文件（必须在上下文外）")
    ap.add_argument("--force", action="store_true", help="--out 的文件已存在时覆盖")
    ap.add_argument("--limit", type=int, default=1000, help="字数上限，默认 1000")
    ap.add_argument("--skip", action="append", default=[], help="点名不要的条目里的一段字，可给多个")
    ap.add_argument("--include-pending", action="store_true", help="连待确认的条目也导出")
    args = ap.parse_args()
    root = load_root(args.root)
    if not root.is_dir():
        print(f"上下文根目录不存在：{root}", file=sys.stderr)
        return 1

    rels, errors = expand(root, args.chapter)
    for e in errors:
        print(f"注意：{e}", file=sys.stderr)
    st = Stats(args.include_pending)
    st.skips = [k for k in args.skip if k.strip()]
    privates = private_titles(root)

    parts: list[tuple[str, list[str]]] = [("开头一句", [HEADER])]
    cov = cover_part(root, privates, st)
    if cov:
        parts.append(("卷首·一页纸的我", cov))
    for rel in rels:
        lines = process_file(root, rel, privates, st)
        if not count_chars([l for l in lines if not l.startswith("#")]):
            st.warnings.append(f"{rel} 去掉说明、私密和待确认之后没有可导出的内容，没放进去")
            continue
        parts.append((rel, lines))

    text = "\n\n".join("\n".join(lines) for _, lines in parts) + "\n"
    print(text, end="")

    err = sys.stderr
    counts = [(name, count_chars(lines)) for name, lines in parts]
    total = sum(n for _, n in counts)
    print("\n---- 导出统计（这部分不在导出文本里）----", file=err)
    print(f"上下文根目录：{root}", file=err)
    print(f"共 {total} 字，上限 {args.limit} 字", file=err)
    for name, n in counts:
        print(f"  {name}：{n} 字（{(n * 100 / total) if total else 0:.0f}%）", file=err)
    over = total - args.limit
    if over > 0:
        print(f"超了 {over} 字。请问用户删哪块，不要自己删。", file=err)
    if st.private_dropped:
        print(f"剔除私密 {len(st.private_dropped)} 条：", file=err)
        for x in st.private_dropped:
            print(f"  - {x}", file=err)
    if st.pending_dropped:
        print(f"剔除待确认 {len(st.pending_dropped)} 条（用户转正后才导出；要连它们一起导，加 --include-pending）：", file=err)
        for x in st.pending_dropped:
            print(f"  - {x}", file=err)
    if st.skipped:
        print(f"按 --skip 去掉 {len(st.skipped)} 条：", file=err)
        for x in st.skipped:
            print(f"  - {x}", file=err)
    if st.no_level_dropped:
        print(f"素材条目缺「公开」级别、没导出 {len(st.no_level_dropped)} 条：", file=err)
        for x in st.no_level_dropped:
            print(f"  - {x}", file=err)
    for w in st.warnings:
        print(f"注意：{w}", file=err)
    if st.assistant_only:
        print(f"提醒：导出文本里含「仅助理」内容约 {st.assistant_only} 条，只贴给你自己用的 AI，别贴到公开场合。", file=err)

    if args.out:
        out = Path(args.out).expanduser()
        try:
            out.resolve().relative_to(root.resolve())
            print(f"没写文件：{out} 在上下文文件夹里面，导出文件要放外面。", file=err)
            return 1
        except ValueError:
            pass
        if out.exists() and not args.force:
            print(f"没写文件：{out} 已存在（要覆盖加 --force）。", file=err)
            return 1
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(text.encode("utf-8"))
        print(f"已另存到：{out}", file=err)
    return 2 if over > 0 else 0


if __name__ == "__main__":
    run_main(main)
