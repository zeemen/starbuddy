"""WeKnora 技术验证探针（specs/kb-weknora-integration/weknora-validation-plan.md 第四节）。

每个探针对应计划里一个能自动化的验证项，返回 ProbeResult：
- pass / fail：按计划写的通过标准判定；
- conditional：基本项通过，但缺少配置（例如没给只读密钥）有一部分没测；
- manual：探针只能准备材料，结论要人核对（V17 的查询留存要由数据开发团队扫描数据库与日志）；
- error：调用 WeKnora 出了意外（按失败处理）。

只写合成数据：标题都带本次运行的前缀（sbprobe-<run_id>-），结束时删除（--keep 时保留，便于人工查看）。
证据只含计数、布尔值、状态码、字段名与耗时，不含正文、查询原文、密钥与地址。
"""
from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

from .client import SearchResult, WeKnoraClient, WeKnoraError, wait_until

STATUSES = ("pass", "fail", "conditional", "manual", "error", "skipped")

# V07 的合成“同名不同版”干扰集：数学 · 初二上 · 同一个知识点，在不同省份、出版社、版本下各一篇
COMBOS: tuple[tuple[str, str, str, str], ...] = (
    ("JS-SK-2024", "江苏", "苏科版", "2024"),
    ("JS-SK-2013", "江苏", "苏科版", "2013"),
    ("ZJ-ZJ-2013", "浙江", "浙教版", "2013"),
    ("BJ-BS-2024", "北京", "北师大版", "2024"),
    ("QG-RJ-2024", "全国", "人教版", "2024"),
)
V07_QUERY = "一次函数的图像与性质"
V07_BODY = ("合成资料（不是真实教材，只用于技术验证）。\n\n"
            "一次函数的图像与性质：一次函数 y=kx+b 的图像是一条直线；k>0 时 y 随 x 增大而增大，k<0 时 y 随 x 增大而减小。\n\n"
            "版本标记：{code}（{province} · {publisher} · {edition} 版 · 数学 · 初二 · 上学期）")

V16_FORMULA = "$$\nx=\\frac{-b\\pm\\sqrt{b^{2}-4ac}}{2a}\n$$"
V16_TABLE = ("| 判别式 | 根的情况 |\n"
             "|---|---|\n"
             "| Δ>0 | 两个不相等的实数根 |\n"
             "| Δ=0 | 两个相等的实数根 |\n"
             "| Δ<0 | 没有实数根 |")
V16_BODY = ("合成资料（不是真实教材）。一元二次方程求根公式与判别式。\n\n"
            "求根公式：\n" + V16_FORMULA + "\n\n判别式与根的情况对照表：\n" + V16_TABLE)
_VOLATILE = re.compile(r"(time|_at$|count|stat|visit|hit)", re.I)


@dataclass
class ProbeResult:
    id: str
    title: str
    status: str
    summary: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        assert self.status in STATUSES, self.status


@dataclass
class ProbeContext:
    client: WeKnoraClient                    # 测试库的写入 + 检索密钥（数据开发团队发放，只限测试库）
    kb_id: str
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    retrieve_client: WeKnoraClient | None = None   # 只有“检索”能力的密钥（V03）
    other_kb_id: str | None = None                 # 只读密钥不应能访问的另一个库（V03）
    partition_kb_ids: tuple[str, ...] = ()         # 按维度分库做法用的测试库（V07a，至少 2 个）
    index_timeout: float = 120.0
    poll_interval: float = 2.0
    allowlist_sizes: tuple[int, ...] = (10, 100, 1000)
    keep: bool = False
    sleep: Callable[[float], None] | None = None
    created: list[str] = field(default_factory=list)
    created_tags: list[tuple[str, str]] = field(default_factory=list)

    @property
    def prefix(self) -> str:
        return f"sbprobe-{self.run_id}-"

    # ---------- 写入（都记账，结束时清理） ----------
    def add_doc(self, name: str, content: str, *, kb_id: str | None = None, tag_ids: Sequence[str] = ()) -> str:
        kid = self.client.create_manual(kb_id or self.kb_id, self.prefix + name, content, tag_ids=tag_ids)
        self.created.append(kid)
        return kid

    def add_tag(self, name: str, kb_id: str | None = None) -> str:
        kb = kb_id or self.kb_id
        tid = self.client.create_tag(kb, self.prefix + name)
        self.created_tags.append((kb, tid))
        return tid

    def cleanup(self) -> dict[str, int]:
        if self.keep:
            return {"kept_docs": len(self.created), "kept_tags": len(self.created_tags)}
        failed = 0
        for kid in reversed(self.created):
            try:
                self.client.delete_knowledge(kid)
            except WeKnoraError as e:
                if e.status != 404:
                    failed += 1
        for kb, tid in reversed(self.created_tags):
            try:
                self.client.delete_tag(kb, tid)
            except WeKnoraError as e:
                if e.status != 404:
                    failed += 1
        n = {"deleted_docs": len(self.created), "deleted_tags": len(self.created_tags), "cleanup_failures": failed}
        self.created, self.created_tags = [], []
        return n

    def wait(self, predicate: Callable[[], bool]) -> bool:
        kw = {"sleep": self.sleep} if self.sleep else {}
        return wait_until(predicate, timeout=self.index_timeout, interval=self.poll_interval, **kw)

    def search(self, query: str, **kw: Any) -> SearchResult:
        kw.setdefault("kb_ids", [self.kb_id])
        return self.client.knowledge_search(query, **kw)


