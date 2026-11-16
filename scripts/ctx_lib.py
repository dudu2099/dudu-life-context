"""关于我的上下文：脚本共用的小工具。只用 Python 标准库。

- 上下文文件夹位置只在本技能目录的 config.json 里定义一处（脚本都能用 --root 覆盖）。
  环境变量 LIFE_CONTEXT_CONFIG 可以指向另一份 config（测试或多份上下文时用）。
- 时间一律按 config 里的 timezone 算；留空就用系统本地时间。
- 文件一律 UTF-8 无 BOM、LF 换行读写。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = SKILL_DIR / "templates" / "context"
SPEC_NAME = "_规范.md"
COVER_NAME = "00-卷首.md"
REPORT_NAME = "_填充报告.md"
PROGRESS_NAME = "_填充进度.md"
READLIST_NAME = "_已读清单.md"
APPENDIX_DIR = "99-附录"
BACKUP_DIR = "99-附录/_填充前备份"
REPORT_ARCHIVE_DIR = "99-附录/_历次报告"
RAW_DIR = "99-附录/_原始材料"
OLD_SPEC_DIR = "99-附录/_旧规范"
UNDO_LOG_NAME = "_撤回记录.md"  # 放在 BACKUP_DIR 里：撤回不动备份文件夹，所以这份记录不会被撤回抹掉
SKIP_DIRS = {".git", ".obsidian", ".trash", "node_modules", "__pycache__"}
# 守则里只有用户本人能改的两份（方式一读材料时不得改）
GUARD_FILES = ("05-守则/权限边界.md", "05-守则/公开边界.md")
FLOW_DIR = "03-流水"
PROJECT_DIR = "04-项目"
# 打印给用户看的命令提示里用的解释器名：就用现在跑脚本的这个（python3 或 python）
PY = Path(sys.executable).stem if sys.executable else "python3"


class UserFacingError(Exception):
    """要原样告诉用户的错误（不打印堆栈）。"""


def setup_io() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except AttributeError:
            pass


def run_main(main) -> None:
    """统一入口：配好 utf-8 输出，把 UserFacingError 变成一段清楚的提示和退出码 2。"""
    setup_io()
    try:
        code = main()
    except UserFacingError as e:
        print(str(e), file=sys.stderr)
        code = 2
    sys.exit(code or 0)


# ---------- 配置 ----------

def config_path() -> Path:
    env = os.environ.get("LIFE_CONTEXT_CONFIG")
    return Path(env).expanduser() if env else SKILL_DIR / "config.json"


def load_config() -> dict | None:
    p = config_path()
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_bytes().decode("utf-8-sig"))
    except (ValueError, OSError) as e:
        raise UserFacingError(f"config.json 读不出来（{p}）：{e}\n"
                              "请检查它是不是合法的 JSON，或删掉后重新跑 setup.py。")


NO_CONFIG_HINT = (
    "还没有配置上下文文件夹放在哪里（找不到 {path}）。\n"
    "请先问用户：上下文文件夹想放在哪（一个新的空文件夹最好），时区是什么（不知道可以留空，用系统时间）。\n"
    "然后运行：{py} \"{skill}/scripts/setup.py\" --root \"<文件夹路径>\" [--timezone Asia/Shanghai]\n"
    "临时测试可以不建配置，每条命令加 --root \"<测试目录>\"。"
)


def load_root(override: str | None = None) -> Path:
    """上下文根目录：有 --root 用它，否则读 config.json 的 context_root。"""
    if override:
        return Path(override).expanduser()
    cfg = load_config()
    if cfg is None:
        raise UserFacingError(NO_CONFIG_HINT.format(path=config_path(), skill=SKILL_DIR.as_posix(),
                                                    py=PY))
    root = (cfg.get("context_root") or "").strip()
    if not root:
        raise UserFacingError(f"config.json 里的 context_root 是空的（{config_path()}）。"
                              "请问用户上下文文件夹放哪，再跑 setup.py --force 重新生成。")
    return Path(root).expanduser()


# ---------- 时间 ----------

# 没有时区库（Windows 上 zoneinfo 常缺 tzdata）时的退路：只收常年不变、没有夏令时的时区。
FIXED_OFFSETS = {
    "UTC": 0, "Etc/UTC": 0, "GMT": 0, "Etc/GMT": 0,
    "Asia/Shanghai": 480, "Asia/Chongqing": 480, "Asia/Harbin": 480, "Asia/Urumqi": 360,
    "Asia/Hong_Kong": 480, "Asia/Macau": 480, "Asia/Taipei": 480, "Asia/Singapore": 480,
    "Asia/Kuala_Lumpur": 480, "Asia/Manila": 480, "Asia/Tokyo": 540, "Asia/Seoul": 540,
    "Asia/Bangkok": 420, "Asia/Ho_Chi_Minh": 420, "Asia/Jakarta": 420,
    "Asia/Kolkata": 330, "Asia/Calcutta": 330, "Asia/Dubai": 240, "Asia/Karachi": 300,
    "Asia/Dhaka": 360, "Asia/Kathmandu": 345,
}
_OFFSET_RE = re.compile(r"^(?:UTC|GMT)?\s*([+-])(\d{1,2})(?::?(\d{2}))?$", re.I)


def _fixed(minutes: int, label: str) -> tzinfo:
    return timezone(timedelta(minutes=minutes), name=label)


def resolve_timezone(name: str | None) -> tuple[tzinfo | None, str, str | None]:
    """返回 (tzinfo 或 None 表示系统本地, 说明文字, 警告或 None)。"""
    name = (name or "").strip()
    if not name:
        return None, "系统本地时间", None
    m = _OFFSET_RE.match(name)
    if m:
        sign = 1 if m.group(1) == "+" else -1
        minutes = sign * (int(m.group(2)) * 60 + int(m.group(3) or 0))
        if abs(minutes) <= 14 * 60:
            return _fixed(minutes, name), name, None
    try:
        from zoneinfo import ZoneInfo  # Python 3.9+
        return ZoneInfo(name), name, None
    except Exception:  # 名字不对，或本机缺 tzdata
        pass
    if name in FIXED_OFFSETS:
        return _fixed(FIXED_OFFSETS[name], name), f"{name}（本机没有时区库，按固定偏移算）", None
    warn = (f"注意：时区「{name}」认不出来（名字不对，或本机缺时区数据），这次改用系统本地时间。"
            "请把 config.json 的 timezone 改成 IANA 名（如 Asia/Shanghai）或 UTC+8 这种写法，或者留空。")
    return None, "系统本地时间", warn


_warned = False


def now() -> datetime:
    """按配置时区的当前时间（带时区信息）。"""
    global _warned
    cfg = None
    try:
        cfg = load_config()
    except UserFacingError:
        cfg = None
    tz, _, warn = resolve_timezone((cfg or {}).get("timezone"))
    if warn and not _warned:
        print(warn, file=sys.stderr)
        _warned = True
    return datetime.now(tz) if tz else datetime.now().astimezone()


def tz_label() -> str:
    try:
        cfg = load_config()
    except UserFacingError:
        cfg = None
    _, label, _ = resolve_timezone((cfg or {}).get("timezone"))
    return label


# ---------- 读写 ----------

def read_text(path: Path) -> str:
    """UTF-8 读取；带 BOM 也能读（BOM 由 check_format 单独报告）。"""
    return path.read_bytes().decode("utf-8-sig")


def write_text(path: Path, text: str) -> None:
    """UTF-8 无 BOM、LF 换行写入。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(normalize_newlines(text).encode("utf-8"))


