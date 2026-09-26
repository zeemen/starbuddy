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
