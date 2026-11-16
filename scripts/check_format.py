"""检查上下文文件夹的格式。只读，不改任何文件。

用法：
    python3 check_format.py [--root <路径>]

查什么（格式以 `_规范.md` 为准）：
  编码        所有 .md 是否带 BOM（不查 .git 和填充前备份）
  卷首        「一页纸的我」正文是否超过 500 字（不算 `> 说明：` 行和空白）
  导航表      路径是否存在、写法对不对（文件带 .md，文件夹以 / 结尾）
  章节状态表  路径是否存在；状态写法；写「空」但有内容、写「有内容」但是空的；最后更新的写法；
              实际存在的章节文件表里没有这一行（给出补登记的 meta.py 命令）
  素材条目    缺 日期 / 标签 / 公开 / 来源，或写法不对（来源要带状态：待确认 / 已确认 / 说过；
              「公开」只能是 可公开 / 仅助理 / 私密）；标题不以「标题：」开头
  素材库长度  单个素材库超过约 150 条，该按年拆
  短条目格式  01-我是谁、05-守则、04-项目/<项目>/定位.md 和 打法.md：缺来源或年月、来源写法不在规范之内
              （提炼自 / 归纳自《文件名》可带一个中文括号备注，归纳自访谈也认）、
              行尾标记不是 [可公开] / [私密]、不是「- 」开头的一行一条；
              同一份材料在一条出处里写了两次（不能凑成「两处出处」）
  5-表达样例  每段超过 150 字、没注明出处；段数超了（每种声音 2 到 4 段、合计不超过 8 段；
              只有一种声音时 3 到 5 段。少于下限不报：访谈只拿到一段时就只放一段）
  标签清单    素材条目用了但规范末尾清单没登记；清单登记了但没有条目在用
  半年没更新  短条目行尾年月距当前年月（配置时区）6 个月及以上
  疑似凭据    除 99-附录/_填充前备份/ 外所有 .md（含报告、附录）：证件号、卡号（Luhn）、密钥前缀、
              「密码：xxx」「密码是 xxx」「password=xxx」这类口令、表格里密码一栏的值、带 SSN 字样的 SSN。
              只报文件和行号，不显示内容。分级：03-流水/、04-项目/*/记录.md 是用户自己的原话，命中只算提醒；
              _填充进度.md、_已读清单.md 只扫备注，不扫材料名和路径
  私密标记    写法不规范的私密标记（【私密】（私密）、[私密] 不在行尾、带备注、素材「公开：」写歪……）
  01 里的矛盾 01-我是谁 下出现「【矛盾」这类标注（矛盾两边都不该进 01）
  方式一越界  有「进行中」的 _填充进度.md 时，和进度里记的那份填充前备份逐文件比：新增的行里有「说过」或
              「已确认」；05-守则/权限边界.md、公开边界.md 被改；备份里已确认（或说过）的条目被改或删
              （素材条目整条比，样例整块比）；卷首「一页纸的我」备份时已经没有待确认起草行却被重写。
              那份备份不在了：只提醒「没法比对」，不算问题
另外单列（不算问题）：还有多少条「待确认」没转正，按文件分（不数 03-流水/ 和 04-项目/*/记录.md）；
有没有一次没做完的填充；提醒（卷首或 01-我是谁、协作偏好的条目里用「他」「她」「用户」作主语，
原话区里的疑似凭据，备份不在了没法比对）。

有任何问题时退出码为 1，没有为 0（待确认和提醒不算问题）。CRLF 换行也能查。
"""
from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ctx_lib import (  # noqa: E402
    BACKUP_DIR, COVER_NAME, GUARD_FILES, LEVEL_MARKS, MATERIAL_FIELDS, PROGRESS_NAME, SPEC_NAME,
    content_lines, entry_md_files, find_secrets, has_bom, load_root, material_source_ok,
    normalize_newlines, now, parse_material_entries, parse_nav, parse_short_entry, parse_status,
    pending_lines, read_text, run_main, scan_md_files, secret_kinds, section_lines, short_source_ok,
    tz_label, mask_for_scan, secret_tier, PY, SKILL_DIR, is_note_line,
)

