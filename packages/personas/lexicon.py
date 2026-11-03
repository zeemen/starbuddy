"""禁语词表加载（packages/personas/forbidden/，spec 设计要点 2）。

词表是唯一真相源：点点评分器、guard、KGE-03 页面检查都经这里读取。加载时逐条校验（fail closed）：
类名合法、rule_id 不重复、method 与字段匹配、正则能编译、出处与 origin 齐全。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import REPO_ROOT, PersonaConfigError

MANIFEST_REL = "packages/personas/forbidden/manifest.yaml"
CLASSES = ("A", "B", "C", "D", "E", "F:math", "F:chinese", "G", "platform")
METHODS = ("rule", "context", "model")
RULE_KINDS = ("sentence_length", "question")
# 结构上下文层已实现的检查（guard.classify）；登记成 context 的条目必须是其中之一，否则应标 model
CONTEXT_CHECKS = ("hint_overreach", "unverified_claim", "off_moment", "star_outside", "uncited_quote", "full_generation")
ORIGINS = ("verbatim", "inferred")


@dataclass(frozen=True)
class Rule:
    rule_id: str
    cls: str
    method: str
    source: str
    origin: str
    pattern: str | None = None
    literal: bool = False
    kind: str | None = None
    max_chars: int | None = None
    check: str | None = None
    applies_to_types: tuple[str, ...] = ()
    examples_positive: tuple[Any, ...] = ()
    examples_negative: tuple[Any, ...] = ()
    file: str = ""

    @property
    def regex(self) -> str | None:
        """规则层正则的源文本：literal 条目按字面转义（与 KGE-03 抽取时的 re.escape 一致）"""
        if self.pattern is None:
            return None
        return re.escape(self.pattern) if self.literal else self.pattern

    def compiled(self) -> re.Pattern[str] | None:
        rx = self.regex
        return None if rx is None else re.compile(rx)


@dataclass(frozen=True)
class Lexicon:
    version: str
    reviewed: bool
    rules: tuple[Rule, ...]
    class_versions: dict[str, str]
    patterns: dict[str, dict[str, str]]
    patterns_version: str
    files: tuple[str, ...]          # 相对仓库根
    legacy_mirror: str | None = None
    _compiled: dict[str, re.Pattern[str]] = field(default_factory=dict, compare=False, repr=False)

    def by_class(self, cls: str) -> list[Rule]:
        return [r for r in self.rules if r.cls == cls]

    def rx(self, rule: Rule) -> re.Pattern[str]:
        if rule.rule_id not in self._compiled:
            c = rule.compiled()
            if c is None:
                raise PersonaConfigError(f"{rule.rule_id} 没有规则层模式")
            self._compiled[rule.rule_id] = c
        return self._compiled[rule.rule_id]

    def pattern(self, name: str, key: str = "regex") -> re.Pattern[str]:
        cache = f"pattern:{name}:{key}"
        if cache not in self._compiled:
            try:
                self._compiled[cache] = re.compile(self.patterns[name][key])
            except KeyError as e:
                raise PersonaConfigError(f"规则模式 {name}.{key} 不存在") from e
        return self._compiled[cache]


def read_yaml(path: Path, rel: str) -> Any:
    """YAML 解析失败按配置错误报告（fail closed，调用方只需接 PersonaConfigError）"""
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise PersonaConfigError(f"{rel}: YAML 不合法") from e


def _as_tuple(v: Any) -> tuple[Any, ...]:
    if v is None:
        return ()
    if not isinstance(v, list):
        raise PersonaConfigError(f"应为列表：{v!r}")
    return tuple(v)


def _rule(raw: dict[str, Any], cls: str, rel: str, file_types: tuple[str, ...]) -> Rule:
    rid = str(raw.get("rule_id") or "")
    if not re.fullmatch(r"[A-Z][A-Za-z0-9]*(-[A-Za-z0-9]+)+", rid):
        raise PersonaConfigError(f"{rel}: rule_id 不合法：{rid!r}")
    method = raw.get("method")
    if method not in METHODS:
        raise PersonaConfigError(f"{rel} {rid}: method 必须是 {METHODS} 之一")
    if raw.get("origin") not in ORIGINS:
        raise PersonaConfigError(f"{rel} {rid}: origin 必须是 verbatim 或 inferred")
    if not str(raw.get("source") or "").strip():
        raise PersonaConfigError(f"{rel} {rid}: 必须注明出处 source")
    if "class" in raw and raw["class"] != cls:
        raise PersonaConfigError(f"{rel} {rid}: class 与文件的类 {cls} 不一致")
    kind = raw.get("kind")
    r = Rule(rule_id=rid, cls=cls, method=method, source=str(raw["source"]), origin=str(raw["origin"]),
             pattern=None if raw.get("pattern") is None else str(raw["pattern"]), literal=bool(raw.get("literal", False)),
             kind=kind, max_chars=raw.get("max_chars"), check=raw.get("check"),
             applies_to_types=tuple(raw.get("applies_to_types") or file_types),
             examples_positive=_as_tuple(raw.get("examples_positive")), examples_negative=_as_tuple(raw.get("examples_negative")),
             file=rel)
    if method == "rule":
        if kind is None and r.pattern is None:
            raise PersonaConfigError(f"{rel} {rid}: 规则层条目要有 pattern 或 kind")
        if kind is not None and kind not in RULE_KINDS:
            raise PersonaConfigError(f"{rel} {rid}: kind 必须是 {RULE_KINDS} 之一")
        if kind == "sentence_length" and not isinstance(r.max_chars, int):
            raise PersonaConfigError(f"{rel} {rid}: sentence_length 要写 max_chars")
        if r.pattern is not None:
            try:
                re.compile(r.regex or "")
            except re.error as e:
                raise PersonaConfigError(f"{rel} {rid}: 正则不合法") from e
        if not r.examples_positive or not r.examples_negative:
            raise PersonaConfigError(f"{rel} {rid}: 规则层条目要有正例与反例（examples_positive / examples_negative）")
    elif method == "context":
        if r.check not in CONTEXT_CHECKS:
            raise PersonaConfigError(f"{rel} {rid}: context 条目的 check 必须是已实现的 {CONTEXT_CHECKS} 之一，否则标 model")
        if not r.examples_positive or not r.examples_negative:
            raise PersonaConfigError(f"{rel} {rid}: 结构层条目要有正例与反例")
    elif r.pattern is not None or kind is not None:
        raise PersonaConfigError(f"{rel} {rid}: model 条目不写模式（本冲刺不执行模型判定）")
    return r


def load_lexicon(root: Path | str = REPO_ROOT, manifest_rel: str = MANIFEST_REL) -> Lexicon:
    root = Path(root)
    mpath = root / manifest_rel
    if not mpath.is_file():
        raise PersonaConfigError(f"找不到禁语词表清单 {manifest_rel}")
    man = read_yaml(mpath, manifest_rel) or {}
    version = str(man.get("version") or "")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise PersonaConfigError(f"{manifest_rel}: version 不合法")
    base = mpath.parent
    rules: list[Rule] = []
    class_versions: dict[str, str] = {}
    files: list[str] = [manifest_rel]
    for name in man.get("files") or []:
        p = base / name
        rel = p.relative_to(root).as_posix() if p.is_relative_to(root) else str(p)
        if not p.is_file():
            raise PersonaConfigError(f"找不到禁语词表 {rel}")
        doc = read_yaml(p, rel) or {}
        cls = doc.get("class")
        if cls not in CLASSES:
            raise PersonaConfigError(f"{rel}: class 必须是 {CLASSES} 之一")
        if cls in class_versions:
            raise PersonaConfigError(f"{rel}: 类 {cls} 重复定义")
        if doc.get("reviewed") is not False and doc.get("reviewed") is not True:
            raise PersonaConfigError(f"{rel}: 要写 reviewed")
        class_versions[cls] = str(doc.get("version"))
        raws = doc.get("rules") or []
        if not raws:
            raise PersonaConfigError(f"{rel}: 没有任何规则（fail closed）")
        file_types = tuple(doc.get("applies_to_types") or ())
        rules += [_rule(x, cls, rel, file_types) for x in raws]
        files.append(rel)
    if not rules:
        raise PersonaConfigError(f"{manifest_rel}: 没有列出任何词表文件")
    ids = [r.rule_id for r in rules]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        raise PersonaConfigError(f"rule_id 重复：{dup}")
    pat_rel = man.get("patterns")
    patterns: dict[str, dict[str, str]] = {}
    patterns_version = ""
    if pat_rel:
        pp = (base / pat_rel).resolve()
        if not pp.is_file():
            raise PersonaConfigError(f"找不到规则模式文件 {pat_rel}")
        pdoc = read_yaml(pp, str(pat_rel)) or {}
        patterns = {k: dict(v) for k, v in (pdoc.get("patterns") or {}).items()}
        patterns_version = str(pdoc.get("version") or "")
        for name, p in patterns.items():
            for k in ("regex", "mention", "allowed"):
                if k in p:
                    try:
                        re.compile(p[k])
                    except re.error as e:
                        raise PersonaConfigError(f"规则模式 {name}.{k} 不合法") from e
        files.append(pp.relative_to(root.resolve()).as_posix() if pp.is_relative_to(root.resolve()) else str(pp))
    return Lexicon(version=version, reviewed=bool(man.get("reviewed")), rules=tuple(rules), class_versions=class_versions,
                   patterns=patterns, patterns_version=patterns_version, files=tuple(files), legacy_mirror=man.get("legacy_mirror"))
