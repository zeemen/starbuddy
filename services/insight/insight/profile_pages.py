"""周报触发学情档案页编译（KGE-04 第二步 PR 4；第一步 spec 第 14 节“惰性编译”的触发点 ②，第二步待人确认 #6 默认）。

洞察服务生成周报前，按学生、按周报可见学科调学习服务 `POST /internal/v1/profile-page/ensure`（learning-internal 1.1.0 草稿内追加，
insightServiceToken，`trigger = weekly`）。档案页仍只由学习服务编译与写入（洞察服务对档案页表没有任何权限）；判断没变时学习服务不重编。
- 失败（超时、非 200、学习服务没配档案页、连接失败）不阻塞周报：只计数（ok / failed），不抛出；
- 超时：连接 2 秒、单次 5 秒，另加整次调用总时长上限（缺省 5 秒）；
- 不跟随重定向、不读代理环境变量；日志与计数里没有学生 ID；响应里本来也没有判断与文字。
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

TRIGGER = "weekly"
SUBJECTS = ("aya", "zhixing", "wenli")   # 英语搁置


class ProfilePageTrigger(Protocol):
    async def ensure(self, student: str, subject: str) -> bool: ...


@dataclass
class LearningProfilePages:
    base_url: str
    token: str
    transport: Any = None
    total_timeout: float = 5.0
    counts: dict[str, int] = field(default_factory=lambda: {"ok": 0, "failed": 0})

    async def ensure(self, student: str, subject: str) -> bool:
        if subject not in SUBJECTS:
            return False
        body = {"request_id": str(uuid.uuid4()), "student_ref": str(uuid.UUID(student)), "subject": subject, "trigger": TRIGGER}

        async def go() -> httpx.Response:
            async with httpx.AsyncClient(transport=self.transport, trust_env=False, follow_redirects=False,
                                         timeout=httpx.Timeout(5.0, connect=2.0)) as c:
                return await c.post(f"{self.base_url}/internal/v1/profile-page/ensure", json=body,
                                    headers={"authorization": f"Bearer {self.token}"})
        try:
            r = await asyncio.wait_for(go(), self.total_timeout)
            ok = r.status_code == 200 and isinstance(r.json(), dict) and r.json().get("subject") == subject
        except Exception:  # noqa: BLE001 — 失败不阻塞周报，只计数
            ok = False
        self.counts["ok" if ok else "failed"] += 1
        return ok


def learning_profile_pages(learning: tuple[str, str] | None) -> LearningProfilePages | None:
    """配置了学习服务（地址, 令牌）就用它；没配置返回 None（周报照常生成，不触发档案页编译）"""
    return LearningProfilePages(*learning) if learning is not None else None