def _denied(fn: Callable[[], Any]) -> int | None:
    """调用应被拒绝：返回状态码（成功返回 None 表示“没被拒绝”）"""
    try:
        fn()
    except WeKnoraError as e:
        return e.status if e.status is not None else -1
    return None


# ---------- V03 鉴权与最小权限 ----------
def probe_v03(ctx: ProbeContext) -> ProbeResult:
    ev: dict[str, Any] = {}
    no_key = _denied(lambda: ctx.client.with_key(None).knowledge_search(V07_QUERY, kb_ids=[ctx.kb_id]))
    bad_key = _denied(lambda: ctx.client.with_key("sbprobe-invalid-" + ctx.run_id).knowledge_search(V07_QUERY, kb_ids=[ctx.kb_id]))
    ev["no_key_status"], ev["wrong_key_status"] = no_key, bad_key
    problems = [n for n, s in (("无密钥", no_key), ("错误密钥", bad_key)) if s not in (401, 403)]
    if ctx.retrieve_client is None:
        ev["retrieve_key"] = "未配置"
        if problems:
            return ProbeResult("V03", "鉴权与最小权限", "fail", f"{'、'.join(problems)}的请求没有被拒绝", ev)
        return ProbeResult("V03", "鉴权与最小权限", "conditional",
                           "无密钥、错误密钥都被拒绝；没有配置只读密钥（WEKNORA_RETRIEVE_API_KEY），最小权限未测", ev)
    rc = ctx.retrieve_client
    try:
        rc.knowledge_search(V07_QUERY, kb_ids=[ctx.kb_id])
        ev["retrieve_key_search"] = "ok"
    except WeKnoraError as e:
        ev["retrieve_key_search"] = e.status
        problems.append("只读密钥不能检索测试库")
    write = _denied(lambda: rc.create_manual(ctx.kb_id, ctx.prefix + "V03-should-not-exist", "合成：不应写入"))
    ev["retrieve_key_write_status"] = write
    if write not in (401, 403):
        problems.append("只读密钥能写入")
    if ctx.other_kb_id:
        other = _denied(lambda: rc.knowledge_search(V07_QUERY, kb_ids=[ctx.other_kb_id]))
        ev["retrieve_key_other_kb_status"] = other
        if other not in (401, 403):
            problems.append("只读密钥能检索白名单以外的库")
    else:
        ev["retrieve_key_other_kb_status"] = "未配置 WEKNORA_OTHER_KB_ID"
    if problems:
        return ProbeResult("V03", "鉴权与最小权限", "fail", "；".join(problems), ev)
    if not ctx.other_kb_id:
        return ProbeResult("V03", "鉴权与最小权限", "conditional", "只读密钥能检索、不能写入；库白名单未测（没有配置另一个库）", ev)
    return ProbeResult("V03", "鉴权与最小权限", "pass", "无密钥、错误密钥被拒；只读密钥只能检索指定的库", ev)


