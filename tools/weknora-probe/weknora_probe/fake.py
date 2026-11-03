"""进程内的模拟 WeKnora（只给测试用；不是 WeKnora 的实现，也不代表它的真实行为）。

按 2026-11-03 初读的官方文档模拟验证计划关心的那部分接口与语义：
- 鉴权：X-API-Key；每把密钥有能力集合（retrieve / ingest）与可选的知识库白名单；
- 手工录入、整篇替换、改元数据（custom_metadata 最多 20 个字段）、删除、列表、标签；
- 检索 POST /api/v1/knowledge-search：只能按 knowledge_base_ids、knowledge_ids、tag_ids 过滤，标签之间是“或”；
  打分是查询与切块的汉字二元组重合率（确定性，只为测试）；
- 切块：按空行切，`$$…$$` 公式块与 Markdown 表格整块保留。

`Defects` 里的开关故意制造缺陷，测试用它证明探针能发现问题（变异测试的思路）。
"""
from __future__ import annotations

import json
import re
import threading
import unicodedata
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

METADATA_MAX_FIELDS = 20
_DROP = re.compile(r"[\s\W_]+", re.UNICODE)


@dataclass
class Defects:
    ignore_filters: bool = False      # 检索不按过滤条件（V07 应失败）
    any_key: bool = False             # 不校验密钥（V03 应失败）
    stale_replace: bool = False       # 整篇替换返回成功但内容不变（V09 应失败）
    keep_deleted: bool = False        # 删除返回成功但仍能检索到（V09 应失败）
    mutate_on_search: bool = False    # 检索时改动文档（V18 应失败）
    split_long: int = 0               # >0 时把切块再按这个长度硬切（公式、表格会被切断，V16 应失败）
    no_metadata_limit: bool = False   # 不限制元数据字段数


@dataclass
class Doc:
    id: str
    kb_id: str
    title: str
    content: str
    tag_ids: list[str]
    custom_metadata: dict[str, Any] = field(default_factory=dict)
    revision: int = 1
    seq: int = 0                      # 写入顺序：分数相同时按它排，结果确定


@dataclass
class State:
    kbs: set[str] = field(default_factory=set)
    keys: dict[str, dict[str, Any]] = field(default_factory=dict)   # key → {"caps": set, "kbs": set | None}
    docs: dict[str, Doc] = field(default_factory=dict)
    tags: dict[str, dict[str, str]] = field(default_factory=dict)  # tag_id → {"kb_id", "name"}
    queries: list[str] = field(default_factory=list)               # 只在内存里；探针看不到（V17 要人工核对真实实例）
    defects: Defects = field(default_factory=Defects)
    next_seq: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)


def _norm(s: str) -> str:
    return _DROP.sub("", unicodedata.normalize("NFKC", s)).lower()


def _bigrams(s: str) -> set[str]:
    n = _norm(s)
    return {n[i:i + 2] for i in range(len(n) - 1)} or ({n} if n else set())


def chunk(content: str, split_long: int = 0) -> list[str]:
    """按空行切块；公式块（$$ … $$）与连续的表格行（以 | 开头）不在中间切"""
    blocks: list[str] = []
    cur: list[str] = []
    in_formula = False
    for line in content.split("\n"):
        s = line.strip()
        if s.startswith("$$") and not (s.endswith("$$") and len(s) > 2):
            in_formula = not in_formula
        if not s and not in_formula:
            if cur:
                blocks.append("\n".join(cur))
                cur = []
            continue
        cur.append(line)
    if cur:
        blocks.append("\n".join(cur))
    if split_long > 0:
        blocks = [b[i:i + split_long] for b in blocks for i in range(0, len(b), split_long)]
    return blocks


class FakeWeKnora:
    """with FakeWeKnora() as fk: fk.base_url …；fk.state 可直接布置知识库、密钥与缺陷"""

    def __init__(self, state: State | None = None) -> None:
        self.state = state or State()
        self._srv: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ---------- 布置 ----------
    def add_kb(self, kb_id: str | None = None) -> str:
        kid = kb_id or f"kb-{uuid.uuid4().hex[:8]}"
        self.state.kbs.add(kid)
        return kid

    def add_key(self, key: str, caps: set[str], kbs: set[str] | None = None) -> None:
        self.state.keys[key] = {"caps": set(caps), "kbs": None if kbs is None else set(kbs)}

    # ---------- 生命周期 ----------
    def __enter__(self) -> "FakeWeKnora":
        state = self.state

        class Handler(_Handler):
            st = state

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._srv:
            self._srv.shutdown()
            self._srv.server_close()

    @property
    def base_url(self) -> str:
        assert self._srv is not None
        host, port = self._srv.server_address[:2]
        return f"http://{host}:{port}"