SPLIT_LIMIT = 150
STALE_MONTHS = 6
SAMPLE_LIMIT = 150
SAMPLE_ONE_VOICE_MAX = 5      # 只有一种声音：3 到 5 段
SAMPLE_PER_VOICE_MAX = 4      # 两种以上声音：每种 2 到 4 段
SAMPLE_TOTAL_MAX = 8          # 合计不超过 8 段
SAMPLE_RULE = "规范是每种声音 2 到 4 段、合计不超过 8 段；只有一种声音时 3 到 5 段"
SHORT_DIRS = ["01-我是谁", "05-守则"]
PROJECT_DIR = "04-项目"
PROJECT_SHORT_FILES = ["定位.md", "打法.md"]
MATERIAL_DIR = "02-素材"
WHO_DIR = "01-我是谁"
PLAYBOOK_EXTRA_SOURCE = "通用初稿，未经验证"

GROUPS = [
    "疑似凭据（只给位置，不显示内容）", "方式一越界", "编码", "卷首", "导航表", "章节状态表",
    "素材条目", "素材库长度", "短条目格式", "私密标记写法", "01 里的矛盾标注", "5-表达样例", "标签清单",
    "半年没更新（逐条问用户还成立吗）",
]
G_SECRET, G_MODE1 = GROUPS[0], GROUPS[1]
G_HINT = "提醒"   # 不算问题：退出码不因此变 1，也不拦收工

# 私密标记：规范写法只有两种——短条目行尾 `[私密]`，素材条目 `- 公开：私密`
_PRIVATE_BRACKET_RE = re.compile(r"[\[【［（(〔]\s*私密[^\]】］）)〕\n]*[\]】］）)〕]")
_PUBLIC_FIELD_RE = re.compile(r"^\s*-\s*公开\s*[:：]")
_CONTRA_RE = re.compile(r"[【\[［（(〔]\s*矛盾|矛盾[，,：:、]?\s*待(?:你|用户|我)?(?:来)?定|#矛盾")
_PRONOUN_RE = re.compile(r"(?:^|[。！？；，：、“”「」『』（）()\s\-—*·.!?;,:])(他|她)(?!人|乡|日|处|者)")
_CONFIRMED_TOKEN_RE = re.compile(r"[，（：]\s*(?:已确认|说过)(?=，|）|\s*$)")
_DRAFT_PENDING_RE = re.compile(r"^（起草自[^（）]*待确认[^（）]*）$")
_TITLE_RE = re.compile(r"《([^《》]+)》")


def is_private_line(line: str) -> bool:
    """私密的、或含疑似凭据的行：报问题时都不显示内容。"""
    return "私密" in line or bool(secret_kinds(line))


def dup_titles(source: str | None) -> list[str]:
    titles = _TITLE_RE.findall(source or "")
    return sorted({t for t in titles if titles.count(t) > 1})


def body_chars(lines: list[str]) -> int:
    n = 0
    for line in lines:
        s = line.strip()
        if not s or s.startswith("> 说明："):
            continue
        n += len(re.sub(r"\s", "", s))
    return n


def snippet(s: str, n: int = 36) -> str:
    s = s.strip()
    return s if len(s) <= n else s[:n] + "……"


def md_files(d: Path) -> list[Path]:
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("*.md") if not p.name.startswith("_"))


def project_dirs(root: Path) -> list[Path]:
    d = root / PROJECT_DIR
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.is_dir() and not p.name.startswith("_"))


def chapter_files(root: Path) -> list[Path]:
    out = []
    for d in ["01-我是谁", MATERIAL_DIR, "05-守则"]:
        out += md_files(root / d)
    for p in project_dirs(root):
        out += md_files(p)
    return out


def short_entry_files(root: Path) -> list[Path]:
    out = []
    for d in SHORT_DIRS:
        out += md_files(root / d)
    for p in project_dirs(root):
        out += [p / n for n in PROJECT_SHORT_FILES if (p / n).is_file()]
    return out


def month_index(ym: str) -> int | None:
    y, m = int(ym[:4]), int(ym[5:7])
    return y * 12 + m if 1 <= m <= 12 else None