# ---------- V07 多维“且”过滤 ----------
def _seed_combos(ctx: ProbeContext) -> tuple[dict[str, list[str]], dict[str, str], dict[str, str]]:
    """每个组合一篇；同时打组合标签与单维度标签（省份、版本）。返回 组合→文档 ID、组合→组合标签、维度值→标签"""
    combo_tag = {code: ctx.add_tag(f"combo-{code}") for code, *_ in COMBOS}
    dim_tag: dict[str, str] = {}
    for _, province, _, edition in COMBOS:
        for v in (f"province-{province}", f"edition-{edition}"):
            if v not in dim_tag:
                dim_tag[v] = ctx.add_tag(v)
    docs: dict[str, list[str]] = {}
    for code, province, publisher, edition in COMBOS:
        body = V07_BODY.format(code=code, province=province, publisher=publisher, edition=edition)
        tags = [combo_tag[code], dim_tag[f"province-{province}"], dim_tag[f"edition-{edition}"]]
        docs[code] = [ctx.add_doc(f"V07-{code}", body, tag_ids=tags)]
    return docs, combo_tag, dim_tag


def _isolation(results: Mapping[str, SearchResult], docs: Mapping[str, Sequence[str]]) -> dict[str, Any]:
    leaks, empty = 0, 0
    for code, r in results.items():
        allowed = set(docs[code])
        got = {h.knowledge_id for h in r.hits}
        if not got:
            empty += 1
        leaks += len(got - allowed)
    lat = [r.elapsed_ms for r in results.values()]
    return {"cross_version_hits": leaks, "empty_results": empty, "queries": len(results),
            "latency_ms_max": round(max(lat), 1) if lat else None}


def probe_v07(ctx: ProbeContext) -> ProbeResult:
    docs, combo_tag, dim_tag = _seed_combos(ctx)
    all_ids = [i for ids in docs.values() for i in ids]
    indexed = ctx.wait(lambda: len({h.knowledge_id for h in ctx.search(V07_QUERY, match_count=50).hits} & set(all_ids)) == len(all_ids))
    ev: dict[str, Any] = {"combos": len(COMBOS), "indexed_within_timeout": indexed}
    if not indexed:
        return ProbeResult("V07", "多维“且”过滤", "error", "合成文档在超时内没有全部检索到，无法判断过滤效果", ev)
    strategies: dict[str, dict[str, Any]] = {}
    # (b) 知识服务先算出允许的文档清单，用 knowledge_ids 检索
    strategies["allowlist"] = _isolation({c: ctx.search(V07_QUERY, knowledge_ids=docs[c], match_count=20) for c in docs}, docs)
    # (c) 组合标签
    strategies["combo_tag"] = _isolation({c: ctx.search(V07_QUERY, tag_ids=[combo_tag[c]], match_count=20) for c in docs}, docs)
    # (a) 按维度分库：只在配置了 ≥2 个分库时测
    if len(ctx.partition_kb_ids) >= 2:
        part_docs: dict[str, list[str]] = {}
        part_kb: dict[str, str] = {}
        for i, (code, province, publisher, edition) in enumerate(COMBOS[:len(ctx.partition_kb_ids)]):
            kb = ctx.partition_kb_ids[i]
            part_kb[code] = kb
            part_docs[code] = [ctx.add_doc(f"V07a-{code}", V07_BODY.format(code=code, province=province, publisher=publisher,
                                                                            edition=edition), kb_id=kb)]
        ctx.wait(lambda: all(part_docs[c][0] in {h.knowledge_id for h in ctx.search(V07_QUERY, kb_ids=[part_kb[c]]).hits}
                             for c in part_docs))
        strategies["kb_partition"] = _isolation(
            {c: ctx.search(V07_QUERY, kb_ids=[part_kb[c]], match_count=20) for c in part_docs}, part_docs)
    else:
        strategies["kb_partition"] = {"skipped": "没有配置 WEKNORA_PARTITION_KB_IDS（至少 2 个测试库）"}
    # 单维度标签组合起来是“且”还是“或”：江苏 + 2013 版，“且”只命中 JS-SK-2013，“或”还会命中 JS-SK-2024、ZJ-ZJ-2013
    r = ctx.search(V07_QUERY, tag_ids=[dim_tag["province-江苏"], dim_tag["edition-2013"]], match_count=20)
    got = {h.knowledge_id for h in r.hits}
    and_set, or_set = set(docs["JS-SK-2013"]), set(docs["JS-SK-2024"] + docs["JS-SK-2013"] + docs["ZJ-ZJ-2013"])
    ev["multi_tag_semantics"] = "and" if got == and_set else "or" if got == or_set else "other"
    # 允许清单很长时：真实 ID + 不存在的 ID 补足长度
    sizes: list[dict[str, Any]] = []
    for n in ctx.allowlist_sizes:
        ids = docs["JS-SK-2024"] + [uuid.uuid4().hex for _ in range(max(0, n - 1))]
        try:
            rr = ctx.search(V07_QUERY, knowledge_ids=ids, match_count=20)
            got_n = {h.knowledge_id for h in rr.hits}
            sizes.append({"size": n, "accepted": True, "latency_ms": round(rr.elapsed_ms, 1),
                          "isolated": got_n <= set(docs["JS-SK-2024"]) and bool(got_n)})
        except WeKnoraError as e:
            sizes.append({"size": n, "accepted": False, "status": e.status})
    ev["allowlist_sizes"] = sizes
    ev["strategies"] = strategies
    ok = [k for k, v in strategies.items() if "skipped" not in v and v["cross_version_hits"] == 0 and v["empty_results"] == 0]
    tested = [k for k, v in strategies.items() if "skipped" not in v]
    if ok:
        return ProbeResult("V07", "多维“且”过滤", "pass",
                           f"{'、'.join(ok)} 做法在干扰集上 0 次跨版本混用；多个单维度标签之间是“{ev['multi_tag_semantics']}”", ev)
    return ProbeResult("V07", "多维“且”过滤", "fail",
                       f"测过的做法（{'、'.join(tested)}）都出现跨版本混用或空结果；关键项不通过，按计划先停下交用户决定", ev)