def has_bom(path: Path) -> bool:
    return path.read_bytes()[:3] == b"\xef\xbb\xbf"


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def is_note_line(line: str) -> bool:
    """`> 说明：` 开头的说明行（给读者的说明，不是关于用户的信息）。"""
    return line.strip().startswith("> 说明：")


def content_lines(text: str) -> list[str]:
    """真正的内容行：去掉空行、标题行和 `> 说明：` 行。"""
    out = []
    for line in normalize_newlines(text).split("\n"):
        s = line.strip()
        if not s or s.startswith("#") or is_note_line(s):
            continue
        out.append(line)
    return out


def context_md_files(root: Path, include_meta: bool = False) -> list[Path]:
    """上下文里的 .md：不进 .git，不进 99-附录；默认跳过根目录的规范、报告、进度文件。"""
    out = []
    meta = {SPEC_NAME, REPORT_NAME, PROGRESS_NAME, READLIST_NAME}
    for p in sorted(root.rglob("*.md")):
        parts = p.relative_to(root).parts
        if any(x in SKIP_DIRS for x in parts) or parts[0] == APPENDIX_DIR:
            continue
        if not include_meta and len(parts) == 1 and p.name in meta:
            continue
        out.append(p)
    return out


def is_raw_record(rel: str) -> bool:
    """放原话、不放条目的文件：03-流水/ 整个文件夹，04-项目/<项目>/记录.md。"""
    parts = rel.split("/")
    if parts[0] == FLOW_DIR:
        return True
    return len(parts) == 3 and parts[0] == PROJECT_DIR and parts[2] == "记录.md"


def entry_md_files(root: Path) -> list[Path]:
    """放条目的 .md：context_md_files 再去掉流水和项目记录（那里是原话，「待确认」三个字不是状态）。"""
    return [p for p in context_md_files(root) if not is_raw_record(p.relative_to(root).as_posix())]


def scan_md_files(root: Path) -> list[Path]:
    """凭据扫描的范围：除 .git 和 99-附录/_填充前备份/ 以外的所有 .md（报告、进度、附录都算）。"""
    out = []
    backup = (root / BACKUP_DIR).resolve()
    for p in sorted(root.rglob("*.md")):
        rel = p.relative_to(root)
        if any(x in SKIP_DIRS for x in rel.parts):
            continue
        try:
            p.resolve().relative_to(backup)
            continue
        except ValueError:
            pass
        out.append(p)
    return out


def secret_tier(rel: str) -> str:
    """凭据扫描里这份文件的分级：「提醒」= 用户自己的原话区（03-流水/、04-项目/*/记录.md），命中只提醒，
    不算问题、不拦收工（那是用户的原话，助理不改）；其余「问题」。"""
    return "提醒" if is_raw_record(rel) else "问题"


