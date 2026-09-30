"""选题用的公共层信息（engine.Source 的两种实现）。

- KnowledgeServiceSource：经知识服务（packages/contracts/openapi/knowledge-service.v0.9.yaml）取：
  node.path（direction = up，前置链；知识服务只走强前置边）与 item.search（题目标签）。调用方身份 learning，
  服务间令牌与学习服务其他出站调用同一做法（Bearer，不跟随重定向、不读代理环境变量）。
  响应按契约严格校验（NodePathResponse / ItemSearchResponse，契约审查建议 3）；前置链有一步的深度到了 max_depth，
  说明链可能被截断，报 SourceError（不当作“没有更下层前置”）；版权状态 restricted 的题不出；
  item.search 没有翻页，每个节点最多取 50 道（契约上限，lessons 已写明）。
  目标节点（建档年级与教材进度对应的 kp 节点）知识服务没有按主题列节点的工具：由配置给出（待人确认 #1、#3）。
- FixtureSource：测试环境的合成夹具（fixtures/quad-synthetic.json，节点在 MATH.SYN.* 命名空间，不与真实地图重名），
  按知识服务的口径只取强前置边；非 test 环境拒绝构造。

两者对同一张图给出相同的前置、闭包、层高与题目顺序（tests/test_diagnosis_engine.py 的知识服务来源用例核对）。
"""
from __future__ import annotations

import json
import re
import uuid
from functools import lru_cache
from pathlib import Path

import httpx

from ..contract import knowledge_contract

FIXTURE = Path(__file__).with_name("fixtures") / "quad-synthetic.json"
NODE_RE = re.compile(r"^[A-Z][A-Z0-9]{1,9}(\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}$")
ITEM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")
MAX_DEPTH = 10
ITEM_LIMIT = 50  # item.search 的契约上限（没有翻页）


class SourceError(Exception):
    """知识服务不可用或返回不合要求：诊断这一步不继续（结果未知，可重试）。"""


def require_test_env(env: str, what: str) -> None:
    if env != "test":
        raise RuntimeError(f"{what} 只能在 LEARNING_ENV=test 下使用（合成夹具，契约签字前不接真实题库）")


class _Graph:
    """由强前置边（from = 前置，to = 后续）算直接前置、传递闭包与层高。"""

    def __init__(self, strong_edges: list[tuple[str, str]], items: dict[str, list[str]], targets: dict[int, list[str]],
                 map_version: str) -> None:
        self.map_version = map_version
        self._prereqs: dict[str, list[str]] = {}
        for f, t in strong_edges:
            self._prereqs.setdefault(t, [])
            if f not in self._prereqs[t]:
                self._prereqs[t].append(f)
        for v in self._prereqs.values():
            v.sort()
        self._items = items
        self._targets = targets

    def targets(self, grade: int) -> list[str]:
        return list(self._targets.get(grade, []))

    def prereqs(self, node_id: str) -> list[str]:
        return list(self._prereqs.get(node_id, []))

    def ancestors(self, node_id: str) -> frozenset[str]:
        seen: set[str] = set()
        stack = list(self.prereqs(node_id))
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            stack.extend(self.prereqs(n))
        return frozenset(seen)

    def height(self, node_id: str) -> int:
        return self._height(node_id, frozenset())

    def _height(self, node_id: str, path: frozenset[str]) -> int:
        if node_id in path:  # 地图体检保证无环；这里再防一道
            return 0
        ps = self.prereqs(node_id)
        return 0 if not ps else 1 + max(self._height(q, path | {node_id}) for q in ps)

    def items(self, node_id: str) -> list[str]:
        return list(self._items.get(node_id, []))


@lru_cache(maxsize=4)
def load_fixture(path: str = str(FIXTURE)) -> dict:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if doc.get("format") != "starbuddy-diagnosis-fixture/1":
        raise RuntimeError("诊断夹具格式不对")
    for it in doc["items"]:
        if set(it) - {"item_id", "node_id", "format", "stem", "options"} or not ITEM_RE.fullmatch(it["item_id"]):
            raise RuntimeError("诊断夹具的题目只能有题号、节点、题型、题面、选项（不能带答案）")
    return doc