# ---------- V08 元数据承载量 ----------
def probe_v08(ctx: ProbeContext) -> ProbeResult:
    marker = f"sbmeta{ctx.run_id}"
    kid = ctx.add_doc("V08", f"合成资料：元数据承载量探针。标记 {marker}。")
    ev: dict[str, Any] = {}
    md20 = {f"f{i:02d}": f"v{i}" for i in range(20)}
    try:
        ctx.client.update_knowledge(kid, custom_metadata=md20)
        ev["fields_20_accepted"] = True
    except WeKnoraError as e:
        ev["fields_20_accepted"], ev["fields_20_status"] = False, e.status
    md21 = {f"f{i:02d}": f"v{i}" for i in range(21)}
    s21 = _denied(lambda: ctx.client.update_knowledge(kid, custom_metadata=md21))
    ev["fields_21_rejected"], ev["fields_21_status"] = s21 is not None, s21
    try:
        stored = ctx.client.get_knowledge(kid).get("custom_metadata")
        ev["stored_fields"] = len(stored) if isinstance(stored, dict) else None
    except WeKnoraError as e:
        ev["stored_fields"] = f"读取失败 {e.status}"
    found = ctx.wait(lambda: any(h.knowledge_id == kid for h in ctx.search(marker).hits))
    hits = [h for h in ctx.search(marker).hits if h.knowledge_id == kid] if found else []
    ev["metadata_in_search_results"] = bool(hits and hits[0].metadata)
    if not ev.get("fields_20_accepted"):
        return ProbeResult("V08", "元数据承载量", "fail", "20 个字段的自定义元数据写不进去", ev)
    return ProbeResult("V08", "元数据承载量", "pass",
                       f"20 个字段可写入；第 21 个字段{'被拒绝' if ev['fields_21_rejected'] else '也被接受'}；"
                       f"检索结果{'带' if ev['metadata_in_search_results'] else '不带'}元数据。据此修订需求单附录 A.1", ev)


# ---------- V09 稳定 ID 与更新 ----------
def probe_v09(ctx: ProbeContext) -> ProbeResult:
    m1, m2 = f"sbverone{ctx.run_id}", f"sbvertwo{ctx.run_id}"
    body1 = f"合成资料：更新语义探针，第一版，标记 {m1}。"
    kid = ctx.add_doc("V09", body1)
    ev: dict[str, Any] = {}
    try:
        dup = ctx.add_doc("V09", body1)
        ev["duplicate_create"] = "accepted_new_id" if dup != kid else "same_id"
    except WeKnoraError as e:
        ev["duplicate_create"] = f"rejected_{e.status}"

    def finds(marker: str) -> bool:
        """检索是模糊匹配：按“返回的切块正文里有这个标记”判断，而不是只看命中了这篇文档"""
        return any(h.knowledge_id == kid and marker in h.content for h in ctx.search(marker).hits)

    ev["v1_indexed"] = ctx.wait(lambda: finds(m1))
    ctx.client.replace_manual(kid, f"合成资料：更新语义探针，第二版，标记 {m2}。")
    ev["replace_visible"] = ctx.wait(lambda: finds(m2) and not finds(m1))
    ev["old_content_gone"] = not finds(m1)
    ctx.client.delete_knowledge(kid)
    ev["delete_hidden"] = ctx.wait(lambda: not finds(m2))
    gone = _denied(lambda: ctx.client.get_knowledge(kid))
    ev["get_after_delete_status"] = gone
    ok = ev["v1_indexed"] and ev["replace_visible"] and ev["delete_hidden"]
    if not ok:
        bad = [k for k in ("v1_indexed", "replace_visible", "delete_hidden") if not ev[k]]
        return ProbeResult("V09", "稳定 ID 与更新", "fail", f"不满足：{'、'.join(bad)}", ev)
    return ProbeResult("V09", "稳定 ID 与更新", "pass",
                       f"整篇替换后检索立即反映新内容、旧内容不再命中；删除后检索不到。重复写入：{ev['duplicate_create']}", ev)