def check_short_file(root: Path, p: Path, now_idx: int, add) -> None:
    rel = p.relative_to(root).as_posix()
    is_playbook = p.name == "打法.md" and p.parent.parent.name == PROJECT_DIR
    is_expr = rel == "01-我是谁/5-表达.md"
    h2 = h3 = ""
    blocks: list[list[tuple[int, str]]] = []
    voices: list[str] = []
    cur: list[tuple[int, str]] | None = None
    for no, line in enumerate(normalize_newlines(read_text(p)).split("\n"), 1):
        s = line.strip()
        if line.startswith("## "):
            h2, h3 = line[3:].strip(), ""
        elif line.startswith("### "):
            h3 = line[4:].strip()
        in_sample = is_expr and h2.startswith("样例")
        if in_sample and s.startswith(">") and not s.startswith("> 说明："):
            if cur is None:
                cur = []
                blocks.append(cur)
                voices.append(h3)
            cur.append((no, s.lstrip(">").strip()))
            continue
        cur = None
        if not s or s.startswith("#") or s.startswith(">"):
            continue
        where = f"{rel}:{no}"
        if not line.startswith("- "):
            shown = "（私密或含疑似凭据，不显示内容）" if is_private_line(line) else snippet(s)
            add("短条目格式", f"{where} 不是「- 」开头的一行一条：{shown}")
            continue
        e = parse_short_entry(line)
        if is_private_line(line):
            s = "（私密或含疑似凭据，不显示内容，请用户自己打开这一行看）"
            e = dict(e, body=s)
        dups = dup_titles(e["source"])
        if dups:
            add("短条目格式", f"{where} 出处里同一份材料写了不止一次（{'、'.join('《' + d + '》' for d in dups)}），"
                "同一份材料读两遍不算两处出处")
        if e["paren"] is None:
            add("短条目格式", f"{where} 行尾缺（来源，年月）：{snippet(s)}")
            continue
        src_ok = short_source_ok(e["source"]) or (is_playbook and e["source"] == PLAYBOOK_EXTRA_SOURCE)
        if e["ym"] is None:
            add("短条目格式", f"{where} 行尾括号里缺年月，或不是（来源，YYYY-MM）的写法：（{'不显示' if is_private_line(line) else snippet(e['paren'], 24)}）")
        elif not src_ok:
            add("短条目格式", f"{where} 来源「{e['source']}」不是规范里的写法"
                "（说过 / 提炼自《文件名》，待确认或已确认 / 归纳自《文件名》，待确认或已确认 / "
                "归纳自访谈，待确认或已确认 / 默认值，待用户调整；《文件名》后可跟一个中文括号备注）："
                f"{snippet(s)}")
        if e["mark"] is not None and e["mark"] not in LEVEL_MARKS:
            add("短条目格式", f"{where} 行尾标记 [{e['mark']}] 不是 [可公开] 或 [私密]：{snippet(s)}")
        if e["ym"]:
            idx = month_index(e["ym"])
            if idx is None:
                add("短条目格式", f"{where} 年月 {e['ym']} 的月份不对：{snippet(s)}")
            elif idx > now_idx:
                add("短条目格式", f"{where} 年月 {e['ym']} 比现在还晚：{snippet(s)}")
            elif now_idx - idx >= STALE_MONTHS:
                add("半年没更新（逐条问用户还成立吗）",
                    f"{where}（{e['ym']}，{now_idx - idx} 个月前）{snippet(e['body'], 60)}")
    if is_expr:
        named = sorted({v for v in voices if v})
        if len(named) >= 2:
            for v in named:
                k = voices.count(v)
                if k > SAMPLE_PER_VOICE_MAX:
                    add("5-表达样例", f"{rel} 样例「{v}」有 {k} 段，{SAMPLE_RULE}")
            if len(blocks) > SAMPLE_TOTAL_MAX:
                add("5-表达样例", f"{rel} 样例合计 {len(blocks)} 段，{SAMPLE_RULE}")
        elif len(blocks) > SAMPLE_ONE_VOICE_MAX:
            add("5-表达样例", f"{rel} 样例有 {len(blocks)} 段，{SAMPLE_RULE}")
        for b in blocks:
            text_lines = [t for _, t in b if "出处" not in t]
            n = len(re.sub(r"\s", "", "".join(text_lines)))
            where = f"{rel}:{b[0][0]}"
            if n > SAMPLE_LIMIT:
                add("5-表达样例", f"{where} 这段样例 {n} 字，超过 {SAMPLE_LIMIT}：{snippet(text_lines[0] if text_lines else '')}")
            if not any("出处" in t for _, t in b):
                add("5-表达样例", f"{where} 这段样例没注明出处：{snippet(text_lines[0] if text_lines else '')}")


