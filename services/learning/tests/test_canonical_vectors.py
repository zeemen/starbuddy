"""规范化 JSON 的共享测试向量（冲刺 2-E）：学习服务的 canonical() 与 BFF 的 TS 实现读同一份向量。"""
import hashlib
import json
from pathlib import Path

from learning.fingerprint import canonical

VECTORS = Path(__file__).resolve().parents[3] / "packages" / "contracts" / "testdata" / "canonical-vectors.json"


def test_shared_canonical_vectors():
    doc = json.loads(VECTORS.read_text(encoding="utf-8"))
    assert len(doc["vectors"]) >= 9
    for v in doc["vectors"]:
        c = canonical(json.loads(v["input"]))
        assert c == v["canonical"], v["name"]
        assert hashlib.sha256(c.encode("utf-8")).hexdigest() == v["sha256"], v["name"]


def test_usage_id_is_part_of_the_learning_fingerprint():
    """spec 第 6 节：usage_id 在请求体里，参与学习服务的请求指纹；BFF 每次重试带同一个 usage_id（由 key 派生）。"""
    from learning.fingerprint import request_fingerprint

    body = {"context": {"idempotency_key": "k", "request_id": "r1", "usage_id": "u1"}, "attempt": {"x": 1}}
    same = {"context": {"idempotency_key": "k", "request_id": "r2", "usage_id": "u1"}, "attempt": {"x": 1}}
    other = {"context": {"idempotency_key": "k", "request_id": "r1", "usage_id": "u2"}, "attempt": {"x": 1}}
    assert request_fingerprint("grade", body) == request_fingerprint("grade", same)
    assert request_fingerprint("grade", body) != request_fingerprint("grade", other)