# ---------- V11 纯检索接口的返回形状 ----------
def probe_v11(ctx: ProbeContext) -> ProbeResult:
    marker = f"sbshape{ctx.run_id}"
    kid = ctx.add_doc("V11", f"合成资料：返回形状探针。标记 {marker}。")
    found = ctx.wait(lambda: any(h.knowledge_id == kid for h in ctx.search(marker).hits))
    r = ctx.search(marker)
    ev: dict[str, Any] = {"indexed": found, "hits": len(r.hits), "latency_ms": round(r.elapsed_ms, 1),
                          "outer_keys": list(r.raw_keys)}
    if not r.hits:
        return ProbeResult("V11", "纯检索接口的返回形状", "fail", "检索没有返回结果", ev)
    h = r.hits[0]
    ev["hit_keys"] = list(h.raw_keys)
    have = {"chunk_id": h.chunk_id is not None, "knowledge_id": h.knowledge_id is not None, "score": h.score is not None,
            "content": bool(h.content)}
    ev["mapped"] = have
    missing = [k for k, v in have.items() if not v]
    empty = _denied(lambda: ctx.search(""))
    ev["empty_query_status"] = empty
    if missing:
        return ProbeResult("V11", "纯检索接口的返回形状", "fail", f"返回里找不到：{'、'.join(missing)}（按 hit_keys 修正 client._hit）", ev)
    return ProbeResult("V11", "纯检索接口的返回形状", "pass", "能拿到切块 ID、文档 ID、分数与正文，足够填统一信封", ev)


# ---------- V16 公式与表格 ----------
def probe_v16(ctx: ProbeContext) -> ProbeResult:
    kid = ctx.add_doc("V16", V16_BODY)
    ctx.wait(lambda: any(h.knowledge_id == kid for h in ctx.search("判别式与根的情况对照表").hits))
    texts = [h.content for q in ("一元二次方程求根公式", "判别式与根的情况对照表", "两个不相等的实数根")
             for h in ctx.search(q, match_count=20).hits if h.knowledge_id == kid]
    formula_ok = any(V16_FORMULA in t for t in texts)
    table_ok = any(V16_TABLE in t for t in texts)
    ev = {"chunks_seen": len(set(texts)), "formula_intact": formula_ok, "table_intact": table_ok}
    if formula_ok and table_ok:
        return ProbeResult("V16", "公式与表格", "pass", "公式块与表格都在同一个切块里完整返回", ev)
    return ProbeResult("V16", "公式与表格", "fail",
                       "公式或表格被切断（退路：中台按题或按小节切好，WeKnora 侧关闭再切块）", ev)


# ---------- V17 检索查询是否落库或留日志（准备材料，人工核对） ----------
def canary_scan_sql(canary: str) -> str:
    """只读：在 WeKnora 数据库的全部用户表的文本类列里找金丝雀字符串，列出命中的表与列"""
    if not re.fullmatch(r"[a-z0-9]+", canary):
        raise ValueError("金丝雀只能是小写字母与数字")
    return f"""-- 只读扫描：WeKnora 数据库里有没有出现金丝雀查询（技术验证计划 V17）。由数据开发团队在测试库的数据库上执行。
-- 命中的每一行是：表、列、命中行数。没有输出表示数据库里没有留下这次的查询文本。
DO $$
DECLARE r record; n bigint;
BEGIN
  FOR r IN
    SELECT table_schema, table_name, column_name FROM information_schema.columns
     WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
       AND data_type IN ('text', 'character varying', 'json', 'jsonb', 'character')
  LOOP
    EXECUTE format('SELECT count(*) FROM %I.%I WHERE %I::text LIKE %L', r.table_schema, r.table_name, r.column_name, '%{canary}%') INTO n;
    IF n > 0 THEN RAISE NOTICE '命中：%.% 列 %（% 行）', r.table_schema, r.table_name, r.column_name, n; END IF;
  END LOOP;
END $$;
"""