def check_secrets(root: Path, add) -> None:
    """凭据扫描分两级：用户自己的原话区（03-流水/、04-项目/*/记录.md）命中只提醒；别处命中算问题。
    _填充进度.md、_已读清单.md 只扫备注，不扫材料名和路径。"""
    for p in scan_md_files(root):
        rel = p.relative_to(root).as_posix()
        for no, kinds in find_secrets(mask_for_scan(rel, read_text(p))):
            if secret_tier(rel) == "提醒":
                add(G_HINT, f"{rel}:{no} 疑似{'、'.join(kinds)}（内容不显示）。这是你自己的原话，我不改；"
                    "建议你自己看一眼")
            else:
                add(G_SECRET, f"{rel}:{no} 疑似{'、'.join(kinds)}（内容不显示；凭据一个字都不该记，请删掉这一行或这一段）")


def check_private_marks(root: Path, add) -> None:
    for p in entry_md_files(root):
        rel = p.relative_to(root).as_posix()
        is_material = rel.startswith(MATERIAL_DIR + "/")
        for no, line in enumerate(normalize_newlines(read_text(p)).split("\n"), 1):
            if "私密" not in line or line.strip().startswith("> 说明："):
                continue
            where = f"{rel}:{no}"
            if _PUBLIC_FIELD_RE.match(line):
                if line.strip() != "- 公开：私密":
                    add("私密标记写法", f"{where} 「公开」字段里有「私密」但写法不对，要一字不差写成 `- 公开：私密`")
                continue
            tail = line.rstrip()
            for m in _PRIVATE_BRACKET_RE.finditer(line):
                if is_material:
                    add("私密标记写法", f"{where} 素材条目不用括号标私密，写成字段 `- 公开：私密`")
                    break
                if m.group() == "[私密]" and m.end() == len(tail):
                    continue
                add("私密标记写法", f"{where} 私密标记写法不对，要在行尾写半角 `[私密]`，前后不带别的字或标点")
                break


def check_contradiction_marks(root: Path, add) -> None:
    d = root / WHO_DIR
    for p in md_files(d):
        rel = p.relative_to(root).as_posix()
        for no, line in enumerate(normalize_newlines(read_text(p)).split("\n"), 1):
            s = line.strip()
            if s.startswith(">"):
                continue
            if _CONTRA_RE.search(s):
                add("01 里的矛盾标注", f"{rel}:{no} 有「矛盾」标注。矛盾的两边都只写进观点库、在报告里请用户定，"
                    "01-我是谁 里两边都不写")


def cover_pronoun_hints(root: Path) -> list[str]:
    p = root / COVER_NAME
    if not p.is_file():
        return []
    out, inside = [], False
    for no, line in enumerate(normalize_newlines(read_text(p)).split("\n"), 1):
        if line.startswith("# "):
            inside = line[2:].strip() == "一页纸的我"
            continue
        s = line.strip()
        if not inside or not s or s.startswith(">"):
            continue
        if _PRONOUN_RE.search(s):
            out.append(f"{COVER_NAME}:{no} 有「他」或「她」作主语的句子；卷首一律用第一人称「我」")
    return out


_ENTRY_SUBJECT_RE = re.compile(r"(?:^|[。！？；]\s*)(他|她|用户(?!邮件|的|群|数|量|反馈|名|画像|增长|访谈|调研|需求|体验|协议))(?!人|乡|日|处|者|们)")