_PROG_ITEM_RE = re.compile(r"^- \[[ x\-]\] 《.+?》 `.*?`(?: —— (.*))?$")
_READ_ITEM_RE = re.compile(r"^- 《.+?》 `[0-9a-f]{16}` \d+ 字节 —— 读于 .+$")


def mask_for_scan(rel: str, text: str) -> str:
    """扫凭据之前，把脚本写的材料名和路径换掉（行号不变）：`_填充进度.md` 每一项只留备注，
    `_已读清单.md` 每一项整行不扫。材料叫「密码：备忘.txt」这种名字，不该算凭据。"""
    if rel not in (PROGRESS_NAME, READLIST_NAME):
        return text
    out = []
    for line in normalize_newlines(text).split("\n"):
        m = _PROG_ITEM_RE.match(line.strip()) if rel == PROGRESS_NAME else None
        if m:
            out.append(m.group(1) or "")
        elif rel == READLIST_NAME and _READ_ITEM_RE.match(line.strip()):
            out.append("")
        else:
            out.append(line)
    return "\n".join(out)


# ---------- 疑似凭据、写给 AI 的指令（check_format、inventory、save_raw 共用一套） ----------
# 只报「哪一行、哪一类」，任何时候都不回显命中的内容。
# 改这里的规则之前先跑回归：在本技能目录下 python3 -m unittest tests.test_secrets -v
#
# 口令类的思路：先认关键词（中文「密码」「口令」「密钥」「私钥」「PIN 码」，英文 password / pwd / pw / secret /
# token / api_key……必须是一个完整的词，tokens、max_tokens、password_hash、token_count 这类不算），再看后面跟的值
# 「像不像凭据」：没有空白、不是中文、不像路径/函数调用/占位符、够长；英文关键词还要求字母和数字（或符号）混着。

