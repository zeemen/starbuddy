"""库层处理判据（LT-06 决定记录（续）第 4 条，迁移 0059 platform.student_processing_allowed）。

学习服务的写入与模型处理入口（/internal/v1 判分与诊断写入、诊断会话、再练、复测、今日编排与今日计划、档案页 ensure、验证任务）
在动任何东西之前先问库：档案存在、未删除、未退出内部测试、有有效同意才继续；否则一律 404 STUDENT_NOT_FOUND（learning-internal 契约
已有的问题码，与“学生不存在”同一个，不新增问题码、不泄露退出状态）。掌握对账（mastery-sweep）按同一判据过滤选人。

判据函数只授学习、洞察、调度三个服务角色，不依赖调用者身份（不读 app.account_id）。函数不存在时（迁移 0059 未执行）按旧行为放行：
与迁移执行之前逐字节一致；执行之后无条件生效（启动时查一次，见 available）。
"""
from __future__ import annotations

import re
from typing import Any

from .errors import IngestError

FUNCTION = "platform.student_processing_allowed(uuid)"
ALLOWED_SQL = "SELECT platform.student_processing_allowed($1::uuid)"
# 只拦“档案存在但判据为假”的学生：不存在的学生照旧交给各模块原来的判断（条目、票据不存在等 404 与原来逐字一致）
BLOCKED_SQL = ("SELECT EXISTS (SELECT 1 FROM platform.student_profile WHERE id = $1::uuid) "
               "AND NOT platform.student_processing_allowed($1::uuid)")
# 选人用：在批量任务的学生集合上过滤（不改原查询的排序与去重）
FILTER_SQL = "platform.student_processing_allowed({col})"
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
NOT_AVAILABLE = "student is not available for processing"


async def available(pool: Any) -> bool:
    """迁移 0059 是否已执行、本角色能否执行判据函数（启动时查一次）"""
    if pool is None:
        return False
    return bool(await pool.fetchval(
        "SELECT to_regprocedure($1) IS NOT NULL AND has_function_privilege(current_user, to_regprocedure($1), 'EXECUTE')", FUNCTION))


async def allowed(conn_or_pool: Any, student: str) -> bool:
    return bool(await conn_or_pool.fetchval(ALLOWED_SQL, student))


async def require(pool: Any, enabled: bool, student: object) -> None:
    """enabled：判据可用（app.py 启动时按 available 设）；不是 UUID 的学生交给原来的参数校验（400 / 404），这里不先拦"""
    if not enabled or pool is None or not isinstance(student, str):
        return
    s = student.lower()
    if not UUID_RE.fullmatch(s):
        return
    if await pool.fetchval(BLOCKED_SQL, s):
        raise IngestError(404, "STUDENT_NOT_FOUND", NOT_AVAILABLE)


def gate(state: dict):
    """API 模块用：state["pool"] 与 state["processing"]（启动时设）"""
    async def check(student: object) -> None:
        await require(state.get("pool"), bool(state.get("processing")), student)
    return check