def entry_pronoun_hints(root: Path) -> list[str]:
    """01-我是谁、05-守则/协作偏好.md 的短条目：句首主语是「他」「她」「用户」的，提醒改成「我」（不算问题）。"""
    files = md_files(root / WHO_DIR) + [root / "05-守则/协作偏好.md"]
    out = []
    for p in files:
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        for no, line in enumerate(normalize_newlines(read_text(p)).split("\n"), 1):
            if not line.startswith("- "):
                continue
            body = parse_short_entry(line)["body"]
            if _ENTRY_SUBJECT_RE.search(body):
                out.append(f"{rel}:{no} 条目里用「他」「她」或「用户」作主语；条目一律用第一人称「我」")
    return out


def _said_line(line: str) -> bool:
    s = line.strip()
    if s.startswith("- 来源："):
        return bool(re.search(r"(?:^|[，：])\s*说过\s*$", s[len("- 来源："):]))
    if s.startswith(">") and "出处" in s:
        return s.rstrip().endswith("说过")
    if s.startswith("- "):
        src = parse_short_entry(s)["source"] or ""
        return bool(re.search(r"(?:^|，)\s*说过$", src))
    return False


def _lines(p: Path) -> list[str]:
    return normalize_newlines(read_text(p)).split("\n") if p.is_file() else []


_CONFIRMED_ONLY_RE = re.compile(r"[，（：]\s*已确认(?=，|）|\s*$)")


def _quote_blocks(lines: list[str]) -> list[tuple[int, list[str]]]:
    """连续的引用行（`>` 开头、不是 `> 说明：`）一块，即样例。返回 [(块首行下标, 行)]。"""
    out: list[tuple[int, list[str]]] = []
    cur: list[str] | None = None
    for i, l in enumerate(lines):
        s = l.strip()
        if s.startswith(">") and not s.startswith("> 说明："):
            if cur is None:
                cur = []
                out.append((i, cur))
            cur.append(l)
        else:
            cur = None
    return out


def _contains_run(hay: list[str], run: list[str]) -> bool:
    n = len(run)
    return any(hay[k:k + n] == run for k in range(len(hay) - n + 1))


def _entry_lines(e: dict) -> list[str]:
    ls = list(e["lines"])
    while ls and not ls[-1].strip():
        ls.pop()
    return ls


def _confirmed_source(src: str | None) -> bool:
    return bool(src) and bool(re.search(r"(?:^|[，：])\s*(?:已确认|说过)\s*$", src))


def mode1_backup(root: Path) -> tuple[Path | None, str | None]:
    """方式一进行中时进度里记的那份填充前备份：(备份路径或 None, 进度里写的名字或 None)。不在进行中返回 (None, None)。"""
    prog = root / PROGRESS_NAME
    if not prog.is_file():
        return None, None
    text = read_text(prog)
    if "- 状态：进行中" not in text:
        return None, None
    m = re.search(r"^- 填充前备份：(.+)$", text, re.M)
    if not m:
        return None, ""
    b = root / m.group(1).strip().rstrip("/")
    return (b if (b / "_清单.txt").is_file() else None), m.group(1).strip()