_A = r"(?<![0-9A-Za-z])"   # 左边不是字母数字
_Z = r"(?![0-9A-Za-z])"    # 右边不是字母数字
_ID_RE = re.compile(_A + r"[1-9]\d{5} ?(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01]) ?\d{3}[\dXx]" + _Z)
_CARD_RE = re.compile(_A + r"(?:\d{13,19}|\d{4}(?:[ -]\d{4}){2,3}(?:[ -]?\d{1,3})?)" + _Z)
_KEY_RES = [
    re.compile(_A + r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(_A + r"AKIA[0-9A-Z]{16}" + _Z),
    re.compile(_A + r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(_A + r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(_A + r"glpat-[A-Za-z0-9_\-]{20,}"),
    re.compile(_A + r"AIza[0-9A-Za-z_\-]{30,}"),
    re.compile(_A + r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"(?i)" + _A + r"bearer\s+(?=[A-Za-z0-9\-._~+/]*\d)[A-Za-z0-9\-._~+/]{16,}"),
    re.compile(_A + r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\."),          # JWT
    re.compile(r"hooks\.slack\.com/services/T[A-Za-z0-9]+/B[A-Za-z0-9]+/[A-Za-z0-9]{8,}"),
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
]
# 英文关键词必须在一个标识符的结尾（前面可以带 db_、OPENAI_ 这类前缀，后面不能再接字母数字、_ 或 -）
_EN_KW = (r"(?:password|passwd|passcode|passphrase|pwd|pw|secret|token|api[_\-]?key|apikey|"
          r"access[_\-]?key|private[_\-]?key|client[_\-]?secret)")
_EN_KW_RE = re.compile(r"(?<![A-Za-z0-9_\-])[A-Za-z0-9_\-]*?" + _EN_KW + r"(?![A-Za-z0-9_\-])", re.I)
_ZH_KW_RE = re.compile(r"(?:密\s*码|口\s*令|密\s*钥|私\s*钥|PIN\s*码)", re.I)
# 关键词后面的连接：冒号 / 等号；「是」「为」「改成了」这类动词（英文还认 is）；或者只隔空白
_SEP_COLON_RE = re.compile(r"^\s*[\"'`]?\s*[:：=＝]\s*")
_SEP_VERB_RE = re.compile(r"^\s*(?:都)?(?:改成了?|改为|换成了?|设成了?|设为|设置为|设置成|是|为)\s*")
_SEP_IS_RE = re.compile(r"^\s+is\s+", re.I)
_SEP_SPACE_RE = re.compile(r"^[ \t　]+")
_VALUE_RE = re.compile(r"^[\"'`]?([^\s\"'`，。；、！？,;|（）()「」『』<>【】]+)")
_PLACEHOLDER_RE = re.compile(r"^(?:x{3,}|\*+|\.{3,}|…+|<.*>|\{.*\}|\$.*|%.*%|null|none|nil|true|false|"
                             r"changeme|placeholder|example|your[_\-]?\w*)$", re.I)
# SSN 只在附近有 SSN / social security 字样时才报（单独的 123-45-6789 太常见：电话、编号、玩笑）
_SSN_RE = re.compile(r"(?i)(?:(?<![A-Za-z])SSN(?![A-Za-z])|social\s+security(?:\s+(?:number|no\.?|#))?|社会安全号)"
                     r"\D{0,20}?(?<![\d-])\d{3}-\d{2}-\d{4}(?![\d-])")
_TABLE_KW_RE = re.compile(r"密\s*码|口\s*令|密\s*钥|私\s*钥|PIN\s*码|(?<![A-Za-z0-9_\-])[A-Za-z0-9_\-]*?"
                          + _EN_KW + r"(?![A-Za-z0-9_\-])", re.I)

SECRET_KINDS = ("证件号", "卡号", "密钥", "口令", "SSN")


def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def credential_like(v: str, min_len: int = 6, need_mix: bool = True) -> bool:
    """这个值像不像凭据：没有空白、全是半角可见字符（不是中文）、不像路径/网址/函数调用/占位符、够长；
    need_mix 时还要字母和数字（或符号）混着，纯数字要 6 位以上。"""
    v = v.strip().strip("\"'`").rstrip(".,;:!?)")
    if len(v) < min_len or not re.fullmatch(r"[!-~]+", v) or not re.search(r"[A-Za-z0-9]", v):
        return False
    if re.match(r"^(?:[/~\\]|\.\.?/|[A-Za-z]:[\\/]|https?://)", v):       # 路径、网址
        return False
    if re.match(r"^[A-Za-z_][A-Za-z0-9_.]*\(", v):                       # 函数调用，如 hash(pw)
        return False
    if _PLACEHOLDER_RE.match(v):
        return False
    if not need_mix:
        return True
    has_l, has_d = bool(re.search(r"[A-Za-z]", v)), bool(re.search(r"\d", v))
    has_s = bool(re.search(r"[^A-Za-z0-9]", v))
    if has_d and not has_l and not has_s:
        return len(v) >= 6
    return (has_l and has_d) or (has_l and has_s) or (has_d and has_s)


def _value_after(rest: str) -> str | None:
    m = _VALUE_RE.match(rest)
    return m.group(1) if m else None


def _assign_hit(line: str) -> bool:
    """「密码：xxx」「密码是 xxx」「X 的密码改成了 xxx」「密码 xxx」「password=xxx」「pw: xxx」这类口令写法。"""
    for m in _ZH_KW_RE.finditer(line):
        rest = line[m.end():]
        s = _SEP_COLON_RE.match(rest) or _SEP_VERB_RE.match(rest)
        if s:   # 冒号、等号、「是」：值 4 位以上、不是中文就算
            v = _value_after(rest[s.end():])
            if v and credential_like(v, 4, False):
                return True
            continue
        s = _SEP_SPACE_RE.match(rest)
        if s:   # 只隔空白：值要 6 位以上、带数字（「密码 manager」不算）
            v = _value_after(rest[s.end():])
            if v and re.search(r"\d", v) and credential_like(v, 6, True):
                return True
    for m in _EN_KW_RE.finditer(line):
        rest = line[m.end():]
        s = _SEP_COLON_RE.match(rest) or _SEP_VERB_RE.match(rest) or _SEP_IS_RE.match(rest)
        if s:
            v = _value_after(rest[s.end():])
            if v and credential_like(v, 6, True):
                return True
    return False


def _cells(line: str) -> list[str] | None:
    s = line.strip()
    if not (s.startswith("|") and s.count("|") >= 2):
        return None
    return [c.strip() for c in s.strip("|").split("|")]


def _is_sep_row(cells: list[str]) -> bool:
    return any(cells) and all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c)


def _kw_cell(c: str) -> bool:
    """一格是不是「密码」这类栏名：含关键词，而且很短（像标签，不像一句话）。"""
    return bool(_TABLE_KW_RE.search(c)) and len(c) <= 16 and not re.search(r"[。，；！？]", c)


def _table_row_label_hit(line: str) -> bool:
    """表格一行里「| 淘宝密码 | hunter2 |」：某格是密码类栏名，紧挨着的下一格像凭据。"""
    cells = _cells(line)
    if not cells or len(cells) < 2:
        return False
    return any(_kw_cell(cells[i]) and credential_like(cells[i + 1], 6, True) for i in range(len(cells) - 1))


def secret_kinds(line: str) -> list[str]:
    """这一行里疑似凭据的种类（可能多种），没有就空列表。表格里「密码」一列的值要整张表看，见 find_secrets。"""
    kinds = []
    if _ID_RE.search(line):
        kinds.append("证件号")
    for m in _CARD_RE.finditer(line):
        digits = re.sub(r"\D", "", m.group())
        if 13 <= len(digits) <= 19 and _luhn_ok(digits) and not _ID_RE.fullmatch(m.group()):
            kinds.append("卡号")
            break
    if any(r.search(line) for r in _KEY_RES):
        kinds.append("密钥")
    if _assign_hit(line) or _table_row_label_hit(line):
        kinds.append("口令")
    if _SSN_RE.search(line):
        kinds.append("SSN")
    return kinds


def find_secrets(text: str) -> list[tuple[int, list[str]]]:
    """[(行号, [种类...])]。只给位置和种类，不给内容。
    除逐行规则外还看 Markdown 表格：表头某一列是「密码」这类栏名时，下面各行这一列的值像凭据（4 位以上、
    不是中文）就报。"""
    out = []
    pw_cols: list[int] | None = None   # 当前表格里「密码」类栏的列号；None 表示不在表格里
    for no, line in enumerate(normalize_newlines(text).split("\n"), 1):
        k = secret_kinds(line)
        cells = _cells(line)
        if cells is None:
            pw_cols = None
        elif pw_cols is None:          # 表格第一行是表头
            pw_cols = [i for i, c in enumerate(cells) if _kw_cell(c)]
        elif not _is_sep_row(cells) and "口令" not in k:
            if any(i < len(cells) and credential_like(cells[i], 4, False) for i in pw_cols):
                k = k + ["口令"]
        if k:
            out.append((no, k))
    return out


_INJECT_RES = [
    re.compile(r"(?:忽略|无视|忘掉|忘记|不要理会)(?:掉)?(?:你)?(?:以上|上面|上述|之前|此前|前面|先前|原来)?的?"
               r"(?:所有|全部|一切)?的?(?:指令|指示|提示词?|设定|规则|要求)"),
    re.compile(r"\b(?:ignore|disregard|forget)\s+(?:all\s+|any\s+)?(?:the\s+|your\s+)?"
               r"(?:previous|prior|above|earlier|preceding)\s+(?:instructions?|prompts?|rules)", re.I),
    re.compile(r"system\s*prompt|系统提示词|开发者模式|developer\s+mode", re.I),
    re.compile(r"你现在是(?!不是)|从现在(?:开始|起)，?你(?:是|要)|you\s+are\s+now\b|act\s+as\s+(?:an?\s+)?(?:ai|assistant)", re.I),
    re.compile(r"(?:请|立即|马上)?(?:执行|运行)(?:一下)?(?:以下|下面|这条|这段|如下)的?(?:命令|指令|脚本|代码)"),
    re.compile(r"\b(?:run|execute)\s+(?:the\s+)?(?:following|this|these)\s+(?:commands?|scripts?)", re.I),
    re.compile(r"\brm\s+-[a-z]*r[a-z]*f|\bcurl\s+[^|\n]*\|\s*(?:ba|z)?sh\b|\bwget\s+[^|\n]*\|\s*(?:ba|z)?sh\b", re.I),
    re.compile(r"<!--\s*(?:system|assistant|ai)\b", re.I),
    re.compile(r"(?:写进|写入|加到|改成).{0,20}(?:权限边界|公开边界).{0,40}(?:说过|已确认)|"
               r"(?:来源|状态)(?:写|标)(?:成|为)?「?(?:说过|已确认)"),
]


def has_injection(text: str) -> bool:
    """材料里有没有像是写给 AI 的指令（粗筛，只当提醒）。"""
    return any(r.search(text) for r in _INJECT_RES)


def file_sha(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


# ---------- 卷首表格解析 ----------

_PATH_RE = re.compile(r"`([^`]+)`")


def section_lines(text: str, heading: str) -> list[str]:
    """一级标题 `# heading` 下面、到下一个一级标题之前的行。"""
    out, inside = [], False
    for line in normalize_newlines(text).split("\n"):
        if line.startswith("# "):
            inside = line[2:].strip() == heading
            continue
        if inside:
            out.append(line)
    return out


def _table_rows(lines: list[str]) -> list[list[str]]:
    rows = []
    for line in lines:
        s = line.strip()
        if not (s.startswith("|") and s.endswith("|")):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
            continue
        rows.append(cells)
    return rows[1:] if rows else []


def parse_nav(text: str) -> list[dict]:
    result = []
    for cells in _table_rows(section_lines(text, "导航")):
        if len(cells) < 3:
            continue
        result.append({"task": cells[0], "required": _PATH_RE.findall(cells[1]),
                       "optional": _PATH_RE.findall(cells[2])})
    return result


def parse_status(text: str) -> list[dict]:
    result = []
    for cells in _table_rows(section_lines(text, "章节状态")):
        if len(cells) < 3:
            continue
        paths = _PATH_RE.findall(cells[0])
        result.append({"path": paths[0] if paths else cells[0], "status": cells[1],
                       "updated": cells[2]})
    return result


# ---------- 条目解析 ----------
# 来源和状态的写法以 `_规范.md`「六、条目格式」「七、待确认和已确认」为准。

STATUS_PENDING = "待确认"
STATUS_CONFIRMED = "已确认"
LEVEL_MARKS = ("可公开", "私密")
MATERIAL_FIELDS = ("日期", "标签", "公开", "来源")

# 书名号出处，后面可以紧跟一个中文括号备注：《某文》（只取本人批注）
_TITLES = r"(?:《[^《》]+》(?:（[^（）]*）)?)+"
# 短条目括号里年月之前的来源
SHORT_SOURCE_RES = [
    re.compile(r"^说过$"),
    re.compile(rf"^提炼自{_TITLES}，(待确认|已确认)$"),
    re.compile(rf"^归纳自{_TITLES}，(待确认|已确认)$"),
    re.compile(r"^归纳自访谈，(待确认|已确认)$"),
    re.compile(r"^默认值，待用户调整$"),
]
# 素材条目「来源：」字段
MATERIAL_SOURCE_RES = [
    re.compile(rf"^(?:归纳自|提炼自)?{_TITLES}，(待确认|已确认)$"),   # 素材来源写不写「提炼自」都认
    re.compile(r"^访谈，(说过|待确认|已确认)$"),
    re.compile(r"^归纳自访谈，(待确认|已确认)$"),
    re.compile(r"^说过$"),
]
# 「待确认」作为状态出现的位置：前面是中文逗号，后面是逗号、右括号或行尾
PENDING_TOKEN_RE = re.compile(r"，待确认(?=，|）|\s*$)")

_TAIL_MARK_RE = re.compile(r"(?<!\])\[(?!\[)([^\[\]]*)\]\s*$")
_YM_RE = re.compile(r"\d{4}-\d{2}")
_FIELD_RE = re.compile(r"^- (日期|标签|公开|来源)：(.*)$")


def short_source_ok(source: str | None) -> bool:
    return bool(source) and any(r.match(source) for r in SHORT_SOURCE_RES)


def material_source_ok(source: str | None) -> bool:
    return bool(source) and any(r.match(source) for r in MATERIAL_SOURCE_RES)


def pending_lines(text: str) -> list[tuple[int, str]]:
    """带「待确认」状态的行（跳过说明行）。返回 [(行号, 原行)]。"""
    out = []
    for no, line in enumerate(normalize_newlines(text).split("\n"), 1):
        if is_note_line(line):
            continue
        if PENDING_TOKEN_RE.search(line):
            out.append((no, line))
    return out


def _tail_paren_start(s: str) -> int | None:
    """行尾是「）」时，找与它配对的「（」的位置（来源括号里可以再嵌一个备注括号）。"""
    s = s.rstrip()
    if not s.endswith("）"):
        return None
    depth = 0
    for i in range(len(s) - 1, -1, -1):
        if s[i] == "）":
            depth += 1
        elif s[i] == "（":
            depth -= 1
            if depth == 0:
                return i
    return None


def parse_short_entry(line: str) -> dict:
    """解析一行短条目 `- 正文（来源，YYYY-MM）[标记]`。"""
    s = line.strip()
    if s.startswith("- "):
        s = s[2:]
    mark = None
    m = _TAIL_MARK_RE.search(s)
    if m:
        mark = m.group(1).strip()
        s = s[:m.start()].rstrip()
    paren = source = ym = None
    start = _tail_paren_start(s)
    if start is not None:
        paren = s[start + 1:s.rstrip().rfind("）")].strip()
        s = s[:start].rstrip()
        parts = paren.split("，")
        if len(parts) >= 2 and _YM_RE.fullmatch(parts[-1].strip()):
            ym = parts[-1].strip()
            source = "，".join(parts[:-1]).strip()
        else:
            source = paren
    return {"body": s, "mark": mark, "paren": paren, "source": source, "ym": ym}


def parse_material_entries(text: str) -> tuple[list[str], list[dict]]:
    """把素材库切成条目。返回 (第一个 ### 之前的行, 条目列表)。"""
    pre, entries, cur = [], [], None
    for line in normalize_newlines(text).split("\n"):
        if line.startswith("### "):
            cur = {"title": line[4:].strip(), "heading": line, "fields": {},
                   "body": [], "lines": [line]}
            entries.append(cur)
            continue
        if cur is None:
            pre.append(line)
            continue
        cur["lines"].append(line)
        m = _FIELD_RE.match(line.strip())
        if m and m.group(1) not in cur["fields"]:
            cur["fields"][m.group(1)] = m.group(2).strip()
        else:
            cur["body"].append(line)
    return pre, entries


# ---------- git 与备份 ----------

def git_available() -> bool:
    return shutil.which("git") is not None


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def is_git_repo(root: Path) -> bool:
    return (root / ".git").exists()


def outer_repo_top(root: Path) -> str | None:
    """上下文文件夹自己不是仓库、却在别人更大的仓库里时，返回那个仓库的顶层；否则 None。"""
    if is_git_repo(root) or not git_available() or not root.is_dir():
        return None
    r = git(root, "rev-parse", "--show-toplevel")
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return r.stdout.strip()


IN_OUTER_REPO_MSG = ("上下文文件夹在另一个更大的 git 仓库里（顶层：{top}），为了不碰那个仓库里别的文件，"
                     "这里不做任何 git 提交；撤回照样靠 99-附录/_填充前备份/。")


OWN_TOP_EXTRA = (APPENDIX_DIR, REPORT_NAME, READLIST_NAME, SPEC_NAME, COVER_NAME)


def _git_tops(root: Path, *args: str) -> set[str]:
    r = git(root, *args)
    if r.returncode != 0:
        return set()
    return {t.split("/")[0] for t in r.stdout.split("\0") if t}


def own_tops(root: Path) -> set[str]:
    """上下文文件夹自己的顶层项：模板里有的、99-附录、报告、已读清单。别的顶层文件一概不碰。"""
    names = {p.name for p in TEMPLATES_DIR.iterdir()} if TEMPLATES_DIR.is_dir() else set()
    return names | set(OWN_TOP_EXTRA)


def git_commit(root: Path, message: str) -> tuple[str, str]:
    """返回 (状态, 说明)。状态：skipped / nothing / ok / error。
    只认根目录自己的 .git；只添加、只提交上下文文件夹自己的文件（不用 git add -A）；
    备份文件夹和进度文件永远不进提交（就算 .gitignore 被改掉）。"""
    top = outer_repo_top(root)
    if top:
        return "skipped", IN_OUTER_REPO_MSG.format(top=top)
    if not is_git_repo(root):
        return "skipped", "不是 git 仓库，跳过提交"
    if not git_available():
        return "skipped", "没装 git，跳过提交"
    own = own_tops(root)
    known = _git_tops(root, "ls-files", "-z")
    add_specs = sorted(n for n in own if (root / n).exists() or n in known)
    if add_specs:
        r = git(root, "add", "-A", "--", *add_specs)
        if r.returncode != 0:
            return "error", "git add 失败：" + (r.stderr.strip() or r.stdout.strip())
    git(root, "rm", "--cached", "-r", "-q", "--ignore-unmatch", "--", BACKUP_DIR, PROGRESS_NAME)
    # 要提交的路径：暂存区或上一次提交里有的、属于上下文的顶层项（空文件夹 git 不认，不能写进路径）
    in_index = _git_tops(root, "ls-files", "-z")
    in_head = _git_tops(root, "ls-tree", "-z", "--name-only", "HEAD")
    specs = sorted(n for n in own if n in in_index or n in in_head)
    if not specs:
        return "nothing", "没有属于上下文的文件，跳过提交"
    if git(root, "diff", "--cached", "--quiet", "--", *specs).returncode == 0:
        return "nothing", "没有改动，跳过提交"
    # 没配过 git 用户名的机器上也能提交：临时给一个本地身份，不改用户的 git 配置
    ident = [] if _has_identity(root) else [
        "-c", "user.name=life-context", "-c", "user.email=life-context@localhost"]
    # 带路径提交：用户自己暂存的别的改动不会被捎进来
    r = git(root, *ident, "commit", "-q", "-m", message, "--", *specs)
    if r.returncode != 0:
        return "error", "git commit 失败：" + (r.stderr.strip() or r.stdout.strip())
    h = git(root, "rev-parse", "--short", "HEAD").stdout.strip()
    return "ok", f"已提交 {h}：{message}（只提交了上下文文件夹自己的文件）"


def _has_identity(root: Path) -> bool:
    return bool(git(root, "config", "user.email").stdout.strip())


def snapshot_files(root: Path) -> list[Path]:
    """要进备份的文件：整个上下文文件夹，除了 .git 和备份文件夹自己。"""
    out = []
    backup = (root / BACKUP_DIR).resolve()
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if rel.parts and rel.parts[0] == ".git":
            continue
        try:
            p.resolve().relative_to(backup)
            continue
        except ValueError:
            pass
        out.append(p)
    return out


def make_backup(root: Path, label: str) -> Path:
    """把整个上下文文件夹（除 .git 和备份本身）复制到 99-附录/_填充前备份/<时间>-<label>/。

    同时写一份 _清单.txt：第一行是备份时间（UTC，排先后用它，不受时区设置变化影响），下面每行一个备份时
    存在的文件（撤回时，不在清单里的文件算备份之后才出现的，会被删掉）。
    """
    t = now()
    stamp = t.strftime("%Y%m%d-%H%M%S")
    dest = root / BACKUP_DIR / f"{stamp}-{label}"
    n = 1
    while dest.exists():
        n += 1
        dest = root / BACKUP_DIR / f"{stamp}-{label}-{n}"
    files = snapshot_files(root)
    dest.mkdir(parents=True)
    rels = []
    for p in files:
        rel = p.relative_to(root)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)
        rels.append(rel.as_posix())
    utc = t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    write_text(dest / MANIFEST_NAME, f"{MANIFEST_UTC_PREFIX}{utc}\n" + "\n".join(rels) + ("\n" if rels else ""))
    return dest


MANIFEST_NAME = "_清单.txt"
MANIFEST_UTC_PREFIX = "# 备份时间（UTC）："


def read_manifest(b: Path) -> list[str]:
    """备份清单里的文件（相对根目录）。老备份没有时间行，照样能读。"""
    return [l.strip() for l in read_text(b / MANIFEST_NAME).split("\n")
            if l.strip() and not l.startswith("# ")]


def backup_utc(b: Path) -> datetime | None:
    """清单里记的 UTC 时间；老备份没有就 None。"""
    try:
        first = read_text(b / MANIFEST_NAME).split("\n", 1)[0].strip()
    except OSError:
        return None
    if not first.startswith(MANIFEST_UTC_PREFIX):
        return None
    try:
        return datetime.strptime(first[len(MANIFEST_UTC_PREFIX):].strip().rstrip("Z"),
                                 "%Y-%m-%dT%H:%M:%S.%f").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _name_time(name: str) -> datetime | None:
    """老备份：从文件夹名读本地时间，按现在的时区设置换成 UTC（只是退路，时区改过就可能不准）。"""
    m = re.match(r"^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})", name)
    if not m:
        return None
    naive = datetime(*(int(x) for x in m.groups()))
    tz = now().tzinfo
    return naive.replace(tzinfo=tz).astimezone(timezone.utc)


def backup_sort_key(b: Path) -> tuple:
    """备份先后：清单里有 UTC 时间就按它，老备份退回按文件夹名里的时间。"""
    t = backup_utc(b) or _name_time(b.name) or datetime.min.replace(tzinfo=timezone.utc)
    return (t, b.name)


def list_backups(root: Path) -> list[Path]:
    """所有备份，从旧到新。"""
    d = root / BACKUP_DIR
    if not d.is_dir():
        return []
    return sorted((p for p in d.iterdir() if p.is_dir() and (p / MANIFEST_NAME).is_file()),
                  key=backup_sort_key)


def pre_fill_backups(root: Path) -> list[Path]:
    """「-填充前」备份，从旧到新（按清单里的 UTC 时间排，老备份按文件夹名）。"""
    return [b for b in list_backups(root) if "-填充前" in b.name]


def backup_time(b: Path) -> str:
    """备份时间，给人看：有 UTC 时间就换成现在的时区显示，老备份从文件夹名读。"""
    u = backup_utc(b)
    if u:
        return f"{u.astimezone(now().tzinfo):%Y-%m-%d %H:%M:%S}"
    m = re.match(r"^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})", b.name)
    return f"{m[1]}-{m[2]}-{m[3]} {m[4]}:{m[5]}:{m[6]}" if m else "（时间读不出）"


def raw_name(path: Path) -> str:
    """save_raw.py 落盘的原话文件的短名（出处写它）：文件第一行 `# 短名`；读不到就从文件名猜。"""
    try:
        first = read_text(path).split("\n", 1)[0]
    except OSError:
        first = ""
    if first.startswith("# ") and first[2:].strip():
        return first[2:].strip()
    m = re.match(r"^\d{8}-\d{4,6}-(.+)\.md$", path.name)
    return m.group(1) if m else path.stem


def cite_name(stem: str) -> str:
    """材料名写进出处时的样子：去掉扩展名之后，再去掉名字里自带的书名号（《辞职》读后感 → 辞职读后感），
    免得写成《《辞职》读后感》这种套着的书名号。"""
    return stem.replace("《", "").replace("》", "")


def read_undo_log(root: Path) -> list[dict]:
    """撤回记录：每次撤回一行，记恢复到哪份、撤回前的样子存在哪份。"""
    p = root / BACKUP_DIR / UNDO_LOG_NAME
    if not p.is_file():
        return []
    out = []
    for line in read_text(p).split("\n"):
        m = re.match(r"^- (.+?)：恢复到 `([^`]+)`，撤回前的样子存在 `([^`]+)`$", line.strip())
        if m:
            out.append({"time": m[1], "restored": m[2], "safety": m[3]})
    return out


def append_undo_log(root: Path, restored: str, safety: str) -> None:
    p = root / BACKUP_DIR / UNDO_LOG_NAME
    head = ("# 撤回记录\n\n> 说明：undo.py 每撤回一次记一行。连续撤回时靠它判断「上次已经恢复到哪份」，"
            "不要手改。\n\n")
    old = read_text(p) if p.is_file() else head
    write_text(p, old.rstrip("\n") + ("\n" if read_undo_log(root) else "\n\n") + f"- {now():%Y-%m-%d %H:%M:%S}：恢复到 `{restored}`，"
               f"撤回前的样子存在 `{safety}`\n")


def archive_report(root: Path) -> str | None:
    """已有 _填充报告.md 时，挪到 99-附录/_历次报告/<时间>.md。返回新位置（相对根目录）或 None。"""
    src = root / REPORT_NAME
    if not src.is_file():
        return None
    text = read_text(src)
    m = re.search(r"^- 时间：(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})", text, re.M)
    stamp = f"{m[1]}{m[2]}{m[3]}-{m[4]}{m[5]}" if m else now().strftime("%Y%m%d-%H%M")
    d = root / REPORT_ARCHIVE_DIR
    dest = d / f"{stamp}.md"
    n = 1
    while dest.exists():
        n += 1
        dest = d / f"{stamp}-{n}.md"
    write_text(dest, text)
    src.unlink()
    return dest.relative_to(root).as_posix()


# ---------- 已读清单（跨次填充：读过的材料不再重读） ----------

_READ_RE = re.compile(r"^- 《(.+?)》 `([0-9a-f]{16})` (\d+) 字节 —— 读于 (.+)$")


def load_readlist(root: Path) -> list[dict]:
    p = root / READLIST_NAME
    if not p.is_file():
        return []
    out = []
    for line in read_text(p).split("\n"):
        m = _READ_RE.match(line.strip())
        if m:
            out.append({"name": m[1], "sha": m[2], "size": int(m[3]), "when": m[4]})
    return out


def add_to_readlist(root: Path, name: str, sha: str, size: int, when: str) -> None:
    p = root / READLIST_NAME
    rows = load_readlist(root)
    if any(r["sha"] == sha and r["name"] == name for r in rows):
        return
    head = ("# 已读清单\n\n> 说明：读过的每份材料一行：名字、内容指纹、大小、哪次填充读的。由 scripts/progress.py "
            "读写，不要手改。下次填充遇到内容没变的同一份材料直接跳过，不重读；要重读，init 加 --reread。\n\n")
    old = read_text(p) if p.is_file() else head
    write_text(p, old.rstrip("\n") + ("\n" if rows else "\n\n") + f"- 《{name}》 `{sha}` {size} 字节 —— 读于 {when}\n")


# ---------- 规范版本 ----------

_SPEC_VER_RE = re.compile(r"^> 规范版本：\s*(\S+)", re.M)


def spec_version(text: str) -> str | None:
    m = _SPEC_VER_RE.search(normalize_newlines(text))
    return m.group(1) if m else None