class _Handler(BaseHTTPRequestHandler):
    st: State

    def log_message(self, *args: Any) -> None:  # 不打印请求
        pass

    # ---------- 工具 ----------
    def _send(self, status: int, data: Any = None) -> None:
        body = json.dumps({"success": 200 <= status < 300, "data": data}, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, Any]:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            d = json.loads(self.rfile.read(n).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return d if isinstance(d, dict) else {}

    def _auth(self, cap: str, kb_ids: set[str] | None = None) -> bool:
        if self.st.defects.any_key:
            return True
        k = self.st.keys.get(self.headers.get("X-API-Key") or "")
        if k is None:
            self._send(401)
            return False
        if cap not in k["caps"] or (k["kbs"] is not None and kb_ids is not None and not kb_ids <= k["kbs"]):
            self._send(403)
            return False
        return True

    def _allowed_kbs(self) -> set[str] | None:
        k = self.st.keys.get(self.headers.get("X-API-Key") or "")
        return None if (k is None or self.st.defects.any_key) else k["kbs"]

    def _doc_view(self, d: Doc) -> dict[str, Any]:
        return {"id": d.id, "knowledge_base_id": d.kb_id, "title": d.title, "tag_ids": list(d.tag_ids),
                "custom_metadata": dict(d.custom_metadata), "revision": d.revision, "parse_status": "completed"}

    # ---------- 路由 ----------
    def _route(self, method: str) -> None:
        u = urlsplit(self.path)
        parts = [p for p in u.path.split("/") if p]
        if parts[:2] != ["api", "v1"]:
            return self._send(404)
        p = parts[2:]
        with self.st.lock:
            if method == "POST" and len(p) == 4 and p[0] == "knowledge-bases" and p[2:] == ["knowledge", "manual"]:
                return self._create_manual(p[1])
            if method == "POST" and len(p) == 3 and p[0] == "knowledge-bases" and p[2] == "tags":
                return self._create_tag(p[1])
            if method == "DELETE" and len(p) == 4 and p[0] == "knowledge-bases" and p[2] == "tags":
                return self._delete_tag(p[1], p[3])
            if method == "GET" and len(p) == 3 and p[0] == "knowledge-bases" and p[2] == "knowledge":
                return self._list(p[1], parse_qs(u.query))
            if method == "PUT" and len(p) == 3 and p[:2] == ["knowledge", "manual"]:
                return self._replace(p[2])
            if len(p) == 2 and p[0] == "knowledge":
                if method == "GET":
                    return self._get(p[1])
                if method == "PUT":
                    return self._update(p[1])
                if method == "DELETE":
                    return self._delete(p[1])
            if method == "POST" and p == ["knowledge-search"]:
                return self._search()
        return self._send(404)

    def do_GET(self) -> None:  # noqa: N802
        self._route("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._route("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._route("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._route("DELETE")

    # ---------- 处理 ----------
    def _create_manual(self, kb_id: str) -> None:
        if kb_id not in self.st.kbs:
            return self._send(404)
        if not self._auth("ingest", {kb_id}):
            return
        b = self._body()
        if not isinstance(b.get("title"), str) or not isinstance(b.get("content"), str):
            return self._send(400)
        tags = [t for t in b.get("tag_ids") or [] if t in self.st.tags]
        d = Doc(uuid.uuid4().hex, kb_id, b["title"], b["content"], tags, seq=self.st.next_seq)
        self.st.next_seq += 1
        self.st.docs[d.id] = d
        self._send(201, self._doc_view(d))

    def _create_tag(self, kb_id: str) -> None:
        if kb_id not in self.st.kbs:
            return self._send(404)
        if not self._auth("ingest", {kb_id}):
            return
        name = self._body().get("name")
        if not isinstance(name, str) or not name:
            return self._send(400)
        tid = uuid.uuid4().hex[:12]
        self.st.tags[tid] = {"kb_id": kb_id, "name": name}
        self._send(201, {"id": tid, "name": name})

    def _delete_tag(self, kb_id: str, tag_id: str) -> None:
        t = self.st.tags.get(tag_id)
        if t is None or t["kb_id"] != kb_id:
            return self._send(404)
        if not self._auth("ingest", {kb_id}):
            return
        del self.st.tags[tag_id]
        for d in self.st.docs.values():
            if tag_id in d.tag_ids:
                d.tag_ids.remove(tag_id)
        self._send(200, None)

    def _doc_or_404(self, kid: str, cap: str) -> Doc | None:
        d = self.st.docs.get(kid)
        if d is None:
            self._send(404)
            return None
        if not self._auth(cap, {d.kb_id}):
            return None
        return d

    def _get(self, kid: str) -> None:
        d = self._doc_or_404(kid, "retrieve")
        if d:
            self._send(200, self._doc_view(d) | {"content": d.content})

    def _list(self, kb_id: str, q: dict[str, list[str]]) -> None:
        if kb_id not in self.st.kbs:
            return self._send(404)
        if not self._auth("retrieve", {kb_id}):
            return
        tags = set(",".join(q.get("tag_ids", [])).split(",")) - {""}
        page, size = int((q.get("page") or ["1"])[0]), int((q.get("page_size") or ["100"])[0])
        docs = sorted((d for d in self.st.docs.values() if d.kb_id == kb_id and (not tags or tags & set(d.tag_ids))),
                      key=lambda d: d.id)
        self._send(200, [self._doc_view(d) for d in docs[(page - 1) * size: page * size]])

    def _replace(self, kid: str) -> None:
        d = self._doc_or_404(kid, "ingest")
        if not d:
            return
        c = self._body().get("content")
        if not isinstance(c, str):
            return self._send(400)
        if not self.st.defects.stale_replace:
            d.content = c
        d.revision += 1
        self._send(200, self._doc_view(d))

    def _update(self, kid: str) -> None:
        d = self._doc_or_404(kid, "ingest")
        if not d:
            return
        b = self._body()
        md = b.get("custom_metadata")
        if md is not None:
            if not isinstance(md, dict) or (len(md) > METADATA_MAX_FIELDS and not self.st.defects.no_metadata_limit):
                return self._send(400)
            if any(not isinstance(k, str) or not 1 <= len(k) <= 64 for k in md):
                return self._send(400)
            d.custom_metadata = dict(md)
        if isinstance(b.get("title"), str):
            d.title = b["title"]
        self._send(200, self._doc_view(d))

    def _delete(self, kid: str) -> None:
        d = self._doc_or_404(kid, "ingest")
        if not d:
            return
        if not self.st.defects.keep_deleted:
            del self.st.docs[kid]
        self._send(200, {"task_id": uuid.uuid4().hex[:8]})

    def _search(self) -> None:
        b = self._body()
        query = b.get("query")
        if not isinstance(query, str) or not query.strip():
            return self._send(400)
        kbs = set(b.get("knowledge_base_ids") or [])
        if not self._auth("retrieve", kbs or None):
            return
        allowed = self._allowed_kbs()
        kids, tags = set(b.get("knowledge_ids") or []), set(b.get("tag_ids") or [])
        self.st.queries.append(query)
        qg = _bigrams(query)
        hits = []
        for d in self.st.docs.values():
            if allowed is not None and d.kb_id not in allowed:
                continue
            if not self.st.defects.ignore_filters:
                if kbs and d.kb_id not in kbs:
                    continue
                if kids and d.id not in kids:
                    continue
                if tags and not tags & set(d.tag_ids):     # 标签之间是“或”
                    continue
            for i, c in enumerate(chunk(d.content, self.st.defects.split_long)):
                cg = _bigrams(c)
                score = len(qg & cg) / len(qg) if qg else 0.0
                if score > 0:
                    hits.append((-score, d.seq, i, d, c))
            if self.st.defects.mutate_on_search:
                d.revision += 1
        hits.sort(key=lambda h: h[:3])
        n = int(b.get("match_count") or 10)
        self._send(200, [{"id": f"{d.id}:{i}", "knowledge_id": d.id, "knowledge_title": d.title, "content": c,
                          "score": round(-s, 6), "chunk_index": i, "metadata": dict(d.custom_metadata)}
                         for s, _, i, d, c in hits[:n]])
