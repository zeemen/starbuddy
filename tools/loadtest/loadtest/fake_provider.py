"""本机栈里的假模型供应商（OpenAI 兼容协议，只监听 127.0.0.1）：学习服务的模型路由按 test 环境路由表经
OpenAICompatProvider 调它，所以供应商适配器的超时、429 Retry-After、5xx 分类都走真实代码路径。

- 路径 /<供应商 ID>/chat/completions（路由表里 MODEL_FAKE_{A,B,X}_URL 各指向一个前缀）；
- 答复：与 e2e_cohort.scripted.ScriptedModel 同一套规则认出已登记的合成作答，给结构化判定；
- token 数：按消息字符数估算（CHARS_PER_TOKEN，合成估算，报告里标注），只作成本模型的输入；
- 延迟与故障：faults.FaultController（基础延迟分布 + 注入规则），运行中经 /_control/faults 切换；
- 计数只有类别（ok / server / rate_limited / timeout），不记请求内容与答复。
用法：python -m loadtest.fake_provider --port P --seed S --students N [--median-ms 300 --p90-ms 900]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math

from .answers import for_student
from .faults import FaultController, FaultSpec, LatencyModel

CHARS_PER_TOKEN = 1.5  # 估算口径：中文约 1–1.5 字符 / token；真实值以供应商返回的 usage 为准（RUNBOOK 第 3 节）
VENDORS = ("fake-a", "fake-b", "fake-x")
MAX_BODY = 2 * 1024 * 1024


def estimate_tokens(chars: int) -> int:
    return max(1, math.ceil(chars / CHARS_PER_TOKEN))


def build_model(seed: int, students: int):
    from e2e_cohort.replay import load_zhixing_items
    from e2e_cohort.scripted import ScriptedModel

    items = load_zhixing_items()
    model = ScriptedModel(items)
    points = {k: v["points"] for k, v in items.items()}
    for slot in range(students):
        a = for_student(seed, slot, points)
        model.register("zhixing", a.zhixing_item, a.zhixing_text, a.pattern)
        model.register("wenli", a.wenli_item, a.wenli_text, a.pattern)
    return model


class App:
    def __init__(self, controller: FaultController, model) -> None:
        self.controller = controller
        self.model = model

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "lifespan":
            while True:
                msg = await receive()
                if msg["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif msg["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http":
            return
        body = b""
        while True:
            msg = await receive()
            body += msg.get("body", b"")
            if len(body) > MAX_BODY:
                return await _json(send, 413, {"error": "too large"})
            if not msg.get("more_body"):
                break
        path, method = scope["path"], scope["method"]
        if path == "/_control/faults" and method == "POST":
            return await self.control(send, body)
        if path == "/_control/stats" and method == "GET":
            return await _json(send, 200, {"counts": self.controller.counts,
                                            "specs": {v: s.__dict__ for v, s in self.controller.specs.items()}})
        parts = path.strip("/").split("/")
        if method == "POST" and len(parts) == 3 and parts[0] in VENDORS and parts[1:] == ["chat", "completions"]:
            return await self.complete(send, parts[0], body)
        return await _json(send, 404, {"error": "not found"})

    async def control(self, send, body: bytes) -> None:
        try:
            req = json.loads(body)
            if req.get("clear"):
                self.controller.clear(req.get("vendor"))
            else:
                if req["vendor"] not in VENDORS:
                    raise ValueError
                self.controller.set(req["vendor"], FaultSpec.parse(req["spec"]))
        except (ValueError, KeyError, TypeError):
            return await _json(send, 400, {"error": "bad control request"})
        return await _json(send, 200, {"status": "ok"})

    async def complete(self, send, vendor: str, body: bytes) -> None:
        d = self.controller.decide(vendor)
        if d.kind == "timeout":
            await asyncio.sleep(d.delay_s)
            return await _json(send, 504, {"error": "timeout"})
        if d.delay_s > 0:
            await asyncio.sleep(d.delay_s)
        if d.kind == "server":
            return await _json(send, 503 if self.controller.spec(vendor).mode == "unavailable" else 500, {"error": "unavailable"})
        if d.kind == "rate_limited":
            headers = [(b"retry-after", str(d.retry_after_s).encode())] if d.retry_after_s is not None else []
            return await _json(send, 429, {"error": "rate limited"}, headers)
        try:
            req = json.loads(body)
            messages = req["messages"]
            chars = sum(len(m.get("content", "")) for m in messages)
            output = self.model.reply(messages[-1]["content"])
        except (ValueError, KeyError, TypeError, IndexError):
            return await _json(send, 400, {"error": "bad request"})
        return await _json(send, 200, {
            "model": req.get("model"),
            "choices": [{"index": 0, "message": {"role": "assistant", "content": output}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": estimate_tokens(chars), "completion_tokens": estimate_tokens(len(output))},
        })


async def _json(send, status: int, obj: dict, headers: list | None = None) -> None:
    raw = json.dumps(obj, ensure_ascii=False).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(raw)).encode())] + (headers or [])})
    await send({"type": "http.response.body", "body": raw})


def main() -> None:
    import uvicorn

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--seed", type=int, default=20260930)
    ap.add_argument("--students", type=int, default=0)
    ap.add_argument("--median-ms", type=float, default=300.0)
    ap.add_argument("--p90-ms", type=float, default=900.0)
    args = ap.parse_args()
    controller = FaultController(seed=args.seed, latency=LatencyModel(args.median_ms, args.p90_ms))
    app = App(controller, build_model(args.seed, args.students))
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning", access_log=False, lifespan="on")


if __name__ == "__main__":
    main()
