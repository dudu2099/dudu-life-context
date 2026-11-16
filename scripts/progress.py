"""方式一的进度文件 `_填充进度.md`：开工、打勾、看还剩什么、收工。中断后再叫一次就能接着来。

用法：
    python3 progress.py [--root <路径>] init <文件或文件夹> [...] [--reread]   开工：先备份（snapshot），再按盘点结果写清单
    python3 progress.py [--root <路径>] init --text "链接：某平台主页" ...   链接也能当一项（粘贴的文字先用 save_raw.py 落盘，
                                                                     再把落盘的文件当普通文件给 init）
    python3 progress.py [--root <路径>] status                       看进度：备份在哪、哪些读过、还剩哪些
    python3 progress.py [--root <路径>] done <项> [--note "备注"]      一份材料读完并写完，打勾（项写路径或文件名；
                                                                     以「批」为单位：一批写完后给这批每份各打一次；备注可不写）
    python3 progress.py [--root <路径>] skip <项> --note "原因"        这项不读了，记下原因（进报告）
    python3 progress.py [--root <路径>] finish                        全部做完，状态改成「已完成」
    python3 progress.py [--root <路径>] finish --force "<原因>"       有报告项消不掉（不是这次写的）、用户同意后，带原因收工
    python3 progress.py [--root <路径>] abandon "<原因>"              放弃这次填充：状态改成「已放弃」，之后可以重新 init
    python3 progress.py [--root <路径>] note --kind <类别> "<内容>"     往「留给报告的备忘」记一行（不给内容就读标准输入）。
        类别：故意没记 / 互相矛盾 / 拿不准 / 和已有条目重合 / 跳过原因 / 其他。每批写完、打勾时就记，
        不要攒到最后：会话断了换人接着填，前面批次的事只剩这里。内容里像有凭据就拒绝；check_format 也扫这一节。

  - init 时如果已有「进行中」的进度文件，不会重来：打印现有进度，退出码 3，照着接着做。
    已完成、已放弃的可以重新 init（新的一次会覆盖这份进度文件）。
  - 读不了的文件（inventory 认定的）init 时直接记成跳过，带原因。
  - 跨次已读清单（上下文根目录 `_已读清单.md`）：done 时把这份材料的名字、大小、内容指纹记进去。
    下次 init 遇到清单里内容没变的材料，直接记成「跳过：以前读过」；同名但内容变了的照常待读，标「内容有变」。
    --reread 强制全部重读。同一次里内容完全相同的两份，第二份记成跳过（同一份材料不能凑成两处出处）。
  - 去掉扩展名后重名的材料（笔记.md 和 笔记.txt，或不同子文件夹里的同名文件）：清单里的名字带上一级文件夹名
    或保留扩展名，后面备注「重名，出处写《…》」，写出处就照那个写。
  - 文件名里自带书名号的（《辞职》读后感.md）：备注「出处写《辞职读后感》」（出处里去掉文件名自带的书名号）。
  - 99-附录/_原始材料/ 里由 save_raw.py 落盘的文件，清单里的名字就是落盘时给的短名（出处写它）。
  - finish 先查这次有没有越界（疑似凭据；方式一写出「说过」「已确认」、改了权限边界/公开边界、动了已确认的条目），
    有就不收工、退出码 1。报出来的位置不是这次写的（用户的原话、用户自己在填充期间改的、脚本写的文件），
    问过用户、他同意后用 --force "<原因>" 收工：原因和当时没消掉的报告项（只写位置和种类）记进进度文件。
    不带原因的 --force 拒绝。用户原话区（03-流水/、04-项目/*/记录.md）里的疑似凭据、填充前备份不在了，都只是提醒，不拦收工。
    收工时把清单里材料的绝对路径抹成文件名。
  - abandon 不动已经写进去的内容；想回到填充前，用 undo.py 撤回。
  - --root 写在子命令前或后都行。
  - 进度文件的格式由本脚本读写，不要手改。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ctx_lib import (  # noqa: E402
    PROGRESS_NAME, PY, RAW_DIR, SKILL_DIR, add_to_readlist, cite_name, file_sha, find_secrets, load_readlist,
    load_root, now, raw_name, read_text, run_main, tz_label, write_text,
)
from inventory import scan  # noqa: E402
from snapshot import do_snapshot  # noqa: E402

ITEM_RE = re.compile(r"^- \[( |x|-)\] 《(.+?)》 `(.+?)`(?: —— (.*))?$")
FIELD_NAMES = ("开始", "填充前备份", "状态", "收工", "放弃", "强制收工原因", "放弃原因")
FIELD_RE = re.compile(r"^- (" + "|".join(FIELD_NAMES) + r")：(.*)$")
UNRESOLVED_HEAD = "## 收工时没消掉的报告"
MEMO_HEAD = "## 留给报告的备忘"
MEMO_KINDS = ("故意没记", "互相矛盾", "拿不准", "和已有条目重合", "跳过原因", "其他")
MEMO_RE = re.compile(r"^- 【(" + "|".join(MEMO_KINDS) + r")】(.*)$")
TEXT_PATH = "（粘贴或链接）"
KEEP_NOTE_PREFIXES = ("重名", "内容有变", "出处写")
SCRIPTS = (SKILL_DIR / "scripts").as_posix()


def load(root: Path) -> dict | None:
    p = root / PROGRESS_NAME
    if not p.is_file():
        return None
    data: dict = {"fields": {}, "items": [], "unresolved": [], "memos": []}
    in_unresolved = in_memo = False
    for line in read_text(p).split("\n"):
        if line.startswith("## "):
            in_unresolved = line.strip() == UNRESOLVED_HEAD
            in_memo = line.strip() == MEMO_HEAD
            continue
        if in_memo:
            m = MEMO_RE.match(line.strip())
            if m:
                data["memos"].append((m.group(1), m.group(2).strip()))
            continue
        if in_unresolved:
            if line.startswith("- "):
                data["unresolved"].append(line[2:].strip())
            continue
        m = FIELD_RE.match(line)
        if m:
            data["fields"][m.group(1)] = m.group(2).strip()
            continue
        m = ITEM_RE.match(line)
        if m:
            data["items"].append({"mark": m.group(1), "name": m.group(2), "path": m.group(3),
                                  "note": m.group(4) or ""})
    return data


def save(root: Path, data: dict) -> None:
    f = data["fields"]
    lines = [
        "# 填充进度", "",
        "> 说明：方式一读料的进度，由 scripts/progress.py 读写，不要手改格式。中断后再叫一次就从没打勾的接着读；"
        "撤回这次填充用「填充前备份」那一行的备份。填充进行中这里留着材料的完整路径（续跑要用），收工时抹成文件名。",
        "",
    ]
    for k in FIELD_NAMES:
        if k in f:
            lines.append(f"- {k}：{f[k]}")
    lines += ["", "## 清单", "",
              "> 说明：[ ] 没读，[x] 读完并已写入，[-] 跳过（后面写原因）。", ""]
    for it in data["items"]:
        tail = f" —— {it['note']}" if it["note"] else ""
        lines.append(f"- [{it['mark']}] 《{it['name']}》 `{it['path']}`{tail}")
    lines += ["", MEMO_HEAD, "",
              "> 说明：每批写完时用 progress.py note 记下要进报告的事（故意没记的、矛盾的、拿不准的、重合的、跳过原因）。"
              "续跑的助理和写报告时都以这里为准。只写哪份材料、哪一类，不写凭据和敏感细节本身。", ""]
    lines += [f"- 【{k}】{t}" for k, t in data.get("memos", [])]
    if data.get("unresolved"):
        lines += ["", UNRESOLVED_HEAD, "",
                  "> 说明：带原因强制收工时，check_format 还在报的项。只记位置和种类，不记内容。", ""]
        lines += [f"- {u}" for u in data["unresolved"]]
    write_text(root / PROGRESS_NAME, "\n".join(lines) + "\n")


def counts(data: dict) -> tuple[int, int, int]:
    marks = [it["mark"] for it in data["items"]]
    return marks.count("x"), marks.count("-"), marks.count(" ")


def print_status(data: dict) -> None:
    f = data["fields"]
    done, skipped, todo = counts(data)
    total = done + skipped + todo
    print(f"共 {total} 份：已读完并写入 {done} 份，跳过 {skipped} 份，还剩 {todo} 份没读。")
    print(f"状态：{f.get('状态', '?')}　开始：{f.get('开始', '?')}")
    print(f"填充前备份：{f.get('填充前备份', '?')}")
    for k in ("强制收工原因", "放弃原因"):
        if k in f:
            print(f"{k}：{f[k]}")
    groups = (("x", f"已读完（{done} 份，不要重读，已写的不要重写）"),
              (" ", f"还没读（{todo} 份，接着读这些）"), ("-", f"跳过（{skipped} 份）"))
    for mk, title in groups:
        its = [it for it in data["items"] if it["mark"] == mk]
        if not its:
            continue
        print(f"\n{title}：")
        for it in its:
            note = f"  —— {it['note']}" if it["note"] else ""
            path = f" {it['path']}" if mk == " " else ""
            print(f"  [{mk}] 《{it['name']}》{path}{note}")
    memos = data.get("memos", [])
    print(f"\n留给报告的备忘 {len(memos)} 条（写报告时以这些加上后面批次自己的发现为准）：" if memos
          else "\n留给报告的备忘：还没有。")
    for k, t in memos:
        print(f"  - 【{k}】{t}")


def find(data: dict, key: str) -> dict:
    key_n = key.replace("\\", "/")
    exact = [it for it in data["items"] if it["path"].replace("\\", "/") == key_n]
    if exact:
        return exact[0]
    try:
        resolved = str(Path(key).expanduser().resolve()).replace("\\", "/")
        exact = [it for it in data["items"] if it["path"].replace("\\", "/") == resolved]
        if exact:
            return exact[0]
    except OSError:
        pass
    by_name = [it for it in data["items"] if it["name"] == key or Path(it["path"]).name == key]
    if len(by_name) == 1:
        return by_name[0]
    if not by_name:
        raise SystemExit(f"清单里找不到「{key}」。用 status 看清单里的写法。")
    raise SystemExit(f"「{key}」对上了 {len(by_name)} 项，请写完整路径。")


def raw_short_name(root: Path, path: Path) -> str | None:
    """save_raw.py 落盘的文件（99-附录/_原始材料/ 里）→ 短名（文件第一行 `# 短名`，和 save_raw 打印的一致）。"""
    try:
        path.resolve().relative_to((root / RAW_DIR).resolve())
    except ValueError:
        return None
    return raw_name(path)


def display_names(root: Path, readable: list[dict]) -> dict[str, tuple[str, str]]:
    """每个可读文件在清单里叫什么、备注什么。重名（去掉扩展名后同名）的要能区分；
    文件名自带书名号的，备注出处该写成什么。"""
    out: dict[str, tuple[str, str]] = {}
    groups: dict[str, list[Path]] = {}
    for x in readable:
        p = Path(x["path"])
        short = raw_short_name(root, p)
        if short:
            out[x["path"]] = (short, "")
            continue
        groups.setdefault(p.stem, []).append(p)
    for stem, ps in groups.items():
        if len(ps) == 1:
            p = ps[0]
            note = f"出处写《{cite_name(p.stem)}》（去掉文件名里的书名号）" if cite_name(p.stem) != p.stem else ""
            out[str(p)] = (p.name, note)
            continue
        for p in ps:
            same_dir = [q for q in ps if q.parent == p.parent]
            same_dirname = [q for q in ps if q.parent != p.parent and q.parent.name == p.parent.name]
            if len(same_dir) > 1 and not same_dirname:   # 同一个文件夹里 笔记.md / 笔记.txt：保留扩展名
                name = src = p.name
            elif not same_dirname:                       # 不同文件夹里的同名文件：带上一级文件夹名
                name, src = f"{p.parent.name}/{p.name}", f"{p.parent.name}/{p.stem}"
            else:                                        # 两样都撞：文件夹名和扩展名都带上
                name = src = f"{p.parent.name}/{p.name}"
            out[str(p)] = (name, f"重名，出处写《{cite_name(src)}》")
    return out


def boundary_check(root: Path) -> tuple[list[str], list[str]]:
    """收工前的兜底：疑似凭据、方式一越界（复用 check_format 的检查）。返回 (问题, 提醒)。"""
    import check_format as cf
    probs: list[str] = []
    hints: list[str] = []

    def add(group: str, msg: str) -> None:
        if group == cf.G_HINT:
            hints.append(msg)
        else:
            probs.append(f"【{group}】{msg}")

    cf.check_secrets(root, add)
    cf.check_mode1(root, add)
    return probs, hints


def brief(problem: str) -> str:
    """记进进度文件的一项：只留位置和种类（到第一个句号为止），不带建议，更不带内容。"""
    s = problem.split("。", 1)[0]
    return s.strip()


def scrub_paths(data: dict) -> None:
    for it in data["items"]:
        if it["path"] != TEXT_PATH:
            it["path"] = Path(it["path"]).name   # 收工后不再需要完整路径，抹成文件名


def main() -> int:
    ap = argparse.ArgumentParser(description="填充进度")
    ap.add_argument("--root", help="上下文根目录，不给就读 config.json")
    sub = ap.add_subparsers(dest="cmd", required=True)
    # --root 写在子命令前或后都行：子命令这边不给默认值，免得盖掉前面给的
    root_kw = {"help": "上下文根目录（写在子命令后也行）", "default": argparse.SUPPRESS}
    a = sub.add_parser("init")
    a.add_argument("--root", **root_kw)
    a.add_argument("paths", nargs="*")
    a.add_argument("--text", action="append", default=[], help="链接（或没法落盘的东西），写一个简短的名字")
    a.add_argument("--reread", action="store_true", help="以前读过、内容没变的材料也重读")
    sub.add_parser("status").add_argument("--root", **root_kw)
    for name in ("done", "skip"):
        b = sub.add_parser(name)
        b.add_argument("--root", **root_kw)
        b.add_argument("item")
        b.add_argument("--note", default="", help="自由备注，可不写（skip 必须写原因）")
    f = sub.add_parser("finish")
    f.add_argument("--root", **root_kw)
    f.add_argument("--force", metavar="原因", default=None,
                   help="有报告项消不掉、用户同意后带原因收工（原因必须写）")
    nt = sub.add_parser("note")
    nt.add_argument("--root", **root_kw)
    nt.add_argument("--kind", required=True, choices=MEMO_KINDS, help="备忘的类别")
    nt.add_argument("text", nargs="?", default=None, help="备忘内容；不给就从标准输入读")
    ab = sub.add_parser("abandon")
    ab.add_argument("--root", **root_kw)
    ab.add_argument("reason", nargs="?", default="", help="为什么放弃（必须写）")
    args = ap.parse_args()
    root = load_root(getattr(args, "root", None))
    if not root.is_dir():
        print(f"上下文根目录不存在：{root}（先跑 scaffold.py）", file=sys.stderr)
        return 1
    data = load(root)

    if args.cmd == "init":
        if data and data["fields"].get("状态") == "进行中":
            print("已有一次没做完的填充，不重来。接着做下面没打勾的：")
            print_status(data)
            print(f"真要放弃这次、重新开工：问过用户后先 {PY} \"{SCRIPTS}/progress.py\" abandon \"<原因>\"。")
            return 3
        if not args.paths and not args.text:
            print("init 至少要给一个文件、文件夹，或一个 --text。", file=sys.stderr)
            return 1
        missing = [p for p in args.paths if not Path(p).expanduser().exists()]
        if missing:
            print("找不到：" + "、".join(missing), file=sys.stderr)
            return 2
        res = scan([Path(p).expanduser() for p in args.paths]) if args.paths else \
            {"readable": [], "unreadable": []}
        names = display_names(root, res["readable"])
        readlist = [] if args.reread else load_readlist(root)
        read_by_sha = {r["sha"]: r for r in readlist}
        read_names = {r["name"] for r in readlist}
        dest, gitmsg = do_snapshot(root, "填充前")
        t = now()
        items, seen_sha, n_old, n_changed, dup_names, cite_notes = [], {}, 0, 0, [], []
        for x in res["readable"]:
            name, note = names.get(x["path"], (x["name"], ""))
            if note.startswith("重名"):
                dup_names.append(f"{name}（{note}）")
            elif note.startswith("出处写"):
                cite_notes.append(f"{name}：{note}")
            sha = file_sha(Path(x["path"]))
            if sha in seen_sha:
                items.append({"mark": "-", "name": name, "path": x["path"],
                              "note": f"跳过：和《{seen_sha[sha]}》内容完全相同（同一份材料不能算两处出处）"})
                continue
            seen_sha[sha] = name
            if sha in read_by_sha:
                n_old += 1
                items.append({"mark": "-", "name": name, "path": x["path"],
                              "note": f"跳过：以前读过（{read_by_sha[sha]['when']}），内容没变"})
                continue
            if name in read_names:
                n_changed += 1
                note = "内容有变（以前读过同名的一份，这次内容不一样，照常读）" + (f"；{note}" if note else "")
            items.append({"mark": " ", "name": name, "path": x["path"], "note": note})
        items += [{"mark": " ", "name": t_, "path": TEXT_PATH, "note": ""} for t_ in args.text]
        items += [{"mark": "-", "name": x["name"], "path": x["path"], "note": "读不了：" + x["reason"]}
                  for x in res["unreadable"]]
        data = {"fields": {"开始": f"{t:%Y-%m-%d %H:%M}（{tz_label()}）",
                           "填充前备份": dest.relative_to(root).as_posix() + "/",
                           "状态": "进行中"}, "items": items, "unresolved": [], "memos": []}
        save(root, data)
        print(f"已开工：清单 {len(items)} 项，写在 {PROGRESS_NAME}")
        print(f"填充前备份：{data['fields']['填充前备份']}　git：{gitmsg}")
        if n_old:
            print(f"有 {n_old} 份以前读过、内容没变，记成跳过（要重读，加 --reread 重新 init 之前先问用户）。")
        if n_changed:
            print(f"有 {n_changed} 份和以前读过的同名但内容有变，照常读；写之前先看已有条目，别把旧内容重复写一遍。")
        if dup_names:
            print("有去掉扩展名后重名的材料，出处要写成能区分的样子：")
            for d in dup_names:
                print(f"  - {d}")
        if cite_notes:
            print("有文件名自带书名号的材料，出处里去掉它自带的书名号：")
            for d in cite_notes:
                print(f"  - {d}")
        print_status(data)
        return 0

    if data is None:
        print(f"还没有 {PROGRESS_NAME}（这次填充还没开工，先 init）。", file=sys.stderr)
        return 1

    if args.cmd == "status":
        print_status(data)
        return 0

    if args.cmd in ("done", "skip"):
        try:
            it = find(data, args.item)
        except SystemExit as e:
            print(str(e), file=sys.stderr)
            return 1
        if args.cmd == "skip" and not args.note:
            print("skip 要写原因：--note \"……\"", file=sys.stderr)
            return 1
        kept = it["note"] if it["note"].startswith(KEEP_NOTE_PREFIXES) else ""
        it["mark"] = "x" if args.cmd == "done" else "-"
        it["note"] = "；".join(x for x in (kept, args.note) if x)
        if args.cmd == "done" and it["path"] != TEXT_PATH and Path(it["path"]).is_file():
            p = Path(it["path"])
            add_to_readlist(root, it["name"], file_sha(p), p.stat().st_size,
                            data["fields"].get("开始", f"{now():%Y-%m-%d %H:%M}"))
        save(root, data)
        done, skipped, todo = counts(data)
        print(f"{'已打勾' if args.cmd == 'done' else '已跳过'}：《{it['name']}》。读完 {done}，跳过 {skipped}，还剩 {todo}")
        return 0

    if args.cmd == "note":
        text = args.text if args.text is not None else sys.stdin.buffer.read().decode("utf-8", "replace")
        text = " ".join(text.split())
        if not text:
            print("note 要写内容：note --kind 故意没记 \"《某材料》里有凭据类内容，没记\"", file=sys.stderr)
            return 1
        if find_secrets(text):
            print("这条备忘里像有凭据（内容不显示），没记。备忘只写哪份材料、哪一类，"
                  "例如「《某材料》里有凭据类内容，没记」。", file=sys.stderr)
            return 1
        data.setdefault("memos", []).append((args.kind, text))
        save(root, data)
        print(f"已记备忘【{args.kind}】（共 {len(data['memos'])} 条）。status 能看到全部。")
        return 0

    state = data["fields"].get("状态")
    if args.cmd == "abandon":
        reason = (args.reason or "").strip()
        if not reason:
            print("abandon 要写原因：abandon \"<为什么放弃>\"。", file=sys.stderr)
            return 1
        if state != "进行中":
            print(f"这份进度的状态是「{state}」，不是进行中，不用放弃。要开新的一次，直接 init。", file=sys.stderr)
            return 1
        data["fields"]["状态"] = "已放弃"
        data["fields"]["放弃"] = f"{now():%Y-%m-%d %H:%M}"
        data["fields"]["放弃原因"] = reason
        scrub_paths(data)
        save(root, data)
        done, skipped, todo = counts(data)
        print(f"已放弃这次填充（读完 {done}，跳过 {skipped}，没读 {todo}）。原因记进了 {PROGRESS_NAME}。")
        print("已经写进各章的内容没动。想回到这次填充之前的样子，告诉用户可以说「撤回这次填充」"
              f"（{PY} \"{SCRIPTS}/undo.py\" --dry-run 先看会改什么）。")
        if done:
            print(f"注意：打过勾的 {done} 份已记进 _已读清单.md，重新 init 时会记成「以前读过」跳过；"
                  "撤回会把已读清单一起恢复。不撤回又要重读，init 加 --reread。")
        print("之后可以重新 init。")
        return 0

    if args.cmd == "finish":
        if state != "进行中":
            print(f"这份进度的状态是「{state}」，不用再收工。", file=sys.stderr)
            return 1
        reason = None
        if args.force is not None:
            reason = args.force.strip()
            if not reason:
                print("--force 必须带原因：finish --force \"<为什么这些报告项不改>\"（原因会记进进度文件）。",
                      file=sys.stderr)
                return 1
        done, skipped, todo = counts(data)
        if todo:
            print(f"还有 {todo} 项没打勾，不能收工：", file=sys.stderr)
            print_status(data)
            return 1
        probs, hints = boundary_check(root)
        for h in hints:
            print(f"提醒（不拦收工）：{h}")
        if probs and reason is None:
            print(f"有 {len(probs)} 处越界或疑似凭据，没收工：", file=sys.stderr)
            for x in probs:
                print(f"  - {x}", file=sys.stderr)
            print("逐条看报出来的那一行：是这次自己写的，就改到不报（改完跑 check_format.py 确认）；"
                  "不是这次写的（用户的原话、用户在填充期间自己改的条目或守则、脚本写的文件），不许改它——"
                  "把位置告诉用户，问一句，他同意后用 finish --force \"<原因>\" 收工。", file=sys.stderr)
            return 1
        data["fields"]["状态"] = "已完成"
        data["fields"]["收工"] = f"{now():%Y-%m-%d %H:%M}"
        if reason is not None and probs:
            data["fields"]["强制收工原因"] = reason
            data["unresolved"] = [brief(x) for x in probs]
        scrub_paths(data)
        save(root, data)
        print(f"已收工：读完 {done}，跳过 {skipped}。清单里材料的完整路径已抹成文件名。")
        if reason is not None and probs:
            print(f"带原因强制收工：{len(probs)} 项没消掉，原因和这些位置记进了 {PROGRESS_NAME}"
                  f"「{UNRESOLVED_HEAD[3:]}」。报告里也照实写一句。")
        elif reason is not None:
            print("（给了 --force，但没有要消的报告项，按正常收工处理，原因没记。）")
        return 0
    return 1


if __name__ == "__main__":
    run_main(main)
