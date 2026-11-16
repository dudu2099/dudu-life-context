"""把粘贴的文字、口述转写、访谈里用户的回答落盘，让出处能核对；也能列出、删掉已存的原话。

用法（内容从标准输入读，或用 --file 读一个文件；不要把原话写在命令行参数里，转义会弄坏原话）：
    python3 save_raw.py [--root <路径>] --name "粘贴-最近在忙的事" < 内容.txt
    python3 save_raw.py [--root <路径>] --name "口述" --file "<先用 Write 写好的临时文件>"
    python3 save_raw.py [--root <路径>] --to "<已落盘的那个文件>" --file "<下一段>"    往同一份里追加（访谈一问一答地记）
    python3 save_raw.py [--root <路径>] --list                                       列出已存的原话（名字、时间、大小）
    python3 save_raw.py [--root <路径>] --delete "<短名或文件名>" [--yes]             删掉一份（不带 --yes 只打印会删什么）

  - 落到 `99-附录/_原始材料/<时间>-<短名>.md`，文件第一行是 `# 短名`。条目的出处写《短名》（访谈的短名就是「访谈」，
    出处照规范写 说过 / 访谈，说过 / 归纳自访谈）。同一个短名已经存过一份，新的一份短名后面加 -2、-3，打印出来的就是
    该写的出处。
  - 交给脚本之前，助理先按规范把健康、感情、家庭矛盾、个人收入存款负债具体数字的句子换成一行占位
    「（此处一句涉及<类别>，没存）」——这一步脚本做不了，靠助理写临时文件时做。
  - 落盘前脚本再用和 check_format 同一套规则（ctx_lib）扫一遍：哪一段（空行隔开）命中疑似凭据，那一段就不存，
    换成一行「（这里原有一段疑似凭据，没有存）」。打印第几段、哪一类，不显示内容。
  - 方式一：落盘后把打印出来的路径当普通文件交给 `progress.py init`，清单里的名字就是短名。
  - 存下的原话会进填充前备份（撤回、换回都靠它），文件夹用了 git 的也会进 git 历史。--delete 只删这一份文件，
    备份和 git 历史里的要另外处理（undo.py --delete-backup；git 历史由用户自己处理）。
退出码：0 正常；1 没有内容、参数不对、找不到要删的。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ctx_lib import (  # noqa: E402
    PY, RAW_DIR, SKILL_DIR, find_secrets, is_git_repo, load_root, normalize_newlines, now, raw_name,
    read_text, run_main, write_text,
)

DROPPED = "（这里原有一段疑似凭据，没有存）"
SCRIPTS = (SKILL_DIR / "scripts").as_posix()


def short_name(name: str) -> str:
    s = re.sub(r'[\\/:*?"<>|：\s《》]+', "-", name.strip()).strip("-.")
    return s[:40] or "粘贴"


def decode(b: bytes) -> str:
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", errors="replace")


def scrub(text: str) -> tuple[str, list[tuple[int, list[str]]]]:
    """按段（空行隔开）去掉命中疑似凭据的段。返回 (留下的文字, [(第几段, [种类])])。"""
    paras = re.split(r"\n\s*\n", normalize_newlines(text).strip("\n"))
    out, dropped = [], []
    for i, p in enumerate(paras, 1):
        hits = find_secrets(p)
        if hits:
            out.append(DROPPED)
            dropped.append((i, sorted({k for _, ks in hits for k in ks})))
        else:
            out.append(p)
    return "\n\n".join(out), dropped


def raw_files(root: Path) -> list[Path]:
    d = root / RAW_DIR
    return sorted(p for p in d.glob("*.md")) if d.is_dir() else []


def unique_name(root: Path, name: str) -> str:
    """同一个短名已经有一份了，就加 -2、-3，免得两份原话的出处写成同一个名字。"""
    taken = {raw_name(p) for p in raw_files(root)}
    if name not in taken:
        return name
    n = 2
    while f"{name}-{n}" in taken:
        n += 1
    return f"{name}-{n}"


def list_raw(root: Path) -> int:
    files = raw_files(root)
    if not files:
        print(f"{RAW_DIR}/ 里还没有存过原话。")
        return 0
    print(f"{RAW_DIR}/ 里存了 {len(files)} 份原话（出处名 ← 文件）：")
    for p in files:
        print(f"  《{raw_name(p)}》 ← {p.name}（{p.stat().st_size / 1024:.1f} KB）")
    print("这些文件也在填充前备份里" + ("，也在 git 历史里" if is_git_repo(root) else "") + "。"
          f"删一份：{PY} \"{SCRIPTS}/save_raw.py\" --delete \"<短名或文件名>\"（先不带 --yes 看会删什么）。")
    return 0


def delete_raw(root: Path, key: str, yes: bool) -> int:
    key = key.strip()
    if not key:
        print("--delete 要给短名或文件名。先 --list 看有哪些。", file=sys.stderr)
        return 1
    files = raw_files(root)
    hits = [p for p in files if p.name == key or p.name == key + ".md"]
    if not hits:
        hits = [p for p in files if raw_name(p) == key]
    if not hits:
        print(f"找不到「{key}」。先 --list 看有哪些。", file=sys.stderr)
        return 1
    if len(hits) > 1:
        print(f"「{key}」对上了 {len(hits)} 份，请写文件名：" + "、".join(p.name for p in hits), file=sys.stderr)
        return 1
    p = hits[0]
    rel = p.relative_to(root).as_posix()
    if not yes:
        print(f"会删掉：{rel}（出处名《{raw_name(p)}》）。没加 --yes，什么都没删。")
        print("提醒：用这份原话写的条目，出处就核不回去了；填充前备份里还有它的副本"
              + ("，git 历史里也有" if is_git_repo(root) else "") + "。")
        return 0
    p.unlink()
    print(f"已删：{rel}。")
    print(f"填充前备份里还有副本：要彻底删，{PY} \"{SCRIPTS}/undo.py\" --list 看哪些备份是它存进来之后做的，"
          "再按用户的意思删那几份备份。" + ("用了 git：git 历史里也还有，要用户自己处理。" if is_git_repo(root) else ""))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="把原话落盘到 99-附录/_原始材料/，或列出、删掉已存的")
    ap.add_argument("--root", help="上下文根目录，不给就读 config.json")
    ap.add_argument("--name", help="短名，出处就写它，例如「粘贴-最近在忙的事」「口述」「访谈」")
    ap.add_argument("--file", help="从这个文件读内容（不给就读标准输入）")
    ap.add_argument("--to", help="追加到已落盘的这个文件（一次访谈记在同一份里）")
    ap.add_argument("--list", action="store_true", help="列出已存的原话")
    ap.add_argument("--delete", metavar="短名或文件名", default=None, help="删掉一份（要加 --yes 才真删）")
    ap.add_argument("--yes", action="store_true", help="和 --delete 一起用：真的删")
    args = ap.parse_args()
    root = load_root(args.root)
    if not root.is_dir():
        print(f"上下文根目录不存在：{root}（先跑 scaffold.py）", file=sys.stderr)
        return 1
    if args.list:
        return list_raw(root)
    if args.delete is not None:
        return delete_raw(root, args.delete, args.yes)
    if not args.name and not args.to:
        print("要给 --name（新落一份）或 --to（追加到已有的一份），或者 --list / --delete。", file=sys.stderr)
        return 1
    raw = decode(Path(args.file).expanduser().read_bytes()) if args.file else decode(sys.stdin.buffer.read())
    if not raw.strip():
        print("没读到内容。内容从标准输入给，或先用 Write 写成文件再 --file。", file=sys.stderr)
        return 1
    text, dropped = scrub(raw)

    raw_dir = (root / RAW_DIR).resolve()
    if args.to:
        dest = Path(args.to).expanduser()
        if not dest.is_absolute() and not dest.exists():
            dest = root / dest
        try:
            dest.resolve().relative_to(raw_dir)
        except ValueError:
            print(f"--to 只能是 {RAW_DIR}/ 里的文件。", file=sys.stderr)
            return 1
        if not dest.is_file():
            print(f"找不到要追加的文件：{dest}", file=sys.stderr)
            return 1
        name = raw_name(dest)
        is_interview = re.fullmatch(r"访谈(?:-\d+)?", name) is not None
        write_text(dest, read_text(dest).rstrip("\n") + "\n\n" + text + "\n")
    else:
        base = short_name(args.name)
        name = unique_name(root, base)
        stamp = now().strftime("%Y%m%d-%H%M")
        dest = root / RAW_DIR / f"{stamp}-{name}.md"
        n = 1
        while dest.exists():
            n += 1
            dest = root / RAW_DIR / f"{stamp}-{name}-{n}.md"
        is_interview = base == "访谈"
        cite = ("条目的出处照规范写：说过 / 访谈，说过 / 归纳自访谈" if is_interview
                else f"条目的出处写《{name}》")
        head = (f"# {name}\n\n> 说明：用户给的原话（粘贴、口述或访谈回答），由 scripts/save_raw.py 落盘；"
                "涉及健康、感情、家庭矛盾、个人金额具体数字的句子换成了占位，疑似凭据的段落没有存，其余原样。"
                f"{cite}。这里不加载，用户点名才读。\n\n")
        write_text(dest, head + text + "\n")

    rel = dest.relative_to(root).as_posix() if dest.resolve().is_relative_to(root.resolve()) else str(dest)
    print(f"已落盘：{rel}")
    if is_interview:
        print("出处照规范写：说过 / 访谈，说过 / 归纳自访谈。下一段回答用 --to 追加到这一份。")
    else:
        print(f"出处写《{name}》。方式一开工时把这个文件当普通文件交给 progress.py init：")
        print(f"  {PY} \"{SCRIPTS}/progress.py\" init \"{dest}\" [其他材料 ...]")
    if dropped:
        where = "；".join(f"第 {i} 段（疑似{'、'.join(k)}）" for i, k in dropped)
        print(f"有 {len(dropped)} 段疑似凭据没有存（内容不显示）：{where}。"
              "报告「看到了但故意没记的」里如实说一句「粘贴的内容里有凭据类内容，没存、没记」。")
    return 0


if __name__ == "__main__":
    run_main(main)
