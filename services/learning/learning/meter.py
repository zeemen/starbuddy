"""写入前的计量核对（specs/launch-lt04-learning-internal-v1 决定记录（续）第 2、3 条；迁移 0058 platform.learning_meter_check）。

learning-internal v1 签字后的防护是“服务身份限定 + 计量核对”：只有 BFF 服务令牌能调 /internal/v1 的 grade、diagnose，
且请求的 usage_id + 幂等键必须与计量账本里这名学生、这个方法的记录对得上。学习服务对计量表没有读权限，只能经只读函数
platform.learning_meter_check 拿到状态编码（不返回额度、数量与时间）。各路径放行的状态：
- 直接经 HTTP 的 /internal/v1/grade、/diagnose（BFF meteredCall，一次作答一笔预留）：reserved；
- 再练、复测作答（BFF 按题库自练计次 meter_practice）：counted；
- 诊断会话的步骤作答（一个会话一笔 diagnosis_full 预留，BFF 在第一道题判分成功后即结算）：按会话自己的 usage_id 与预留键、
  方法 diagnose 核对，reserved、settled 与 started_in_time（会话已结束或放弃由学习服务自己的会话状态挡住）。
  started_in_time（决定记录（再续）第 2 条，计量规则变更，待人确认见 lessons）：预留已过期（lapsed / expired），但这个会话
  （同 usage_id、幂等键、学生）创建时间不晚于预留过期时间——在有效期内开始的诊断，开始后超过有效期才作答仍可推进；
  会话创建于预留过期之后的仍是 lapsed / expired → 409。时间比较在 0058 函数里做，学习服务拿不到任何时间。
核对不通过一律 409 IDEMPOTENCY_KEY_SEALED（不改已签字契约的问题码枚举；契约描述注明“该幂等键无有效计量，不可写入”），不调适配器、不落库。
幂等重放（同键同内容、已成功）在核对之前返回已存结果，不受影响（BFF 结算之后的重放仍拿得回原结果）。

用途（purpose）与证据等级由学习服务推导，不接受调用方自报（决定第 3 条）：直接经 HTTP 的写入用途只能是 practice；
复测、迁移检验的用途只由学习服务内部按票据（复测锚点）写入。trusted_purpose 只在进程内调用方构造。
"""
from __future__ import annotations

from dataclasses import dataclass

from . import store
from .errors import IngestError
from .metrics import meter_checked

STATES = frozenset({"reserved", "lapsed", "started_in_time", "settled", "released", "expired", "counted", "none"})


@dataclass(frozen=True)
class MeterBasis:
    """一次写入按什么核对计量。usage_id / key / method 为 None 时取请求自己的 context.usage_id、幂等键与方法。"""
    allowed: frozenset[str]
    usage_id: str | None = None
    key: str | None = None
    method: str | None = None
    trusted_purpose: bool = False  # True：用途由学习服务按票据 / 会话写入（进程内调用方）；False：直接 HTTP，只能 practice


# 直接经 HTTP 的 /internal/v1/grade、/diagnose（缺省）
DIRECT = MeterBasis(frozenset({"reserved"}))
# 再练、复测作答：题库自练计次
COUNTED = MeterBasis(frozenset({"counted"}), trusted_purpose=True)


def diagnosis_session(usage_id: str, reservation_key: str) -> MeterBasis:
    """诊断会话的步骤作答：会话自己的 usage_id 与预留键（diagnosis_session.idempotency_key），方法 diagnose；
    预留在会话开始后才过期的（started_in_time）同样放行。"""
    return MeterBasis(frozenset({"reserved", "settled", "started_in_time"}), usage_id=usage_id, key=reservation_key, method="diagnose",
                      trusted_purpose=True)


SEALED = "idempotency_key has no valid metering record; it cannot be written"


async def require(pool, basis: MeterBasis, *, method: str, ctx: dict) -> None:
    """核对不通过 → 409 IDEMPOTENCY_KEY_SEALED（确定没有写入，BFF 释放预留）。"""
    usage = basis.usage_id if basis.usage_id is not None else ctx.get("usage_id")
    key = basis.key if basis.key is not None else ctx.get("idempotency_key")
    state = await store.meter_check(pool, usage_id=usage, key=key, student_id=ctx.get("student_ref"),
                                    method=basis.method or method)
    meter_checked(state in basis.allowed)  # LT-05：放行 / 409 计数（只记结果，不记键与学生）
    if state not in basis.allowed:
        raise IngestError(409, "IDEMPOTENCY_KEY_SEALED", SEALED)
