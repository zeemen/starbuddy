"""请求体指纹与证据内容摘要。

指纹不含 context.request_id：重试时 request_id 可能变化，但作答内容不变。
默认值先归一（例如 grade 的 appeal 缺省等于 false），两种写法得到同一指纹（契约守护 N1）。
"""
from __future__ import annotations

import copy
import hashlib
import json


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(value: object) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def request_fingerprint(method: str, body: dict) -> str:
    b = copy.deepcopy(body)
    b.get("context", {}).pop("request_id", None)
    if method == "grade":
        b["appeal"] = b.get("appeal") is True
    return sha256_hex({"method": method, "body": b})
