"""人设中立与适龄词表（spec 设计要点 4“人设中立与适龄”；KGE V1.1 修订“页面人设中立”）。

冲刺 3-F 词表收拢后，本模块不再自行抽取，只复用人设治理组件 guard 的导出（packages/personas/guard.export_kge）：
  - 禁语 A–E、G 类与数学 / 语文 F 类：packages/personas/forbidden/（唯一真相源；F:math 只对 aya 页面、F:chinese 只对 wenli 页面，
    按登记表的 kge_subject；A–E、G 对所有页面）；
  - 角色名、称呼、口头禅与固定句式：packages/personas/registry.yaml（从规格书摘录，逐项注明出处）；
  - 角色设定元素：packages/personas/patterns/diandian.yaml 的 aya_world（阿鸦的设定只在课堂内）。
guard 加载失败（文件缺失、正则不合法、类为空）时直接报错（fail closed），不静默返回空词表。

页面仓库里没有本仓的 packages/：先在本仓用 `python -m knowledge.lint --export-lexicon lexicon.json` 导出，
随页面仓库 CI 用 `--lexicon lexicon.json` 读入（词表更新由人设负责人在本仓改，再导出）。
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

LEXICON_FORMAT = "kge-lint-lexicon/1"
# 句长上限（字）：编纂规范 codification-math-v1 §6 的机器可检项，数值待教研签发时确认
MAX_SENTENCE_CHARS = 60

REGISTRY = "packages/personas/registry.yaml"
FORBIDDEN_DIR = "packages/personas/forbidden"
# guard 导出里的类 → 本模块的类（页面检查只收这几类；结构层、模型层与平台级条目不导出）
KGE_CLASSES = {"A": (), "B": (), "C": (), "D": (), "E": (), "F": ("aya", "wenli"), "G": ()}


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


def _guard(root: Path) -> Any:
    """导入本仓 packages/personas 的 guard（与 compiler/full.py 接模型路由的方式相同：把目录加进 sys.path）"""
    pkg = root / "packages"
    if not (pkg / "personas" / "guard" / "__init__.py").is_file():
        raise LexiconError(f"找不到 {pkg / 'personas'}（人设治理组件 guard）")
    if str(pkg) not in sys.path:
        sys.path.insert(0, str(pkg))
    from personas import PersonaConfigError  # noqa: PLC0415
    from personas.guard import export_kge, load_context  # noqa: PLC0415

    return export_kge, load_context, PersonaConfigError


def load_from_repo(root: Path) -> Lexicon:
    root = Path(root)
    if not (root / REGISTRY).is_file():
        raise LexiconError(f"找不到 {REGISTRY}（人设词表的来源）")
    export_kge, load_context, config_error = _guard(root)
    try:
        exp = export_kge(load_context(root))
    except config_error as e:
        raise LexiconError(f"人设治理组件的词表不合法：{e}") from e
    forbidden = [Rule(x["id"], x["cls"], x["regex"], x["source"], tuple(x["subjects"])) for x in exp["forbidden"] if x["cls"] in KGE_CLASSES]
    for cls, subjects in KGE_CLASSES.items():
        got = [r for r in forbidden if r.cls == cls]
        if not got:
            raise LexiconError(f"guard 导出里没有 {cls} 类禁语")
        if any(set(r.subjects) - set(subjects) for r in got):
            raise LexiconError(f"{cls} 类的适用学科超出页面检查的口径")
    setting = [Rule(x["id"], x["cls"], x["regex"], x["source"], tuple(x["subjects"])) for x in exp["setting_patterns"]]
    lex = Lexicon(tuple(exp["role_names"]), tuple(exp["address_terms"]), tuple(exp["catchphrases"]), tuple(setting),
                  tuple(forbidden), MAX_SENTENCE_CHARS, tuple(exp["sources"]))
    lex.validate()
    return lex


def repo_root() -> Path | None:
    """本仓根目录（services/knowledge/knowledge/lint/lexicon.py 往上四级）；页面仓库里没有就返回 None"""
    root = Path(__file__).resolve().parents[4]
    return root if (root / REGISTRY).is_file() else None


def default_lexicon() -> Lexicon:
    root = repo_root()
    if root is None:
        raise LexiconError("不在 StarBuddy 仓库里：请用 --lexicon 指定导出的词表")
    return load_from_repo(root)