class FixtureSource(_Graph):
    def __init__(self, env: str, doc: dict | None = None) -> None:
        require_test_env(env, "合成诊断夹具")
        doc = doc or load_fixture()
        strong = [(e["from"], e["to"]) for e in doc["edges"] if e["strength"] == "strong"]
        items: dict[str, list[str]] = {}
        for it in doc["items"]:
            items.setdefault(it["node_id"], []).append(it["item_id"])
        items = {n: sorted(v) for n, v in items.items()}  # 与知识服务 item.search 同一顺序（按题号）
        super().__init__(strong, items, {int(g): list(v) for g, v in doc["targets"].items()}, doc["map_version"])
        self.doc = doc


class KnowledgeServiceSource(_Graph):
    """经知识服务取图与题目。一次会话开始时按目标节点把用到的前置链一次取全（node.path max_depth = 10），之后只读缓存。"""

    def __init__(self, client: httpx.AsyncClient, base_url: str, token: str, subject: str, targets: dict[int, list[str]],
                 map_version: str) -> None:
        super().__init__([], {}, targets, map_version)
        self._client = client
        self._base = base_url.rstrip("/")
        self._token = token
        self._subject = subject
        self._loaded: set[str] = set()

    async def _call(self, tool: str, body: dict) -> dict:
        body = {"context": {"request_id": str(uuid.uuid4()), "contract_version": "0.9.0", "caller": "learning", "data_class": "L1",
                            "map_version": self.map_version}, **body}
        try:
            r = await self._client.post(f"{self._base}/v0/{tool}", json=body, headers={"authorization": f"Bearer {self._token}"},
                                        timeout=10.0)
        except httpx.HTTPError:
            raise SourceError(f"{tool} unavailable") from None
        if r.status_code != 200:
            raise SourceError(f"{tool} status {r.status_code}")
        try:
            out = r.json()
        except ValueError:
            raise SourceError(f"{tool} malformed") from None
        schema = {"node.path": "NodePathResponse", "item.search": "ItemSearchResponse"}[tool]
        if knowledge_contract().validate(schema, out):
            raise SourceError(f"{tool} response does not match the contract")
        return out["result"]

    async def load(self, grade: int) -> None:
        """取目标节点的前置链与链上每个节点的题目。只接受深度 1…10、ID 合规、方向为 up 的步。"""
        for root in self.targets(grade):
            if root in self._loaded:
                continue
            res = await self._call("node.path", {"node_id": root, "direction": "up", "max_depth": MAX_DEPTH})
            if res.get("root") != root or res.get("direction") != "up":
                raise SourceError("node.path answered a different question")
            nodes = {root}
            for s in res.get("steps", []):
                f, t = s.get("from_id"), s.get("to_id")
                if not (isinstance(f, str) and isinstance(t, str) and NODE_RE.fullmatch(f) and NODE_RE.fullmatch(t)):
                    raise SourceError("node.path step malformed")
                if s.get("depth", 0) >= MAX_DEPTH:
                    # 到了最大深度：更下层可能还有前置，只是没返回；不能当作“没有前置”去判断点
                    raise SourceError("node.path reached max_depth; the prerequisite chain may be truncated")
                self._prereqs.setdefault(t, [])
                if f not in self._prereqs[t]:
                    self._prereqs[t].append(f)
                    self._prereqs[t].sort()
                nodes |= {f, t}
            for n in sorted(nodes - self._loaded):
                found = await self._call("item.search", {"subject": self._subject, "node_id": n, "limit": ITEM_LIMIT})
                refs = sorted(i["item_ref"].split(":", 1)[1] for i in found.get("items", [])
                              if i.get("main_node_id") == n and i.get("item_ref", "").startswith(f"{self._subject}:")
                              and i.get("license_state") != "restricted")
                self._items[n] = refs
                self._loaded.add(n)
            self._loaded.add(root)
