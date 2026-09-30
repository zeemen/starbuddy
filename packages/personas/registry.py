"""Prompt 登记表、指纹锁与 persona-registry-check（spec 设计要点 1）。

  python -m personas.registry check [--root R] [--registry P] [--json]   # 进 make check，CI 必跑
  python -m personas.registry lock  [--root R] [--registry P]            # make persona-lock：锁文件只能由它改写

检查项：
  1. 登记的文件与符号都存在，版本号合法，prompt_id 不重复；代码常量里的版本号、模板名与登记一致；
  2. 内容指纹变了而版本没升 → 失败“Prompt 变更必须升版本”（禁语词表同理：任何词表文件变了，manifest 的 version 要升）；
  3. 指纹或版本与锁文件不一致 → 失败“运行 make persona-lock 并跑回归”；
  4. 未登记的 Prompt（启发式扫描），可用 `persona-registry: ignore <原因>` 豁免，豁免行列进输出；
  另外核对禁语词表旧位置（前端单测仍在读）与新位置逐条一致。
退出码：0 通过，1 失败，3 缺依赖（非严格模式下 Makefile 当“跳过”）。
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - 缺依赖时按退出码 3 报告
    yaml = None  # type: ignore[assignment]

from . import REGISTRY_REL, REPO_ROOT, PersonaConfigError

LOCK_FORMAT = "persona-prompts-lock/1"
PERSONA_TYPES = ("peer", "resident", "examiner", "scene")
TYPES = PERSONA_TYPES + ("task",)
STATUSES = ("active", "not_started")
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$")
INTEGER = re.compile(r"^[1-9]\d*$")
CODE_EXT = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
IGNORE_MARK = re.compile(r"persona-registry:\s*ignore\b(.*)")
MIN_PROMPT_CHARS = 40


# ---------------------------------------------------------------- 加载与校验

@dataclass(frozen=True)
class PromptEntry:
    owner_id: str
    owner_type: str
    prompt_id: str
    path: str
    version: str
    symbols: tuple[str, ...] = ()
    version_scheme: str = "semver"
    version_symbol: str | None = None
    template_symbol: str | None = None


def load_registry(root: Path | str = REPO_ROOT, rel: str = REGISTRY_REL) -> dict[str, Any]:
    p = Path(root) / rel
    if not p.is_file():
        raise PersonaConfigError(f"找不到登记表 {rel}")
    from .lexicon import read_yaml  # noqa: PLC0415
    doc = read_yaml(p, rel) or {}
    if not isinstance(doc, dict) or not isinstance(doc.get("personas"), list):
        raise PersonaConfigError(f"{rel}: personas 必须是列表")
    return doc


def personas_by_id(reg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {p["persona_id"]: p for p in reg.get("personas") or []}


def iter_prompts(reg: dict[str, Any]) -> list[PromptEntry]:
    out: list[PromptEntry] = []
    owners = [(p, p.get("persona_id"), p.get("type")) for p in reg.get("personas") or []]
    owners += [(t, t.get("task_id"), "task") for t in reg.get("tasks") or []]
    for owner, oid, otype in owners:
        for pr in owner.get("prompts") or []:
            out.append(PromptEntry(owner_id=str(oid), owner_type=str(otype), prompt_id=str(pr.get("prompt_id")), path=str(pr.get("path")),
                                   version=str(pr.get("prompt_version")), symbols=tuple(pr.get("symbols") or ()),
                                   version_scheme=str(pr.get("version_scheme") or "semver"), version_symbol=pr.get("version_symbol"),
                                   template_symbol=pr.get("template_symbol")))
    return out


def schema_problems(reg: dict[str, Any], root: Path, classes: tuple[str, ...]) -> list[str]:
    errs: list[str] = []
    ids = [p.get("persona_id") for p in reg.get("personas") or []]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        errs.append(f"persona_id 重复：{dup}")
    for p in reg.get("personas") or []:
        pid = p.get("persona_id")
        if not isinstance(pid, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", pid):
            errs.append(f"persona_id 不合法：{pid!r}")
            continue
        if p.get("type") not in PERSONA_TYPES:
            errs.append(f"{pid}: type 必须是 {PERSONA_TYPES} 之一")
        if p.get("status") not in STATUSES:
            errs.append(f"{pid}: status 必须是 {STATUSES} 之一")
        if not p.get("display_name"):
            errs.append(f"{pid}: 缺 display_name")
        for c in p.get("forbidden_classes") or []:
            if c not in classes:
                errs.append(f"{pid}: forbidden_classes 里的 {c} 不是已知的禁语类")
        if not p.get("forbidden_classes"):
            errs.append(f"{pid}: 缺 forbidden_classes")
        if p.get("status") == "active":
            doc = p.get("spec_doc")
            if not doc or not (root / doc).is_file():
                errs.append(f"{pid}: 规格书 {doc} 不存在")
            if not p.get("spec_version"):
                errs.append(f"{pid}: 缺 spec_version")
        elif p.get("prompts"):
            errs.append(f"{pid}: 未立项的角色不能登记 Prompt")
        if p.get("type") == "examiner" and p.get("status") == "active":
            if not p.get("appearance_moments") or len(p["appearance_moments"]) > 4:
                errs.append(f"{pid}: 考官型要登记 1–4 条出场时刻（考官型基线 §3.1）")
            if not (p.get("fixed_lines") or {}).get("exit"):
                errs.append(f"{pid}: 考官型要登记退场短句")
    for t in reg.get("tasks") or []:
        if t.get("type") != "task":
            errs.append(f"{t.get('task_id')}: tasks 里的条目 type 必须是 task")
    pids = [e.prompt_id for e in iter_prompts(reg)]
    for dup in sorted({i for i in pids if pids.count(i) > 1}):
        errs.append(f"prompt_id 重复：{dup}")
    for e in iter_prompts(reg):
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", e.prompt_id):
            errs.append(f"prompt_id 不合法：{e.prompt_id!r}")
        if e.version_scheme == "semver":
            if not SEMVER.match(e.version):
                errs.append(f"{e.prompt_id}: prompt_version {e.version!r} 不是 semver（契约 persona.prompt_version 与 source_version 要求）")
        elif e.version_scheme == "integer":
            if e.owner_type != "task":
                errs.append(f"{e.prompt_id}: 只有 type: task 允许整数版本号")
            elif not INTEGER.match(e.version):
                errs.append(f"{e.prompt_id}: prompt_version {e.version!r} 不是正整数")
        else:
            errs.append(f"{e.prompt_id}: version_scheme 只能是 semver 或 integer")
        if not (root / e.path).is_file():
            errs.append(f"{e.prompt_id}: 登记的文件 {e.path} 不存在")
    return errs


# ---------------------------------------------------------------- 指纹

def normalize(text: str) -> str:
    """统一换行、去掉行尾空白（spec 设计要点 1 指纹锁）"""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(ln.rstrip() for ln in lines).strip("\n") + "\n"


def sha256(text: str) -> str:
    return "sha256:" + hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def _py_symbol(src: str, name: str, path: str) -> str:
    tree = ast.parse(src)
    for node in tree.body:
        hit = False
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            hit = node.name == name
        elif isinstance(node, ast.Assign):
            hit = any(isinstance(t, ast.Name) and t.id == name for t in node.targets)
        elif isinstance(node, ast.AnnAssign):
            hit = isinstance(node.target, ast.Name) and node.target.id == name
        if hit:
            seg = ast.get_source_segment(src, node)
            if seg:
                return seg
    raise PersonaConfigError(f"{path}: 找不到符号 {name}")


def _skip_string(src: str, i: int) -> int:
    """src[i] 是引号：返回字符串结束后的下标。模板字符串里的 ${…} 递归跳过"""
    q = src[i]
    i += 1
    while i < len(src):
        c = src[i]
        if c == "\\":
            i += 2
            continue
        if q == "`" and src.startswith("${", i):
            i = _skip_code(src, i + 2, "}")
            continue
        if c == q:
            return i + 1
        if c == "\n" and q != "`":
            raise PersonaConfigError("字符串没有闭合")
        i += 1
    raise PersonaConfigError("字符串没有闭合")


def _skip_code(src: str, i: int, close: str) -> int:
    """从 i 扫到与 close 配对的位置之后（跳过字符串、注释与嵌套括号）"""
    pairs = {"(": ")", "[": "]", "{": "}"}
    stack = [close]
    while i < len(src):
        c = src[i]
        if c in "'\"`":
            i = _skip_string(src, i)
            continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = len(src) if j < 0 else j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i)
            if j < 0:
                raise PersonaConfigError("注释没有闭合")
            i = j + 2
            continue
        if c in pairs:
            stack.append(pairs[c])
        elif c in ")]}":
            if c != stack[-1]:
                raise PersonaConfigError("括号不配对")
            stack.pop()
            if not stack:
                return i + 1
        i += 1
    raise PersonaConfigError("括号没有闭合")


def _ts_symbol(src: str, name: str, path: str) -> str:
    """TypeScript 顶层 const / function 的源码（不支持正则字面量；遇到不配对直接报错，fail closed）"""
    m = re.search(rf"^(?:export\s+)?(?:const|let|var)\s+{re.escape(name)}\b[^=\n]*=\s*", src, re.M)
    try:
        if m:
            i = m.end()
            while i < len(src):
                c = src[i]
                if c in "'\"`":
                    i = _skip_string(src, i)
                elif c in "([{":
                    i = _skip_code(src, i + 1, {"(": ")", "[": "]", "{": "}"}[c])
                elif c == ";":
                    return src[m.start():i + 1]
                elif c == "\n" and src[m.end():i].strip():
                    nxt = src[i + 1:].lstrip(" \t")
                    if not nxt.startswith((".", "+", "?", ":", "|", "&")):
                        return src[m.start():i]
                    i += 1
                else:
                    i += 1
            raise PersonaConfigError("语句没有结束")
        m = re.search(rf"^(?:export\s+)?(?:async\s+)?function\s+{re.escape(name)}\s*\(", src, re.M)
        if m:
            i = _skip_code(src, m.end(), ")")
            j = src.find("{", i)
            if j < 0:
                raise PersonaConfigError("找不到函数体")
            return src[m.start():_skip_code(src, j + 1, "}")]
    except PersonaConfigError as e:
        raise PersonaConfigError(f"{path}: 符号 {name} 解析失败（{e}）") from e
    raise PersonaConfigError(f"{path}: 找不到符号 {name}")


def extract_symbol(path: Path, name: str, rel: str) -> str:
    src = path.read_text(encoding="utf-8")
    if path.suffix == ".py":
        return _py_symbol(src, name, rel)
    if path.suffix in {".ts", ".tsx", ".js", ".mjs", ".cjs"}:
        return _ts_symbol(src, name, rel)
    raise PersonaConfigError(f"{rel}: 不支持按符号登记的文件类型")


def const_value(path: Path, name: str, rel: str) -> str:
    seg = extract_symbol(path, name, rel)
    m = re.search(r"=\s*(['\"])([^'\"\n]*)\1", seg)
    if not m:
        raise PersonaConfigError(f"{rel}: {name} 不是字符串常量")
    return m.group(2)


def prompt_text(e: PromptEntry, root: Path) -> str:
    p = root / e.path
    if not e.symbols:
        return p.read_text(encoding="utf-8")
    return "".join(f"# symbol {s}\n{extract_symbol(p, s, e.path)}\n" for s in e.symbols)


def prompt_fingerprint(e: PromptEntry, root: Path) -> str:
    return sha256(prompt_text(e, root))


def lexicon_fingerprint(root: Path, files: tuple[str, ...]) -> str:
    return sha256("".join(f"# file {f}\n{normalize((root / f).read_text(encoding='utf-8'))}" for f in sorted(files)))


# ---------------------------------------------------------------- 锁文件

def lock_path(reg: dict[str, Any], root: Path) -> Path:
    return root / str(reg.get("lock") or "packages/personas/prompts.lock")


def read_lock(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"format": LOCK_FORMAT, "lexicon": None, "prompts": {}}
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("format") != LOCK_FORMAT:
        raise PersonaConfigError(f"{path.name}: 锁文件格式不是 {LOCK_FORMAT}")
    return doc


def _cmp_versions(a: str, b: str, scheme: str) -> int:
    if scheme == "integer":
        return (int(a) > int(b)) - (int(a) < int(b))
    ka = tuple(int(x) for x in SEMVER.match(a).groups()[:3])  # type: ignore[union-attr]
    kb = tuple(int(x) for x in SEMVER.match(b).groups()[:3])  # type: ignore[union-attr]
    return (ka > kb) - (ka < kb)


@dataclass
class State:
    reg: dict[str, Any]
    root: Path
    entries: list[PromptEntry]
    current: dict[str, dict[str, str]]
    lexicon: dict[str, str] | None
    lock: dict[str, Any]


def _state(root: Path, rel: str, errs: list[str]) -> State | None:
    from .lexicon import CLASSES, load_lexicon  # noqa: PLC0415
    try:
        reg = load_registry(root, rel)
    except PersonaConfigError as e:
        errs.append(str(e))
        return None
    lex_info: dict[str, str] | None = None
    known: tuple[str, ...] = CLASSES
    try:
        lex = load_lexicon(root, str(reg.get("lexicon") or "packages/personas/forbidden/manifest.yaml"))
        lex_info = {"version": lex.version, "sha256": lexicon_fingerprint(root, lex.files)}
        known = tuple(sorted({r.cls for r in lex.rules}))
    except PersonaConfigError as e:
        errs.append(f"禁语词表：{e}")
    errs += schema_problems(reg, root, known)
    entries = iter_prompts(reg)
    current: dict[str, dict[str, str]] = {}
    for e in entries:
        if not (root / e.path).is_file():
            continue
        try:
            current[e.prompt_id] = {"version": e.version, "sha256": prompt_fingerprint(e, root)}
            if e.version_symbol and const_value(root / e.path, e.version_symbol, e.path) != e.version:
                errs.append(f"{e.prompt_id}: 代码里的 {e.version_symbol} 与登记的 prompt_version {e.version} 不一致")
            if e.template_symbol and const_value(root / e.path, e.template_symbol, e.path) != e.prompt_id:
                errs.append(f"{e.prompt_id}: 代码里的 {e.template_symbol} 与 prompt_id 不一致")
        except PersonaConfigError as ex:
            errs.append(str(ex))
    try:
        lock = read_lock(lock_path(reg, root))
    except (PersonaConfigError, json.JSONDecodeError) as ex:
        errs.append(f"锁文件不合法：{ex}")
        lock = {"format": LOCK_FORMAT, "lexicon": None, "prompts": {}}
    return State(reg, root, entries, current, lex_info, lock)


def _compare(pid: str, cur: dict[str, str], old: dict[str, str] | None, scheme: str, what: str) -> list[str]:
    if old is None:
        return [f"{pid}: 新{what}还没有写进锁文件：运行 make persona-lock 并跑回归"]
    if cur["sha256"] != old.get("sha256") and cur["version"] == old.get("version"):
        return [f"{pid}: {what}内容变了但版本号没升（{cur['version']}）：Prompt 变更必须升版本"]
    if cur["sha256"] != old.get("sha256") or cur["version"] != old.get("version"):
        try:
            if _cmp_versions(cur["version"], str(old.get("version")), scheme) < 0:
                return [f"{pid}: 版本号从 {old.get('version')} 回退到 {cur['version']}"]
        except (AttributeError, ValueError):
            pass
        return [f"{pid}: 指纹或版本与锁文件不一致：运行 make persona-lock 并跑回归"]
    return []


def lock_problems(st: State) -> list[str]:
    errs: list[str] = []
    old = st.lock.get("prompts") or {}
    schemes = {e.prompt_id: e.version_scheme for e in st.entries}
    for pid, cur in st.current.items():
        errs += _compare(pid, cur, old.get(pid), schemes.get(pid, "semver"), "Prompt ")
    for pid in sorted(set(old) - {e.prompt_id for e in st.entries}):
        errs.append(f"{pid}: 锁文件里有登记表之外的 prompt_id：运行 make persona-lock")
    if st.lexicon is not None:
        errs += [x.replace("Prompt 变更必须升版本", "禁语词表变更必须升 manifest 的 version")
                 for x in _compare("禁语词表", st.lexicon, st.lock.get("lexicon"), "semver", "词表")]
    return errs


# ---------------------------------------------------------------- 未登记 Prompt 的启发式扫描

_LITERAL = re.compile(r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|`(?:[^`\\]|\\.)*`|"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\'')
_ROLE_PREFIX = re.compile(r"(?:^|[。！？!?\n]|\\n)\s*(你是|你扮演)")
_SYSTEM_MSG = re.compile(r"""["']?role["']?\s*[:=]\s*["']system["']\s*,\s*["']?content["']?\s*[:=](?!\s*["'][^"'\n]{0,39}["'])""")


def _prompt_like(literal: str) -> bool:
    body = literal.strip("\"'`")
    return len(body) >= MIN_PROMPT_CHARS and bool(_ROLE_PREFIX.search(body))


def _line_of(src: str, pos: int) -> int:
    return src.count("\n", 0, pos) + 1


def scan_unregistered(root: Path, reg: dict[str, Any]) -> tuple[list[str], list[str]]:
    """返回（未登记的命中, 豁免清单）。登记过的文件整体跳过"""
    scan = reg.get("scan") or {}
    dirs = scan.get("dirs") or ["adapters", "services", "packages", "apps"]
    excl = set(scan.get("exclude_dirs") or [])
    registered = {e.path for e in iter_prompts(reg)}
    hits: list[str] = []
    exempt: list[str] = []
    for d in dirs:
        base = root / d
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file() or any(part in excl for part in p.relative_to(root).parts):
                continue
            rel = p.relative_to(root).as_posix()
            if rel in registered:
                continue
            reasons: list[tuple[int, str]] = []
            is_prompt_file = ".prompt." in p.name or "prompts" in p.relative_to(root).parts[:-1]
            if is_prompt_file:
                reasons.append((1, "Prompt 文件（*.prompt.* 或 prompts/ 目录）"))
            if p.suffix in CODE_EXT:
                try:
                    src = p.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                for m in _LITERAL.finditer(src):
                    if _prompt_like(m.group(0)):
                        reasons.append((_line_of(src, m.start()), "含“你是 / 你扮演”的长字符串常量"))
                for m in _SYSTEM_MSG.finditer(src):
                    reasons.append((_line_of(src, m.start()), "system 角色消息的内容不是短字面量"))
                lines = src.split("\n")
            else:
                lines = []
            for line, why in reasons:
                ctx = [lines[line - 1]] if 0 < line <= len(lines) else []
                if line >= 2 and line - 2 < len(lines):
                    ctx.append(lines[line - 2])
                if line == 1 and lines:
                    ctx = lines[:3]  # 文件级命中：前三行里的豁免
                mark = next((IGNORE_MARK.search(c) for c in ctx if IGNORE_MARK.search(c)), None)
                where = f"{rel}:{line}"
                if mark is None:
                    hits.append(f"{where}: 未登记的 Prompt（{why}）：登记进 {REGISTRY_REL}，或加 `persona-registry: ignore <原因>`")
                elif not mark.group(1).strip():
                    hits.append(f"{where}: 豁免必须写原因（persona-registry: ignore <原因>）")
                else:
                    exempt.append(f"{where}: {mark.group(1).strip()}")
    return hits, exempt


# ---------------------------------------------------------------- 词表旧位置（前端单测还在读）

def legacy_mirror_problems(root: Path, reg: dict[str, Any]) -> list[str]:
    from .lexicon import load_lexicon  # noqa: PLC0415
    lex = load_lexicon(root, str(reg.get("lexicon") or "packages/personas/forbidden/manifest.yaml"))
    if not lex.legacy_mirror:
        return []
    p = root / lex.legacy_mirror
    if not p.is_file():
        return []  # 前端接入 guard 后删除旧文件，同时删 manifest 的 legacy_mirror
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    errs: list[str] = []
    old = [(e.get("id"), e.get("class"), e.get("regex"), e.get("source"), e.get("origin"), e.get("styles")) for e in doc.get("forbidden") or []]
    new = [(r.rule_id, r.cls, r.pattern, r.source, r.origin, "all") for r in lex.rules if r.cls in ("A", "B", "C", "D", "E")]
    if old != new:
        errs.append(f"{lex.legacy_mirror} 的 forbidden 段与 packages/personas/forbidden/ 的 A–E 类不一致（旧位置只读镜像，改词表请改新位置并同步）")
    if (doc.get("patterns") or {}) != lex.patterns:
        errs.append(f"{lex.legacy_mirror} 的 patterns 段与 packages/personas/patterns/diandian.yaml 不一致")
    if doc.get("moved_to") != "packages/personas/forbidden/":
        errs.append(f"{lex.legacy_mirror} 要写 moved_to: packages/personas/forbidden/")
    return errs


# ---------------------------------------------------------------- 命令

def check(root: Path, rel: str = REGISTRY_REL) -> dict[str, Any]:
    errs: list[str] = []
    st = _state(root, rel, errs)
    hits: list[str] = []
    exempt: list[str] = []
    if st is not None:
        errs += lock_problems(st)
        hits, exempt = scan_unregistered(root, st.reg)
        try:
            errs += legacy_mirror_problems(root, st.reg)
        except PersonaConfigError as e:
            errs.append(str(e))
    return {"ok": not errs and not hits, "errors": errs + hits, "exemptions": exempt,
            "prompts": sorted(st.current) if st else [], "lexicon": st.lexicon if st else None}


def write_lock(root: Path, rel: str = REGISTRY_REL) -> list[str]:
    """重算锁文件。内容变了而版本没升时拒绝（否则“必须升版本”这条检查会被锁文件绕过）"""
    errs: list[str] = []
    st = _state(root, rel, errs)
    if st is None or errs:
        return errs or ["登记表不合法"]
    old = st.lock.get("prompts") or {}
    for pid, cur in st.current.items():
        o = old.get(pid)
        if o and o.get("sha256") != cur["sha256"] and o.get("version") == cur["version"]:
            errs.append(f"{pid}: Prompt 内容变了但版本号没升（{cur['version']}）：先升 prompt_version 再运行 make persona-lock")
    ol = st.lock.get("lexicon")
    if st.lexicon and ol and ol.get("sha256") != st.lexicon["sha256"] and ol.get("version") == st.lexicon["version"]:
        errs.append("禁语词表内容变了但 manifest 的 version 没升：先升版本再运行 make persona-lock")
    if errs:
        return errs
    doc = {"format": LOCK_FORMAT, "note": "由 make persona-lock 生成，不要手改；PR 里由人审 diff",
           "lexicon": st.lexicon, "prompts": {k: st.current[k] for k in sorted(st.current)}}
    lock_path(st.reg, root).write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return []


def main(argv: list[str] | None = None) -> int:
    if yaml is None:
        print("persona-registry-check: 跳过（没有 PyYAML）")
        return 3
    ap = argparse.ArgumentParser(prog="personas.registry")
    ap.add_argument("cmd", choices=["check", "lock"])
    ap.add_argument("--root", default=str(REPO_ROOT))
    ap.add_argument("--registry", default=REGISTRY_REL)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    root = Path(a.root).resolve()
    if a.cmd == "lock":
        errs = write_lock(root, a.registry)
        for e in errs:
            print(f"  {e}")
        print("persona-lock: 失败" if errs else "persona-lock: 已重算 packages/personas/prompts.lock（请跑 make eval-persona 并在 PR 里附前后对比）")
        return 1 if errs else 0
    res = check(root, a.registry)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        for e in res["errors"]:
            print(f"  {e}")
        for x in res["exemptions"]:
            print(f"  豁免：{x}")
        n = len(res["prompts"])
        print(f"persona-registry-check: {'通过' if res['ok'] else '失败'}（登记 Prompt {n} 份，豁免 {len(res['exemptions'])} 处）")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