def probe_v17(ctx: ProbeContext) -> ProbeResult:
    canary = f"sbcanary{ctx.run_id}"
    statuses = []
    for _ in range(3):
        try:
            ctx.search(f"{canary} 合成查询，不含任何学生信息")
            statuses.append("ok")
        except WeKnoraError as e:
            statuses.append(e.status)
    ev = {"canary": canary, "searches": statuses,
          "scan_sql_sha256": hashlib.sha256(canary_scan_sql(canary).encode()).hexdigest()[:16],
          "next_steps": [
              "数据开发团队在测试库的数据库上执行报告目录里的 v17-canary-scan.sql（只读），把输出贴回验证报告；",
              f"在 app、docreader 的日志（kubectl logs）与 Redis 里搜索 {canary}；",
              "默认日志级别与关闭调试日志两种配置各做一次；结论由合规负责人确认（已定事项第 3 条）。"]}
    return ProbeResult("V17", "检索查询是否落库或留日志", "manual",
                       "已发出 3 次带金丝雀的纯检索请求；是否留痕要由数据开发团队扫描数据库与日志后判断", ev)


# ---------- V18 结果不回写 ----------
def _fingerprint(ctx: ProbeContext, ids: Iterable[str]) -> tuple[str, list[str]]:
    dropped: set[str] = set()
    rows = []
    for kid in sorted(ids):
        d = ctx.client.get_knowledge(kid)
        keep = {}
        for k, v in d.items():
            if _VOLATILE.search(k):
                dropped.add(k)
            else:
                keep[k] = v
        rows.append(repr(sorted(keep.items(), key=lambda kv: kv[0])))
    return hashlib.sha256("\n".join(rows).encode()).hexdigest(), sorted(dropped)


def probe_v18(ctx: ProbeContext) -> ProbeResult:
    marker = f"sbnowrite{ctx.run_id}"
    ids = [ctx.add_doc(f"V18-{i}", f"合成资料：不回写探针 {i}，标记 {marker}。") for i in range(3)]
    ctx.wait(lambda: len({h.knowledge_id for h in ctx.search(marker).hits} & set(ids)) == len(ids))
    before, dropped = _fingerprint(ctx, ids)
    for _ in range(5):
        ctx.search(marker)
    after, _ = _fingerprint(ctx, ids)
    ev = {"docs": len(ids), "searches": 5, "unchanged": before == after, "ignored_volatile_fields": dropped}
    if before == after:
        return ProbeResult("V18", "结果不回写", "pass", "检索前后文档内容与元数据一致", ev)
    return ProbeResult("V18", "结果不回写", "fail", "检索之后文档被改动", ev)


PROBES: dict[str, tuple[str, Callable[[ProbeContext], ProbeResult]]] = {
    "V03": ("鉴权与最小权限", probe_v03),
    "V07": ("多维“且”过滤", probe_v07),
    "V08": ("元数据承载量", probe_v08),
    "V09": ("稳定 ID 与更新", probe_v09),
    "V11": ("纯检索接口的返回形状", probe_v11),
    "V16": ("公式与表格", probe_v16),
    "V17": ("检索查询是否落库或留日志", probe_v17),
    "V18": ("结果不回写", probe_v18),
}
KEY_PROBES = ("V07", "V17")   # 计划里标 ★ 且能由探针覆盖的项；V02、V05、V08 的其余部分是人工项


def run(ctx: ProbeContext, ids: Sequence[str] | None = None) -> tuple[list[ProbeResult], dict[str, int]]:
    """逐个运行；某个探针出错不影响其他探针；最后清理本次写入的合成数据"""
    out: list[ProbeResult] = []
    try:
        for pid in ids or list(PROBES):
            title, fn = PROBES[pid]
            try:
                out.append(fn(ctx))
            except WeKnoraError as e:
                out.append(ProbeResult(pid, title, "error", f"调用 WeKnora 失败：{e}", {"status": e.status}))
    finally:
        cleanup = ctx.cleanup()
    return out, cleanup
