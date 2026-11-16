"""建上下文文件夹的骨架。

用法：
    python3 scaffold.py [--root <路径>] [--git] [--update-spec]

  幂等：已存在的文件绝不覆盖，只补缺的。打印建了哪些、跳过哪些。
  模板在 ../templates/context/，按原样的目录结构复制过去；模板里的 {{年月}} 换成配置时区的当前年月。
  .gitignore 已存在时不覆盖，只把模板里有、它缺的行（比如 `99-附录/_填充前备份/`）追加到末尾。
  已有 `_规范.md` 的版本和模板不一致时，打印提醒，不覆盖。
  --update-spec  换成新版规范：旧的先备份进 99-附录/_旧规范/（同一秒重复跑不覆盖），保留旧文件末尾「标签清单」里登记的
                 内容。只有标签清单不同、正文相同的不算不一致，不换不备份。旧规范里有模板没有的二级小节时打印提醒。
  --git  上下文文件夹还不是 git 仓库时，git init 并做第一次提交（先问过用户再加）。
         本机没装 git 就说明一句、跳过；撤回靠的是备份，不靠 git，所以不装也能用。
         上下文文件夹在别人更大的 git 仓库里时，不 init、不提交，只说明一句。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ctx_lib import (  # noqa: E402
    IN_OUTER_REPO_MSG, OLD_SPEC_DIR, SPEC_NAME, TEMPLATES_DIR, git, git_available, git_commit,
    is_git_repo, load_root, normalize_newlines, now, outer_repo_top, read_text, run_main,
    spec_version, tz_label, write_text,
)

EXTRA_DIRS = ["99-附录"]
TAG_HEADING_RE = re.compile(r"^## .*标签清单")


def merge_gitignore(dst: Path, tpl_text: str) -> list[str]:
    """把模板里有、已有 .gitignore 缺的行追加进去。返回追加了哪些。"""
    have = {l.strip() for l in normalize_newlines(read_text(dst)).split("\n")}
    want = [l.strip() for l in normalize_newlines(tpl_text).split("\n")
            if l.strip() and not l.strip().startswith("#")]
    missing = [l for l in want if l not in have]
    if missing:
        old = normalize_newlines(read_text(dst))
        sep = "" if not old or old.endswith("\n") else "\n"
        write_text(dst, old + sep + "# 关于我的上下文：备份和进度文件不进 git\n" + "\n".join(missing) + "\n")
    return missing


def tag_section(text: str) -> list[str]:
    """「标签清单」标题下面、说明行以外的内容行（登记的标签等）。"""
    lines = normalize_newlines(text).split("\n")
    start = next((i for i, l in enumerate(lines) if TAG_HEADING_RE.match(l)), None)
    if start is None:
        return []
    out = []
    for l in lines[start + 1:]:
        if l.startswith("## ") or l.startswith("# "):
            break
        if l.strip() and not l.strip().startswith("> 说明："):
            out.append(l.rstrip())
    return out


def spec_body(text: str) -> str:
    """规范去掉「标签清单」登记内容之后的正文（比较两版规范是不是同一版用）。"""
    lines = normalize_newlines(text).rstrip("\n").split("\n")
    start = next((i for i, l in enumerate(lines) if TAG_HEADING_RE.match(l)), None)
    if start is None:
        return "\n".join(lines)
    keep = lines[:start + 1]
    for l in lines[start + 1:]:
        if l.startswith("## ") or l.startswith("# "):
            break
        if l.strip().startswith("> 说明："):
            keep.append(l)
    return "\n".join(keep).rstrip("\n")


def h2_titles(text: str) -> list[str]:
    return [l[3:].strip() for l in normalize_newlines(text).split("\n") if l.startswith("## ")]


def update_spec(root: Path, tpl_text: str) -> list[str]:
    p = root / SPEC_NAME
    if not p.is_file():
        return ["没有旧的 _规范.md，不用换（scaffold 已按模板新建）"]
    old = read_text(p)
    if spec_body(old) == spec_body(tpl_text):
        return ["_规范.md 已经是模板这一版（只是标签清单里登记的不同），没换、没备份"]
    stamp = now().strftime("%Y%m%d-%H%M%S")
    bdir = root / OLD_SPEC_DIR
    dest = bdir / f"{stamp}-{SPEC_NAME}"
    n = 1
    while dest.exists():          # 同一秒里再跑一次，不覆盖已有的旧规范备份
        n += 1
        dest = bdir / f"{stamp}-{n}-{SPEC_NAME}"
    write_text(dest, old)
    tags = tag_section(old)
    new = normalize_newlines(tpl_text).rstrip("\n") + "\n"
    if tags:
        new += "\n" + "\n".join(tags) + "\n"
    write_text(p, new)
    msgs = [f"旧规范（版本 {spec_version(old) or '没有版本号'}）已备份到 {dest.relative_to(root).as_posix()}",
            f"_规范.md 已换成版本 {spec_version(tpl_text)}，保留了标签清单里的 {len(tags)} 行"]
    extra = [t for t in h2_titles(old) if t not in h2_titles(tpl_text)]
    if extra:
        msgs.append("提醒：旧规范里有模板没有的二级小节（多半是用户自己加的），新规范里没有它们："
                    + "、".join(f"「{t}」" for t in extra)
                    + f"。旧文件在 {dest.relative_to(root).as_posix()}，告诉用户，问要不要把这几节抄回来。")
    return msgs


def main() -> int:
    ap = argparse.ArgumentParser(description="建上下文文件夹骨架（只补缺，不覆盖）")
    ap.add_argument("--root", help="上下文根目录，不给就读 config.json")
    ap.add_argument("--git", action="store_true", help="不是 git 仓库时 git init 并提交第一版")
    ap.add_argument("--update-spec", action="store_true", help="把 _规范.md 换成模板里的新版（旧的先备份）")
    args = ap.parse_args()

    root = load_root(args.root)
    if not TEMPLATES_DIR.is_dir():
        print(f"找不到模板目录：{TEMPLATES_DIR}", file=sys.stderr)
        return 1
    if root.exists() and not root.is_dir():
        print(f"{root} 是一个文件，不是文件夹。", file=sys.stderr)
        return 1

    t = now()
    yyyymm = t.strftime("%Y-%m")
    created_dirs, created, skipped, notes = [], [], [], []
    if not root.exists():
        root.mkdir(parents=True)
        created_dirs.append("./")

    tpl_dirs = sorted(p for p in TEMPLATES_DIR.rglob("*") if p.is_dir())
    rel_dirs = [p.relative_to(TEMPLATES_DIR).as_posix() for p in tpl_dirs] + EXTRA_DIRS
    for rel in sorted(set(rel_dirs)):
        d = root / rel
        if not d.exists():
            d.mkdir(parents=True)
            created_dirs.append(rel + "/")

    for src in sorted(p for p in TEMPLATES_DIR.rglob("*") if p.is_file()):
        rel = src.relative_to(TEMPLATES_DIR).as_posix()
        dst = root / rel
        text = read_text(src).replace("{{年月}}", yyyymm)
        if dst.exists():
            skipped.append(rel)
            if rel == ".gitignore":
                added = merge_gitignore(dst, text)
                if added:
                    notes.append(f".gitignore 已存在，没覆盖，只在末尾追加了：{'、'.join(added)}")
            continue
        write_text(dst, text)
        created.append(rel)

    tpl_spec = read_text(TEMPLATES_DIR / SPEC_NAME)
    spec_p = root / SPEC_NAME
    if args.update_spec:
        notes += update_spec(root, tpl_spec)
    elif SPEC_NAME in skipped:
        have, want = spec_version(read_text(spec_p)), spec_version(tpl_spec)
        if have != want:
            notes.append(f"提醒：文件夹里的 {SPEC_NAME} 是版本 {have or '（没有版本号，旧版）'}，技能自带的是 {want}。"
                         "没覆盖。问过用户后加 --update-spec 换新（旧的会备份进 99-附录/_旧规范/，标签清单保留）。")

    print(f"上下文根目录：{root}")
    print(f"现在：{t:%Y-%m-%d %H:%M}（{tz_label()}）")
    print(f"新建文件夹 {len(created_dirs)} 个")
    for r in created_dirs:
        print(f"  + {r}")
    print(f"新建文件 {len(created)} 个")
    for r in created:
        print(f"  + {r}")
    print(f"已存在、跳过 {len(skipped)} 个（未覆盖）")
    for r in skipped:
        print(f"  = {r}")
    for n in notes:
        print(n)

    top = outer_repo_top(root)
    if top:
        print("git：" + IN_OUTER_REPO_MSG.format(top=top) + ("（--git 没执行：不在别人的仓库里再套一个仓库。）"
                                                             if args.git else ""))
    elif is_git_repo(root):
        if args.update_spec:
            print(f"git：{git_commit(root, '换新版规范')[1]}")
        print("git：已经是 git 仓库，填充前后会各提交一次（只提交上下文文件夹自己的文件）。")
    elif args.git:
        if not git_available():
            print("git：本机没装 git，跳过。撤回靠备份，照样能用。")
        else:
            r = git(root, "init", "-q")
            if r.returncode != 0:
                print("git init 失败：" + (r.stderr.strip() or r.stdout.strip()), file=sys.stderr)
                return 1
            status, msg = git_commit(root, "建骨架")
            print(f"git：已 git init。{msg}")
    else:
        print("git：不是 git 仓库。想要每次填充都有历史可查，问用户后加 --git 再跑一次；"
              "不用 git 也行，撤回靠 99-附录/_填充前备份/。")
    return 0


if __name__ == "__main__":
    run_main(main)
