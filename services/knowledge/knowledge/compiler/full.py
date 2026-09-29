"""全量重编（spec 设计要点 3）：叙述段由模型经模型路由（services/learning/learning/model_router，冲刺 1-F）重写。

- 任务类型 T8（后台批量生产）：路由表里最贴近“离线公共知识构建”的任务；数据级别固定 L0（非个人数据）。
  路由表 T8 只接 L0（services/learning/config/model_routes.yaml），不需要改路由配置，也不新增供应商。
- 请求只含公共原子：节点的名称与范围、课标条目、前置与后续节点名、可引用的原子键（白名单逐字段挑出，不整条转发）；
  发出前再扫一遍：键名出现任何个人层字段、或任何值（含嵌套）命中个人信息模式（与体检同一份 PII_PATTERNS）就拒绝
  （fail closed，规则 13）。出境路径与 T8 的供应商由人定（lessons 待人确认）。
- SYSTEM_PROMPT 是编译任务的指令，不是任何角色的人设 Prompt，所以不跑 evals/persona（lessons 说明）；改它仍要走审查。
- 结果一律高风险，必须教研审核（两人确认）。模型标识只记在路由的调用记录里，不写进页面与合并请求说明。
- 本仓不连任何真实模型：测试用路由 test 环境的假供应商。
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..atoms import VERB_LEVELS, Snapshot
from ..pages.format import SECTIONS_BY_TYPE, Page, Section
from .jobs import CompileJob
from .rebuild import CompileError, CompileResult, assemble, finish

PROMPT_TEMPLATE = "kge-compile-kp"
PROMPT_VERSION = "1"
MAX_SECTION_CHARS = 4000
LEARNING_DIR = Path(__file__).resolve().parents[3] / "learning"

# 个人层字段（platform.* 个人层、insight.*、计量与账号）：出现在请求里即拒绝
PERSONAL_FIELDS = frozenset({
    "student_id", "student_ref", "student", "attempt", "attempt_id", "answer_raw", "step_judgement", "evidence", "evidence_id",
    "evidence_ids", "evidence_event", "mastery", "mastery_estimate", "claim", "claim_id", "guardian", "guardian_student_link",
    "household", "household_id", "account", "account_id", "phone", "mobile", "id_card", "school", "grade_result", "usage_id",
    "error_attribution", "skill_observation", "profile", "learner", "parent",
})

SYSTEM_PROMPT = (
    "你是初中数学知识页面的编纂助手。只根据用户消息里给出的公共知识原子，编写知识点页的三个叙述段："
    "key_points（讲解要点）、pitfalls（易错）、frequent_questions（高频设问）。\n"
    "要求：中性的教研口吻，不使用任何角色的名字、称呼、口头禅或比喻体系；每句不超过 60 字；"
    "每个自然段末尾至少写一个原子引用，格式 [@类型:ID]，只能引用 citable 列表里的键；"
    "需要验算的等式放在单独一行 $$ 块里，行末加 % check；不写教材原句，不写任何个人信息或群体统计。\n"
    "只输出一个 JSON 对象：{\"key_points\": \"...\", \"pitfalls\": \"...\", \"frequent_questions\": \"...\"}，"
    "每个值是不含二级标题的 Markdown 正文。"
)


def _router_module() -> Any:
    """与 evals/persona/diandian/compare.py 同样的接入方式：把 services/learning 加到 sys.path 后导入模型路由"""
    if str(LEARNING_DIR) not in sys.path:
        sys.path.insert(0, str(LEARNING_DIR))
    from learning import model_router  # noqa: PLC0415

    return model_router


def public_payload(snapshot: Snapshot, node_id: str) -> dict[str, Any]:
    """只挑公共原子里的公共字段（白名单），不整条转发快照记录"""
    atom = snapshot.get("node", node_id)
    if atom is None:
        raise CompileError(f"节点 {node_id} 不在地图快照里")
    n = atom.record

    def name(nid: str) -> str:
        rec = snapshot.node(nid)
        return str(rec["name"]) if rec else nid

    pre = snapshot.edges_to(node_id, "prerequisite")
    post = snapshot.edges_from(node_id, "prerequisite")
    cur = snapshot.curriculum_of(node_id)
    return {
        "subject": snapshot.subject,
        "map_version": snapshot.map_version,
        "node": {"ref": atom.key, "node_id": node_id, "name": str(n["name"]), "includes": list(n.get("includes") or []),
                 "excludes": list(n.get("excludes") or []), "examples": list(n.get("examples") or []),
                 "cognitive_levels": list(n.get("cognitive_levels") or [])},
        "curriculum": [{"ref": a.key, "level": VERB_LEVELS[a.record["verb_level"]], "text": str(a.record["text"])} for a in cur],
        "prerequisites": [{"ref": e.key, "name": name(e.record["from_id"])} for e in pre],
        "successors": [{"ref": e.key, "name": name(e.record["to_id"])} for e in post],
        "citable": [atom.key, *(a.key for a in cur), *(e.key for e in pre), *(e.key for e in post)],
    }


def assert_public(value: Any, path: str = "$") -> None:
    """请求体里不得出现任何个人层字段名，任何字符串值不得命中个人信息模式（递归；消息只给位置与模式名，不回显值）"""
    from ..lint.checks import pii_hits

    if isinstance(value, Mapping):
        for k, v in value.items():
            if str(k).lower() in PERSONAL_FIELDS:
                raise CompileError(f"全量重编请求里出现个人层字段 {path}.{k}：拒绝发送（规则 13）")
            assert_public(v, f"{path}.{k}")
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            assert_public(v, f"{path}[{i}]")
    elif isinstance(value, str):
        hits = pii_hits(value)
        if hits:
            raise CompileError(f"全量重编请求的 {path} 疑似个人信息（{'、'.join(hits)}）：拒绝发送（规则 13）")


def build_messages(payload: dict[str, Any]) -> tuple[dict[str, str], ...]:
    assert_public(payload)
    return ({"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "公共知识原子（JSON）：" + json.dumps(payload, ensure_ascii=False, sort_keys=True)})


def parse_output(text: str, page_type: str = "kp") -> dict[str, Section]:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise CompileError("模型输出不是 JSON 对象")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        raise CompileError("模型输出不是合法 JSON") from None
    specs = [s for s in SECTIONS_BY_TYPE[page_type] if s.kind == "narrative"]
    if not isinstance(data, dict) or set(data) != {s.id for s in specs}:
        raise CompileError(f"模型输出的键应为 {[s.id for s in specs]}")
    out = {}
    for spec in specs:
        body = data[spec.id]
        if not isinstance(body, str) or not body.strip() or len(body) > MAX_SECTION_CHARS:
            raise CompileError(f"叙述段 {spec.id} 为空或超长")
        if "<!--" in body or "\n---" in body or re.search(r"^#{1,2} ", body, re.M):
            raise CompileError(f"叙述段 {spec.id} 含段落标记、分隔线或一二级标题")
        out[spec.id] = Section(spec.id, "narrative", f"## {spec.title}\n\n{body.strip()}")
    return out


async def full_recompile(job: CompileJob, rel_path: str, old: Page, old_text: str, snapshot: Snapshot, known_pages: set[str],
                         router: Any, compiled_at: dt.datetime) -> CompileResult:
    if job.rebuild_kind != "full":
        raise CompileError("full_recompile 只接全量重编任务")
    mr = _router_module()
    node_id = str(old.header.get("node_id"))
    payload = public_payload(snapshot, node_id)
    key = f"kge-compile-{job.job_id}"
    req = mr.RouteRequest(task=mr.Task.T8, data_class=mr.DataClass.L0, subject=snapshot.subject, prompt_template=PROMPT_TEMPLATE,
                          prompt_version=PROMPT_VERSION, messages=build_messages(payload), usage_id=key, idempotency_key=key)
    res = await router.route(req)
    narrative = parse_output(res.output, str(old.header.get("page_type")))
    new = assemble(old, snapshot, narrative, known_pages)
    return finish(job, rel_path, old, old_text, new, "full", compiled_at,
                  ("全量重编：叙述段由模型重写，一律高风险，必须教研审核",), res.source_version)


def fake_router(behavior: Any, sink: Any = None) -> Any:
    """测试用：路由表 test 环境 + 进程内假供应商（不联网、不读凭据）"""
    mr = _router_module()
    cfg = mr.load(mr.DEFAULT_PATH, "test")
    used = sorted({t.vendor for r in cfg.routes.values() for t in r.targets})
    return mr.ModelRouter(cfg, {v: mr.FakeProvider(cfg.vendors[v], behavior) for v in used}, sink=sink or mr.MemorySink())
