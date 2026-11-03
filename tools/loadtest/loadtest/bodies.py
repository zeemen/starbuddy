"""PLT-20 新场景的请求构造与票据处理（纯函数，不碰网络；形状对照 services/bff/src 的 parse* 函数）。

- 再练 / 复测作答：取题返回 status = ready 时用它下发的封装票据（sbp1. / sbr1.）和一个合题型的作答；没有可作答的题时
  用（种子、到达、类别）派生的同前缀假票据——BFF 在解票据这一步回 422 TICKET_INVALID（计次之前），请求序列照样可复现。
  只在写路径未接上时（PLT-21 之前的本栈、--no-write-path 或缺依赖：没有到期条目或知识服务）记为预期结果；
  写路径接上时派生票据不算预期（expect_derived=False），按错误计；
- 计划完成 / 跳过：今日计划带快照任务 ID（ordered.items[].task_id）时用它；没有时用派生 ID（库函数回 22023 → BFF 422 INVALID，预期）；
- 诊断探针与放弃：请求体键集合与 BFF parseProbe / parseAbandon 完全一致。
请求体只含合成值；不读、不记响应里的题面与票据内容以外的任何东西（票据只原样回传，不进报告）。
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
TICKET_PREFIX = {"practice": "sbp1.", "retest": "sbr1."}   # services/bff/src/practice/ticket.ts
MAX_SEALED = 2400
# 本栈缺依赖时的预期结果（状态码, 问题码）：不算错误、单列（metrics 的 expected）
EXPECT_TICKET_INVALID = frozenset({(422, "TICKET_INVALID")})
EXPECT_PLAN_TASK_INVALID = frozenset({(422, "INVALID")})
# 月度计划（家长）：本月没有快照时要调洞察服务生成，本栈不起洞察服务 → 503 UNAVAILABLE。只在 BFF 访问日志里核对到
# generate=not_configured 时才算预期（baseline 压测后交叉核对，核对不到的改记为错误；审查 S3）
EXPECT_NO_INSIGHT = frozenset({(503, "UNAVAILABLE")})
# 预测：BFF 没有接预测客户端（main.ts 不设 trust.exams），在本栈和任何环境都一律 503 UNAVAILABLE——这一步只测鉴权、档案读取与拒绝路径
# （契约审查 4；不进 --journeys all）
EXPECT_PREDICTION_UNWIRED = frozenset({(503, "UNAVAILABLE")})
EXPECT_STEP_NOT_CURRENT = frozenset({(409, "STEP_NOT_CURRENT")})


def rfc3339(now: datetime | None = None) -> str:
    """毫秒精度、Z 结尾（BFF 的 RFC3339 正则：秒以下最多 6 位）"""
    t = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return t.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def answer_for(item: dict | None) -> dict:
    """合题型的作答：单选 / 多选取第一个选项，填空写 "1"（同诊断场景）"""
    item = item if isinstance(item, dict) else {}
    opts = item.get("options") or []
    if item.get("format") in ("single", "multi") and opts and isinstance(opts[0], dict) and isinstance(opts[0].get("key"), str):
        return {"choice": [opts[0]["key"]]}
    return {"text": "1"}


def ready_ticket(kind: str, body: object) -> tuple[str, dict] | None:
    """取题响应 → （封装票据, 作答）；不是 ready、票据前缀或长度不对 → None"""
    if not isinstance(body, dict) or body.get("status") != "ready":
        return None
    t = body.get("ticket")
    if not isinstance(t, str) or not t.startswith(TICKET_PREFIX[kind]) or len(t) > MAX_SEALED:
        return None
    return t, answer_for(body.get("item"))


def fallback_ticket(kind: str, seed: int, arrival: int) -> str:
    """派生的假票据：前缀与长度合 BFF 的形状检查，内容不可能通过解封（不含任何真实密钥材料）"""
    digest = hashlib.sha256(f"loadtest-fallback|{kind}|{seed}|{arrival}".encode()).hexdigest()
    return f"{TICKET_PREFIX[kind]}synthetic.{digest}"


def answer_request(kind: str, next_body: object, seed: int, arrival: int, *, expect_derived: bool = True) -> tuple[dict, frozenset]:
    """作答请求体与预期结果集：有真票据时不设预期（必须 2xx），否则用派生票据并预期 422 TICKET_INVALID。
    expect_derived=False（PLT-21：本栈写路径可用，取题应当 ready）：派生票据不再算预期，照发（请求序列不变）、结果按错误计"""
    got = ready_ticket(kind, next_body)
    if got is not None:
        ticket, answer = got
        return {"ticket": ticket, "answer": answer}, frozenset()
    return {"ticket": fallback_ticket(kind, seed, arrival), "answer": {"text": "1"}}, (EXPECT_TICKET_INVALID if expect_derived else frozenset())


def plan_task_ids(today: object) -> list[str]:
    """今日计划里快照任务 ID（只取 ordered.items[].task_id 里合 UUID 形状的，按出现顺序去重）"""
    if not isinstance(today, dict) or not isinstance(today.get("ordered"), dict):
        return []
    out: list[str] = []
    for it in today["ordered"].get("items") or []:
        t = it.get("task_id") if isinstance(it, dict) else None
        if isinstance(t, str) and UUID_RE.match(t) and t not in out:
            out.append(t)
    return out


def plan_event(kind: str, task_id: str, key: str, occurred_at: str) -> dict:
    if kind not in ("plan_done", "plan_skip"):
        raise ValueError("kind must be plan_done or plan_skip")
    return {"kind": kind, "payload": {"plan_task_id": task_id}, "occurred_at": occurred_at, "idempotency_key": key}


def plan_events(today: object, derived: dict[str, str], keys: dict[str, str], occurred_at: str) -> list[tuple[str, dict, frozenset]]:
    """（步骤, 请求体, 预期）×2：完成取第一个任务、跳过取第二个；缺的用派生 ID 并预期 422 INVALID"""
    ids = plan_task_ids(today)
    out = []
    for i, (step, kind) in enumerate((("plan.done", "plan_done"), ("plan.skip", "plan_skip"))):
        real = ids[i] if i < len(ids) else None
        out.append((step, plan_event(kind, real or derived[step], keys[step], occurred_at),
                    frozenset() if real else EXPECT_PLAN_TASK_INVALID))
    return out


def probe_body(view: object, key: str, asked_at: str) -> tuple[dict, frozenset]:
    """诊断探针：当前步骤的序号；会话不在进行中或没有当前步骤时仍发（序号 1），预期 409 STEP_NOT_CURRENT"""
    step = view.get("step") if isinstance(view, dict) else None
    seq = step.get("seq") if isinstance(step, dict) else None
    active = isinstance(view, dict) and view.get("status") == "active" and isinstance(seq, int) and 1 <= seq <= 100
    body = {"seq": seq if active else 1, "prediction": "unsure", "asked_at": asked_at, "idempotency_key": key}
    return body, frozenset() if active else EXPECT_STEP_NOT_CURRENT


def abandon_body(key: str) -> dict:
    return {"idempotency_key": key}
