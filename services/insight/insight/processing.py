"""库层处理判据（LT-06 决定记录（续）第 4 条，迁移 0059 platform.student_processing_allowed）在洞察服务批量任务里的过滤。

周报（含档案页编译触发）、中圈指标、掌握同步的选人只取判据为真的学生：档案存在、未删除、未退出内部测试、有有效同意。
显式给出的学生（--student）同样过滤，不因为调用方点名就处理已退出的学生。判据函数不依赖调用者身份，只授学习、洞察、调度三个服务角色。
函数不存在时（迁移 0059 未执行）按旧行为不过滤：与迁移执行之前一致；执行之后无条件生效。
"""
from __future__ import annotations

from typing import Any, Sequence

FUNCTION = "platform.student_processing_allowed(uuid)"


async def available(conn: Any) -> bool:
    return bool(await conn.fetchval(
        "SELECT to_regprocedure($1) IS NOT NULL AND has_function_privilege(current_user, to_regprocedure($1), 'EXECUTE')", FUNCTION))


async def keep_allowed(conn: Any, students: Sequence[str]) -> list[str]:
    """按原顺序只留判据为真的学生；判据不可用时原样返回"""
    if not students or not await available(conn):
        return list(students)
    rows = await conn.fetch(
        "SELECT s FROM unnest($1::uuid[]) WITH ORDINALITY AS u(s, n) WHERE platform.student_processing_allowed(u.s) ORDER BY u.n",
        list(students))
    return [str(r["s"]) for r in rows]
