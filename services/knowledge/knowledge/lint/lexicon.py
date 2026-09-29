"""人设中立与适龄词表（spec 设计要点 4“人设中立与适龄”；KGE V1.1 修订“页面人设中立”）。

全部从仓库里人设负责人维护的文件**只读抽取**，本模块不另写一份词表：
  - 禁语 A–E 类：evals/persona/diandian/lexicon/forbidden.yaml 的 forbidden（点点禁语表的机器可读转写）；
  - 数学 F 类：docs/personas/阿鸦-规格书-V1.1.md §5.2“F 类 · 数学教学禁语”里加引号的表述（只对 aya 页面）；
  - 语文 F 类：docs/personas/妙笔-规格书-V1.0.md §5.2“F 类 · 语文教学禁语”（只对 wenli 页面）；
  - G 类：docs/personas/考官型基线与角色卡-方圆春秋雅典娜-V1.1.md §5.2“考官型专属 G 类”（所有页面）；
  - 角色名：各规格书一级标题「」里的名称，考官型基线“称呼”行括号里的老师名；
  - 称呼：各规格书“称呼”行里加引号的称呼；
  - 口头禅与固定句式：规格书里写明“主句式”“固定句式”的行里加引号的句子；
  - 角色设定元素：forbidden.yaml patterns.aya_world（阿鸦的设定只在课堂内）。
锚点找不到时直接报错（fail closed），不静默返回空词表。

页面仓库里没有本仓的 docs/：先在本仓用 `python -m knowledge.lint --export-lexicon lexicon.json` 导出，
随页面仓库 CI 用 `--lexicon lexicon.json` 读入（词表更新由人设负责人在本仓改，再导出）。
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

LEXICON_FORMAT = "kge-lint-lexicon/1"
# 句长上限（字）：编纂规范 codification-math-v1 §6 的机器可检项，数值待教研签发时确认
MAX_SENTENCE_CHARS = 60

FORBIDDEN_YAML = "evals/persona/diandian/lexicon/forbidden.yaml"
AYA = "docs/personas/阿鸦-规格书-V1.1.md"
MIAOBI = "docs/personas/妙笔-规格书-V1.0.md"
EXAMINER = "docs/personas/考官型基线与角色卡-方圆春秋雅典娜-V1.1.md"
DIANDIAN = "docs/personas/点点-规格书-V1.2.md"
PERSONA_FILES = (AYA, MIAOBI, EXAMINER, DIANDIAN)

_QUOTED = re.compile(r"[\"“]([^\"“”]{2,40})[\"”]")


class LexiconError(ValueError):
    pass


@dataclass(frozen=True)
class Rule:
    id: str
    cls: str
    regex: str
    source: str
    subjects: tuple[str, ...] = ()  # 空 = 所有学科

    def applies(self, subject: str) -> bool:
        return not self.subjects or subject in self.subjects


@dataclass(frozen=True)
class Lexicon:
    role_names: tuple[str, ...]
    address_terms: tuple[str, ...]
    catchphrases: tuple[str, ...]
    setting_patterns: tuple[Rule, ...]
    forbidden: tuple[Rule, ...]
    max_sentence_chars: int = MAX_SENTENCE_CHARS
    sources: tuple[str, ...] = field(default=())

    def to_json(self) -> str:
        d = asdict(self)
        return json.dumps({"format": LEXICON_FORMAT, **d}, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    @classmethod
    def from_json(cls, text: str) -> "Lexicon":
        d: dict[str, Any] = json.loads(text)
        if d.pop("format", None) != LEXICON_FORMAT:
            raise LexiconError(f"词表格式不是 {LEXICON_FORMAT}")
        rules = lambda xs: tuple(Rule(**{**x, "subjects": tuple(x.get("subjects") or ())}) for x in xs)  # noqa: E731
        lex = cls(tuple(d["role_names"]), tuple(d["address_terms"]), tuple(d["catchphrases"]), rules(d["setting_patterns"]),
                  rules(d["forbidden"]), int(d["max_sentence_chars"]), tuple(d.get("sources") or ()))
        lex.validate()
        return lex

    def validate(self) -> None:
        if not self.role_names or not self.forbidden or not self.address_terms:
            raise LexiconError("词表不完整：角色名、称呼、禁语都不能为空")
        for r in (*self.forbidden, *self.setting_patterns):
            try:
                re.compile(r.regex)
            except re.error as e:
                raise LexiconError(f"规则 {r.id} 的正则不合法") from e


def _read(root: Path, rel: str) -> str:
    p = root / rel
    if not p.is_file():
        raise LexiconError(f"找不到 {rel}（人设词表只读抽取的来源）")
    return p.read_text(encoding="utf-8")


def _block(text: str, start: str, rel: str) -> str:
    i = text.find(start)
    if i < 0:
        raise LexiconError(f"{rel} 里找不到锚点“{start}”")
    rest = text[i + len(start):]
    end = rest.find("\n###")
    return rest if end < 0 else rest[:end]


def _quoted(block: str) -> list[str]:
    return [q.strip() for q in _QUOTED.findall(block) if q.strip()]


def _phrase_rules(block: str, cls: str, prefix: str, source: str, subjects: tuple[str, ...]) -> list[Rule]:
    phrases = list(dict.fromkeys(_quoted(block)))
    if not phrases:
        raise LexiconError(f"{source} 没有抽到任何禁语")
    return [Rule(f"{prefix}-{i:02d}", cls, re.escape(p), f"{source}：“{p}”", subjects) for i, p in enumerate(phrases, 1)]


def load_from_repo(root: Path) -> Lexicon:
    root = Path(root)
    fy = yaml.safe_load(_read(root, FORBIDDEN_YAML))
    forbidden = [Rule(str(x["id"]), str(x["class"]), str(x["regex"]), f"{FORBIDDEN_YAML} {x['id']}（{x['source']}）")
                 for x in fy.get("forbidden") or []]
    if not forbidden:
        raise LexiconError(f"{FORBIDDEN_YAML} 没有 forbidden 规则")
    aya_world = (fy.get("patterns") or {}).get("aya_world")
    if not aya_world:
        raise LexiconError(f"{FORBIDDEN_YAML} 没有 patterns.aya_world")
    setting = [Rule("SET-aya-world", "setting", str(aya_world["regex"]), f"{FORBIDDEN_YAML} patterns.aya_world")]

    aya, miaobi, examiner = _read(root, AYA), _read(root, MIAOBI), _read(root, EXAMINER)
    forbidden += _phrase_rules(_block(aya, "**F 类 · 数学教学禁语**", AYA), "F", "F-aya", f"{AYA} §5.2 F 类", ("aya",))
    forbidden += _phrase_rules(_block(miaobi, "**F 类 · 语文教学禁语**", MIAOBI), "F", "F-wenli", f"{MIAOBI} §5.2 F 类", ("wenli",))
    forbidden += _phrase_rules(_block(examiner, "考官型专属 **G 类**", EXAMINER), "G", "G", f"{EXAMINER} §5.2 G 类", ())

    names: list[str] = []
    address: list[str] = []
    catch: list[str] = []
    for rel in PERSONA_FILES:
        text = _read(root, rel)
        h1 = re.search(r"^# .*?「([^」]{1,8})」", text, re.M)
        if h1:
            names.append(h1.group(1))
        for row in re.findall(r"^\| 称呼 \|(.*)$", text, re.M):
            address += _quoted(row)
            for inner in re.findall(r"（([^）]+)）", row):
                names += [x.strip() for x in inner.split("、") if x.strip()]
        for ln in text.splitlines():
            if "主句式" in ln or "固定句式" in ln:
                catch += [q for q in _quoted(ln) if len(q) >= 4]
    lex = Lexicon(tuple(dict.fromkeys(names)), tuple(dict.fromkeys(address)), tuple(dict.fromkeys(catch)), tuple(setting),
                  tuple(forbidden), MAX_SENTENCE_CHARS, (FORBIDDEN_YAML, *PERSONA_FILES))
    lex.validate()
    return lex


def repo_root() -> Path | None:
    """本仓根目录（services/knowledge/knowledge/lint/lexicon.py 往上四级）；页面仓库里没有就返回 None"""
    root = Path(__file__).resolve().parents[4]
    return root if (root / FORBIDDEN_YAML).is_file() else None


def default_lexicon() -> Lexicon:
    root = repo_root()
    if root is None:
        raise LexiconError("不在 StarBuddy 仓库里：请用 --lexicon 指定导出的词表")
    return load_from_repo(root)
