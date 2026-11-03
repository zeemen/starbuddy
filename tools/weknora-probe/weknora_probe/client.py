"""WeKnora HTTP 客户端（只用标准库；探针与 evals/kge-compile 的 WeKnora 路径共用）。

接口形状按 WeKnora 官方文档（website-docs/04-api，2026-11-03 初读，v0.9.0）写成，未在真实实例上核对：
返回体的解析尽量宽容（`data` 包一层或不包、ID 字段名 `id` / `knowledge_id`），并把实际看到的字段名交给探针记录
（验证项 V11 就是核对这些形状）。第一次对真实测试库运行后，按探针报告修正这里的映射。

安全约束（技术验证计划第一节“底线”）：
- 地址与密钥只从环境变量读；密钥不进 repr、异常消息与报告；
- `from_env()` 要求 WEKNORA_TARGET=test，否则拒绝构造（只对测试知识库运行）；
- 异常消息只含方法、路径模板与状态码，不含请求体、返回体与查询文本。
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

ENV_BASE_URL = "WEKNORA_BASE_URL"
ENV_API_KEY = "WEKNORA_API_KEY"
ENV_TARGET = "WEKNORA_TARGET"
REQUIRED_TARGET = "test"
DEFAULT_TIMEOUT = 30.0


class ConfigError(RuntimeError):
    """环境没配好或不是测试目标（消息不含密钥）"""


class WeKnoraError(RuntimeError):
    """WeKnora 返回非 2xx 或网络失败。只带方法、路径模板与状态码"""

    def __init__(self, method: str, route: str, status: int | None, reason: str = "") -> None:
        self.method, self.route, self.status = method, route, status
        super().__init__(f"{method} {route} → {status if status is not None else '网络错误'}{('：' + reason) if reason else ''}")


@dataclass(frozen=True)
class SearchHit:
    chunk_id: str | None
    knowledge_id: str | None
    title: str | None
    content: str
    score: float | None
    metadata: Mapping[str, Any] | None
    raw_keys: tuple[str, ...]


@dataclass(frozen=True)
class SearchResult:
    hits: tuple[SearchHit, ...]
    elapsed_ms: float
    raw_keys: tuple[str, ...]          # 外层 data 的字段名（结果不是数组时）


Opener = Callable[[urllib.request.Request, float], Any]


def _default_opener(req: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310（地址来自运维配置的测试库）


@dataclass
class WeKnoraClient:
    base_url: str
    api_key: str = field(repr=False)
    timeout: float = DEFAULT_TIMEOUT
    opener: Opener = field(default=_default_opener, repr=False)

    # ---------- 构造 ----------
    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, *, key_var: str = ENV_API_KEY) -> "WeKnoraClient":
        e = os.environ if env is None else env
        if e.get(ENV_TARGET) != REQUIRED_TARGET:
            raise ConfigError(f"只对测试知识库运行：请设置 {ENV_TARGET}={REQUIRED_TARGET}")
        base, key = e.get(ENV_BASE_URL, "").strip(), e.get(key_var, "").strip()
        if not base or not key:
            raise ConfigError(f"缺少 {ENV_BASE_URL} 或 {key_var}")
        if not base.startswith(("http://", "https://")):
            raise ConfigError(f"{ENV_BASE_URL} 必须以 http:// 或 https:// 开头")
        return cls(base.rstrip("/"), key)

    def with_key(self, api_key: str | None) -> "WeKnoraClient":
        """同一地址换一把密钥（含不带密钥）：鉴权探针用"""
        return WeKnoraClient(self.base_url, api_key or "", self.timeout, self.opener)

    # ---------- 底层 ----------
    def request(self, method: str, route: str, path: str, *, body: Any = None,
                query: Mapping[str, Any] | None = None) -> tuple[int, Any]:
        """route 是路径模板（进异常消息），path 是实际路径。返回 (状态码, 解析后的 JSON 或 None)；非 2xx 抛 WeKnoraError"""
        url = self.base_url + path
        if query:
            q = {k: (",".join(map(str, v)) if isinstance(v, (list, tuple)) else v) for k, v in query.items() if v is not None}
            url += "?" + urllib.parse.urlencode(q)
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with self.opener(req, self.timeout) as resp:
                status, raw = resp.status, resp.read()
        except urllib.error.HTTPError as e:
            raise WeKnoraError(method, route, e.code) from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise WeKnoraError(method, route, None, type(e).__name__) from None
        if not 200 <= status < 300:
            raise WeKnoraError(method, route, status)
        if not raw:
            return status, None
        try:
            return status, json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise WeKnoraError(method, route, status, "返回体不是 JSON") from None

    @staticmethod
    def _data(doc: Any) -> Any:
        return doc.get("data", doc) if isinstance(doc, dict) else doc

    @staticmethod
    def _id(obj: Any) -> str | None:
        if not isinstance(obj, dict):
            return None
        for k in ("id", "knowledge_id", "ID"):
            if obj.get(k):
                return str(obj[k])
        return None

    # ---------- 知识（文档） ----------
    def create_manual(self, kb_id: str, title: str, content: str, *, tag_ids: Sequence[str] = ()) -> str:
        _, doc = self.request("POST", "/knowledge-bases/:id/knowledge/manual",
                              f"/api/v1/knowledge-bases/{_q(kb_id)}/knowledge/manual",
                              body={"title": title, "content": content, "status": "publish", "tag_ids": list(tag_ids)})
        kid = self._id(self._data(doc))
        if kid is None:
            raise WeKnoraError("POST", "/knowledge-bases/:id/knowledge/manual", 200, "返回里没有知识 ID")
        return kid

    def replace_manual(self, knowledge_id: str, content: str) -> None:
        self.request("PUT", "/knowledge/manual/:id", f"/api/v1/knowledge/manual/{_q(knowledge_id)}",
                     body={"content": content, "status": "publish"})

    def update_knowledge(self, knowledge_id: str, *, custom_metadata: Mapping[str, Any] | None = None,
                         title: str | None = None) -> None:
        body: dict[str, Any] = {}
        if title is not None:
            body["title"] = title
        if custom_metadata is not None:
            body["custom_metadata"] = dict(custom_metadata)
        self.request("PUT", "/knowledge/:id", f"/api/v1/knowledge/{_q(knowledge_id)}", body=body)

    def get_knowledge(self, knowledge_id: str) -> dict[str, Any]:
        _, doc = self.request("GET", "/knowledge/:id", f"/api/v1/knowledge/{_q(knowledge_id)}")
        d = self._data(doc)
        return d if isinstance(d, dict) else {}

    def delete_knowledge(self, knowledge_id: str) -> None:
        self.request("DELETE", "/knowledge/:id", f"/api/v1/knowledge/{_q(knowledge_id)}")

    def list_knowledge(self, kb_id: str, *, tag_ids: Sequence[str] = (), page_size: int = 100,
                       max_pages: int = 100) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            _, doc = self.request("GET", "/knowledge-bases/:id/knowledge", f"/api/v1/knowledge-bases/{_q(kb_id)}/knowledge",
                                  query={"page": page, "page_size": page_size, "tag_ids": list(tag_ids) or None})
            d = self._data(doc)
            items = d if isinstance(d, list) else (d.get("items") or d.get("list") or d.get("data") or []) if isinstance(d, dict) else []
            out.extend(x for x in items if isinstance(x, dict))
            if len(items) < page_size:
                break
        return out

    # ---------- 标签 ----------
    def create_tag(self, kb_id: str, name: str) -> str:
        _, doc = self.request("POST", "/knowledge-bases/:id/tags", f"/api/v1/knowledge-bases/{_q(kb_id)}/tags", body={"name": name})
        tid = self._id(self._data(doc))
        if tid is None:
            raise WeKnoraError("POST", "/knowledge-bases/:id/tags", 200, "返回里没有标签 ID")
        return tid

    def delete_tag(self, kb_id: str, tag_id: str) -> None:
        self.request("DELETE", "/knowledge-bases/:id/tags/:tag_id", f"/api/v1/knowledge-bases/{_q(kb_id)}/tags/{_q(tag_id)}")

    # ---------- 检索 ----------
    def knowledge_search(self, query: str, *, kb_ids: Sequence[str] = (), knowledge_ids: Sequence[str] = (),
                         tag_ids: Sequence[str] = (), match_count: int = 10, rerank: bool | None = None) -> SearchResult:
        body: dict[str, Any] = {"query": query, "match_count": match_count}
        if kb_ids:
            body["knowledge_base_ids"] = list(kb_ids)
        if knowledge_ids:
            body["knowledge_ids"] = list(knowledge_ids)
        if tag_ids:
            body["tag_ids"] = list(tag_ids)
        if rerank is not None:
            body["rerank"] = {"enabled": rerank}
        t0 = time.perf_counter()
        _, doc = self.request("POST", "/knowledge-search", "/api/v1/knowledge-search", body=body)
        elapsed = (time.perf_counter() - t0) * 1000
        d = self._data(doc)
        raw_keys: tuple[str, ...] = ()
        if isinstance(d, dict):
            raw_keys = tuple(sorted(d))
            d = d.get("results") or d.get("items") or d.get("chunks") or []
        hits = tuple(_hit(x) for x in (d or []) if isinstance(x, dict))
        return SearchResult(hits, elapsed, raw_keys)


def _q(s: str) -> str:
    return urllib.parse.quote(str(s), safe="")


def _hit(x: Mapping[str, Any]) -> SearchHit:
    def first(*names: str) -> Any:
        for n in names:
            if x.get(n) not in (None, ""):
                return x[n]
        return None
    score = first("score", "rerank_score", "similarity")
    md = first("custom_metadata", "metadata")
    return SearchHit(
        chunk_id=None if first("chunk_id", "id") is None else str(first("chunk_id", "id")),
        knowledge_id=None if first("knowledge_id") is None else str(first("knowledge_id")),
        title=first("knowledge_title", "title"),
        content=str(first("content", "text") or ""),
        score=float(score) if isinstance(score, (int, float)) else None,
        metadata=md if isinstance(md, dict) else None,
        raw_keys=tuple(sorted(x)),
    )


def wait_until(predicate: Callable[[], bool], *, timeout: float, interval: float = 2.0,
               sleep: Callable[[float], None] = time.sleep) -> bool:
    """WeKnora 导入后异步建索引：轮询直到 predicate 为真或超时"""
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        sleep(interval)