def check_mode1(root: Path, add) -> str | None:
    """方式一进行中：和进度里记的填充前备份比。返回用的是哪份备份（没比就 None）。
    备份不在了（被删了）：只提醒一句、不算问题，免得收工被卡死。"""
    b, named = mode1_backup(root)
    if b is None:
        if named == "":
            add(G_HINT, f"{PROGRESS_NAME} 里没有「填充前备份」一行，没法比对这次写了什么（不算问题）")
        elif named:
            add(G_HINT, f"进度里记的填充前备份 {named} 不在了，没法比对这次写了什么（不算问题；"
                "也没有能撤回到这次填充前的备份了）")
        return None
    for p in entry_md_files(root):
        rel = p.relative_to(root).as_posix()
        old, new = _lines(b / rel), _lines(p)
        sm = difflib.SequenceMatcher(None, old, new, autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                continue
            for j in range(j1, j2):
                if is_note_line(new[j]):
                    continue
                if _said_line(new[j]):
                    add(G_MODE1, f"{rel}:{j + 1} 方式一（读材料）写出了「说过」。读材料写的一律是待确认，"
                        "只有用户当面亲口说的才算说过")
                elif _CONFIRMED_ONLY_RE.search(new[j]):
                    add(G_MODE1, f"{rel}:{j + 1} 这次新写的行里是「已确认」。方式一只许写待确认，转正要等用户说")
            for i in range(i1, i2):
                if _CONFIRMED_TOKEN_RE.search(old[i]) and not is_note_line(old[i]):
                    add(G_MODE1, f"{rel} 备份里第 {i + 1} 行是已确认（或说过）的条目，这次被改或被删了。"
                        "已确认的条目不动；新料和它重合就只在报告里列一行，矛盾就另写一条待确认进观点库")
        if rel.startswith(MATERIAL_DIR + "/") and old:
            # 已确认的素材条目按整条比：从 ### 标题到下一个 ### 之前，任何一行变了都算动了
            _, old_entries = parse_material_entries("\n".join(old))
            _, new_entries = parse_material_entries("\n".join(new))
            new_by_head: dict[str, list[str]] = {}
            for e in new_entries:
                new_by_head.setdefault(e["heading"], _entry_lines(e))
            starts = [i for i, l in enumerate(old) if l.startswith("### ")]
            for k, e in enumerate(old_entries):
                if not _confirmed_source(e["fields"].get("来源")):
                    continue
                where = f"{rel} 备份里第 {starts[k] + 1} 行起的那条已确认素材"
                got = new_by_head.get(e["heading"])
                if got is None:
                    add(G_MODE1, f"{where}，这次标题被改了或整条被删了。已确认的条目不动")
                elif got != _entry_lines(e):
                    add(G_MODE1, f"{where}，这次有行被改了（正文也算）。已确认的条目整条不动")
        if rel == "01-我是谁/5-表达.md" and old:
            # 已确认的样例按整块比：引文行变了也算
            for i, blk in _quote_blocks(old):
                if any(_CONFIRMED_TOKEN_RE.search(l) for l in blk) and not _contains_run(new, blk):
                    add(G_MODE1, f"{rel} 备份里第 {i + 1} 行起的那段已确认样例，这次被改或被删了（引文行也算）。"
                        "已确认的样例一个字不改")
    for g in GUARD_FILES:
        old, new = _lines(b / g), _lines(root / g)
        if old != new:
            add(G_MODE1, f"{g} 和填充前备份不一样。方式一不改权限边界和公开边界：材料里看出的相关偏好写进 "
                "05-守则/协作偏好.md（待确认），并在报告里请用户自己决定要不要挪")
    old_cover = section_lines("\n".join(_lines(b / COVER_NAME)), "一页纸的我")
    old_body = [l.strip() for l in old_cover if l.strip() and not l.strip().startswith(">")]
    if old_body and not any(_DRAFT_PENDING_RE.match(l) for l in old_body):
        new_cover = section_lines("\n".join(_lines(root / COVER_NAME)), "一页纸的我")
        new_body = [l.strip() for l in new_cover if l.strip() and not l.strip().startswith(">")]
        if new_body != old_body:
            add(G_MODE1, f"{COVER_NAME}「一页纸的我」备份时已经没有待确认的起草行（用户确认或手改过），"
                "这次不该重写；把建议的改动写进报告「建议对卷首的改动」")
    return b.relative_to(root).as_posix()


def registered_tags(root: Path, add) -> set[str] | None:
    spec = root / SPEC_NAME
    if not spec.exists():
        add("标签清单", f"缺少 {SPEC_NAME}，没法核对标签清单")
        return None
    lines = normalize_newlines(read_text(spec)).split("\n")
    start = next((i for i, l in enumerate(lines) if re.match(r"^## .*标签清单", l)), None)
    if start is None:
        add("标签清单", f"{SPEC_NAME} 里找不到「标签清单」一节")
        return None
    tags = set()
    for l in lines[start + 1:]:
        if l.startswith("## ") or l.startswith("# "):
            break
        if l.strip().startswith("- #"):
            tags.add(l.strip()[2:].strip())
    return tags


def main() -> int:
    ap = argparse.ArgumentParser(description="检查上下文格式（只读）")
    ap.add_argument("--root", help="上下文根目录，不给就读 config.json")
    args = ap.parse_args()
    root = load_root(args.root)
    if not root.is_dir():
        print(f"上下文根目录不存在：{root}（先跑 scaffold.py）", file=sys.stderr)
        return 1
    found: dict[str, list[str]] = {g: [] for g in GROUPS}

    hints: list[str] = []

    def add(group: str, msg: str) -> None:
        if group == G_HINT:
            hints.append(msg)
        else:
            found[group].append(msg)

    t = now()
    now_idx = t.year * 12 + t.month
    backup = (root / BACKUP_DIR).resolve()

    for p in sorted(root.rglob("*.md")):
        rel = p.relative_to(root)
        if ".git" in rel.parts:
            continue
        try:
            p.resolve().relative_to(backup)
            continue
        except ValueError:
            pass
        if has_bom(p):
            add("编码", f"带 BOM：{rel.as_posix()}")

    cover = root / COVER_NAME
    if not cover.exists():
        add("卷首", f"缺少 {COVER_NAME}")
    else:
        text = normalize_newlines(read_text(cover))
        n = body_chars(section_lines(text, "一页纸的我"))
        if n > 500:
            add("卷首", f"「一页纸的我」正文 {n} 字，超过 500")
        nav = parse_nav(text)
        if not nav:
            add("导航表", "导航表为空或解析不到")
        for row in nav:
            for rel in row["required"] + row["optional"]:
                if not (root / rel).exists():
                    add("导航表", f"「{row['task']}」指向不存在的路径：{rel}")
                elif not (rel.endswith(".md") or rel.endswith("/")):
                    add("导航表", f"「{row['task']}」的路径 {rel} 写法不对：文件要带 .md，文件夹要以 / 结尾")
        listed = set()
        for row in parse_status(text):
            rel = row["path"]
            listed.add(rel)
            p = root / rel
            if not p.is_file():
                add("章节状态表", f"指向不存在的文件：{rel}")
                continue
            if row["status"] not in ("空", "有内容"):
                add("章节状态表", f"状态写法不对：{rel} → {row['status']}（只能是「空」或「有内容」）")
            else:
                n_lines = len(content_lines(read_text(p)))
                if row["status"] == "空" and n_lines:
                    add("章节状态表", f"{rel} 表里写「空」，但文件里说明行之外已有 {n_lines} 行内容")
                if row["status"] == "有内容" and not n_lines:
                    add("章节状态表", f"{rel} 表里写「有内容」，但文件里除了说明行什么都没有")
                if row["status"] == "有内容" and row["updated"] in ("—", "-", ""):
                    add("章节状态表", f"{rel} 写了「有内容」，最后更新却是「{row['updated'] or '空白'}」")
            if not (row["updated"] == "—" or re.fullmatch(r"\d{4}-\d{2}", row["updated"])):
                add("章节状态表", f"{rel} 最后更新写法不对：{row['updated'] or '空白'}（写 YYYY-MM 或 —）")
        for p in chapter_files(root):
            rel = p.relative_to(root).as_posix()
            if rel not in listed:
                add("章节状态表", f"实际有章节文件 {rel}，表里没有这一行。补登记（只加一行，不改内容）："
                    f"{PY} \"{(SKILL_DIR / 'scripts' / 'meta.py').as_posix()}\""
                    + (f" --root \"{args.root}\"" if args.root else "") + f" --chapter \"{rel}\"")

    used_tags: dict[str, str] = {}
    for p in md_files(root / MATERIAL_DIR):
        rel = p.relative_to(root).as_posix()
        _, entries = parse_material_entries(read_text(p))
        if len(entries) > SPLIT_LIMIT:
            add("素材库长度", f"{rel} 有 {len(entries)} 条，超过约 {SPLIT_LIMIT} 条，该按年拆分（用户同意再拆）")
        for i, e in enumerate(entries, 1):
            title = e["title"]
            private = "私密" in (e["fields"].get("公开") or "") or is_private_line(title) or                 bool(secret_kinds(e["fields"].get("来源") or ""))
            where = f"{rel} 第 {i} 条（私密或含疑似凭据，不显示标题）" if private else f"{rel}「{snippet(title, 30)}」"
            if not title.startswith("标题："):
                add("素材条目", f"{where} 标题没以「标题：」开头")
            for f in MATERIAL_FIELDS:
                if not e["fields"].get(f):
                    add("素材条目", f"{where} 缺 {f}")
            d = e["fields"].get("日期")
            if d and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
                add("素材条目", f"{where} 日期写法不对：{d}（写 YYYY-MM-DD）")
            lv = e["fields"].get("公开")
            if lv and lv not in ("可公开", "仅助理", "私密"):
                add("素材条目", f"{where} 公开级别写法不对：{lv}（可公开 / 仅助理 / 私密 三选一）")
            src = e["fields"].get("来源")
            dups = dup_titles(src)
            if dups:
                add("素材条目", f"{where} 来源里同一份材料写了不止一次（{'、'.join('《' + d + '》' for d in dups)}），"
                    "同一份材料读两遍不算两处出处")
            if src and not material_source_ok(src):
                add("素材条目", f"{where} 来源写法不对：{'（不显示）' if private else snippet(src, 30)}"
                    "（写成 《文件名》，待确认 / 《文件名》，已确认 / 归纳自《文件名》，待确认 / "
                    "访谈，说过 / 归纳自访谈，待确认）")
            for tg in (e["fields"].get("标签") or "").split():
                if not tg.startswith("#") or len(tg) == 1:
                    add("素材条目", f"{where} 标签 {tg} 写法不对（以 # 开头，空格隔开）")
                    continue
                used_tags.setdefault(tg, where)

    for p in short_entry_files(root):
        check_short_file(root, p, now_idx, add)

    check_secrets(root, add)
    check_private_marks(root, add)
    check_contradiction_marks(root, add)
    compared = check_mode1(root, add)
    hints += cover_pronoun_hints(root) + entry_pronoun_hints(root)

    reg = registered_tags(root, add)
    if reg is not None:
        for tg in sorted(set(used_tags) - reg):
            add("标签清单", f"用了但清单里没登记：{tg}（首次出现在 {used_tags[tg]}）")
        for tg in sorted(reg - set(used_tags)):
            add("标签清单", f"清单里登记了但没有素材条目在用：{tg}")

    # ---- 待确认（不算问题，单列）----
    pend: list[tuple[str, int]] = []
    for p in entry_md_files(root):
        k = len(pending_lines(read_text(p)))
        if k:
            pend.append((p.relative_to(root).as_posix(), k))
    unfinished = False
    prog = root / PROGRESS_NAME
    if prog.is_file() and "- 状态：进行中" in read_text(prog):
        unfinished = True

    total = sum(len(v) for v in found.values())
    print(f"上下文根目录：{root}")
    print(f"现在：{t:%Y-%m}（{tz_label()}；「半年没更新」按这个算）")
    n_pend = sum(k for _, k in pend)
    if n_pend:
        print(f"待确认 {n_pend} 条（还没转正；用户看过后说「全部转正」，跑 confirm.py）：")
        for rel, k in pend:
            print(f"  {rel}：{k} 条")
    else:
        print("待确认 0 条。")
    if unfinished:
        print(f"注意：{PROGRESS_NAME} 显示有一次填充还没做完（progress.py status 看还剩哪些）。")
    if compared:
        print(f"方式一进行中：已和填充前备份 {compared}/ 逐文件比过（说过、守则、已确认条目、卷首）。")
    if hints:
        print(f"提醒 {len(hints)} 条（不算问题，退出码不因此变 1）：")
        for h in hints:
            print(f"  - {h}")
    if not total:
        print("没有发现格式问题。")
        return 0
    groups = [g for g in GROUPS if found[g]]
    print(f"发现 {total} 个问题，分 {len(groups)} 组：")
    for g in groups:
        print(f"\n【{g}】{len(found[g])} 个")
        for x in found[g]:
            print(f"  - {x}")
    return 1


if __name__ == "__main__":
    run_main(main)
