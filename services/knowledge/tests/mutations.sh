#!/usr/bin/env bash
# 知识服务（KGE-03 PR ① + PR ② + PR ③ + 审查处理）的变异测试：逐条把编译器、程序体检、知识服务读工具、兜底检索、M1 报表与
# 对照评测脚本（evals/kge-compile）的关键检查改坏，断言测试能发现。
# 仿照 services/bff/tests/mutations.sh 的源码变异：把 services/knowledge 复制到临时目录（保持仓库结构，docs、evals、
# services/learning 等软链接回仓库），改一处源码，跑指定的测试文件。
# 先跑一次不变异的对照，必须通过；变异后只有“pytest 退出码 1（有断言失败）”才算被发现——
# 收集错误、语法错误（退出码 2 及以上）算变异本身写错；原文找不到也算写错。每条限时 120 秒，超时算漏检。
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SVC="$(cd "$HERE/.." && pwd)"
ROOT="$(cd "$SVC/../.." && pwd)"
PY="$SVC/.venv/bin/python"
if [ ! -x "$PY" ] || ! "$PY" -c "import pytest, sympy, fastapi, asyncpg, mcp, jieba" 2>/dev/null; then
  echo "knowledge-mutation: 跳过（没有安装依赖；见 services/knowledge/README.md）"; exit 3
fi
# 知识服务的变异（M_DB）要一次性 Postgres；没有时只跑其余变异，最后以退出码 3 报“部分跳过”（严格模式下 Makefile 判失败）
HAVE_PG=0
if [ -n "${PGBIN:-}" ] && [ -x "$PGBIN/initdb" ]; then HAVE_PG=1; fi
if ls /usr/lib/postgresql/*/bin/initdb >/dev/null 2>&1; then HAVE_PG=1; fi

# 每条：名字<TAB>文件（相对 services/knowledge；以 evals/ 或 tools/ 开头的相对仓库根）<TAB>原文<TAB>替换为<TAB>测试文件（空格分隔；
# 以 evals/kge-compile/ 开头的在副本的 evals/kge-compile 目录下跑）
M=(
  $'锁定段与上一审核版比对失效\tknowledge/lint/checks.py\tif not any(old_hash.get(k) != new_hash.get(k) for k in keys):\tif False:\ttests/test_lint.py'
  $'锁定段与渲染段不核对模板\tknowledge/lint/checks.py\tif cur is not None and cur.content != sec.content:\tif False:\ttests/test_lint.py'
  $'缺引用不查\tknowledge/lint/checks.py\t            if not CITATION.search(para):\t            if False:\ttests/test_lint.py'
  $'SymPy 恒等不验\tknowledge/lint/mathcheck.py\t        if not _equal(a, b):\t        if False:\ttests/test_lint.py tests/test_mathcheck_lexicon.py'
  $'SymPy 代入取值不生效\tknowledge/lint/mathcheck.py\t        if subs:\n\t        if False:\n\ttests/test_lint.py tests/test_mathcheck_lexicon.py'
  $'标了 check 却无法解析只报警告\tknowledge/lint/checks.py\tout.append(_issue(pf, "sympy", "block", f"标了 % check 的式子无法解析\tout.append(_issue(pf, "sympy", "warn", f"标了 % check 的式子无法解析\ttests/test_lint.py'
  $'手机号模式失效\tknowledge/lint/checks.py\t(?<!\\d)1[3-9]\\d[- ]?\t(?<!\\d)0[3-9]\\d[- ]?\ttests/test_lint.py'
  $'姓名加学校组合不查\tknowledge/lint/checks.py\t[label for label, pat in PII_PATTERNS if\t[label for label, pat in PII_PATTERNS[:-1] if\ttests/test_lint.py'
  $'群体统计人数门槛失效\tknowledge/lint/checks.py\telif min(ns) < ctx.min_group:\telif min(ns) < 1:\ttests/test_lint.py'
  $'人设词表：角色名称呼口头禅不查\tknowledge/lint/checks.py\tliteral = [("角色名", w) for w in lex.role_names] + [("称呼", w) for w in lex.address_terms] + [("口头禅或固定句式", w) for w in lex.catchphrases]\tliteral = []\ttests/test_lint.py'
  $'禁语不查\tknowledge/lint/checks.py\t        for rule in lex.forbidden:\t        for rule in lex.forbidden[:0]:\ttests/test_lint.py'
  $'词表不抽 G 类\tknowledge/lint/lexicon.py\tforbidden += _phrase_rules(_block(examiner, \t_ = (_block(examiner, \ttests/test_lint.py tests/test_mathcheck_lexicon.py'
  $'句长上限不查\tknowledge/lint/checks.py\t                    if n > lex.max_sentence_chars:\t                    if False:\ttests/test_lint.py'
  $'原子哈希过期不查\tknowledge/lint/checks.py\t        elif atom.hash != r.hash:\t        elif False:\ttests/test_lint.py tests/test_e2e.py'
  $'失效链接不查\tknowledge/lint/checks.py\t            if m.group(1) not in ctx.page_ids:\t            if False:\ttests/test_lint.py'
  $'撤下节点的孤立页不查\tknowledge/lint/checks.py\t        if node.get("status", "active") != "active":\t        if False:\ttests/test_lint.py'
  $'带有效期原子过期不查\tknowledge/lint/checks.py\t        if until < ctx.today or\t        if False and until < ctx.today or\ttests/test_lint.py'
  $'教材原句写进页面不查\tknowledge/lint/checks.py\t            if len(q) >= 6 and any(p in flat for p in pieces):\t            if False:\ttests/test_lint.py'
  $'编入不按节点找新增原子\tknowledge/compiler/jobs.py\t            if nid in by_node:\t            if False:\ttests/test_compiler.py'
  $'前置关系表述变更判为低风险\tknowledge/compiler/rebuild.py\telif spec.id == "relations" and\telif False and\ttests/test_compiler.py'
  $'叙述段不逐字保留\tknowledge/compiler/rebuild.py\t            sections.append(narrative[spec.id])\t            sections.append(Section(spec.id, "narrative", narrative[spec.id].content.rstrip() + " "))\ttests/test_compiler.py tests/test_e2e.py'
  $'全量重编不一律高风险\tknowledge/compiler/rebuild.py\t    if kind == "full":\n        risk = "high"\t    if False:\n        risk = "high"\ttests/test_full.py'
  $'全量重编请求放过个人层字段\tknowledge/compiler/full.py\t            if str(k).lower() in PERSONAL_FIELDS:\t            if False:\ttests/test_full.py'
  $'全量重编整条转发节点记录\tknowledge/compiler/full.py\t"node": {"ref": atom.key, "node_id": node_id, "name": str(n["name"]),\t"node": {**dict(n), "ref": atom.key, "node_id": node_id, "name": str(n["name"]),\ttests/test_full.py'
  $'全量重编数据级别不是 L0\tknowledge/compiler/full.py\tdata_class=mr.DataClass.L0\tdata_class=mr.DataClass.L1\ttests/test_full.py'
  $'合并窗口每页每天可发布多次\tknowledge/compiler/window.py\t        slot[r.page_id] = combine(slot[r.page_id], r) if r.page_id in slot else r\t        slot[f"{r.page_id}#{len(slot)}"] = r\ttests/test_compiler.py'
  $'当天已发布的页面不顺延\tknowledge/compiler/window.py\t        while day in done.get(r.page_id, set()):\t        while False:\ttests/test_compiler.py tests/test_e2e.py'
  $'高风险只要 1 人审核\tknowledge/compiler/window.py\tREVIEWERS = {"low": 0, "medium": 2, "high": 2}\tREVIEWERS = {"low": 0, "medium": 2, "high": 1}\ttests/test_compiler.py tests/test_e2e.py'
  $'快照放过未知字段\tknowledge/atoms.py\t    if unknown:\n        raise SnapshotError(f"{where}: 未知字段\t    if False:\n        raise SnapshotError(f"{where}: 未知字段\ttests/test_format_atoms.py'
  $'头部缺字段不查\tknowledge/lint/checks.py\t    if missing:\n        bad(f"头部缺字段\t    if False:\n        bad(f"头部缺字段\ttests/test_lint.py'
  $'知识服务：快照篡改（哈希不符）不拒绝\tknowledge/service/pages.py\t        if hashlib.sha256(raw).hexdigest() != digest:\t        if False:\ttests/test_service_unit.py'
  $'知识服务：监听地址缺省为 0.0.0.0\tknowledge/service/config.py\tDEFAULT_HOST = "127.0.0.1"\tDEFAULT_HOST = "0.0.0.0"\ttests/test_service_unit.py'
  $'知识服务：不拒绝通配监听地址\tknowledge/service/config.py\t    if h.lower() in _WILDCARD:\t    if False:\ttests/test_service_unit.py'
  $'知识服务：node.resolve 放给编排层以外的调用方\tknowledge/service/tools.py\t    "node.resolve": frozenset({"orchestrator"}),\t    "node.resolve": PUBLIC_CALLERS,\ttests/test_service_unit.py'
  # PR ③：兜底检索
  $'BM25 idf 漏掉分母\tknowledge/retrieval/bm25.py\t        return math.log(1 + (self.n - d + 0.5) / (d + 0.5))\t        return math.log(1 + (self.n - d + 0.5))\ttests/test_retrieval.py'
  $'BM25 不做文档长度归一\tknowledge/retrieval/bm25.py\tself.b * dl / self.avgdl\tself.b\ttests/test_retrieval.py'
  $'BM25 查询词不去重\tknowledge/retrieval/bm25.py\t        for t in sorted(set(terms)):\t        for t in terms:\ttests/test_retrieval.py'
  $'BM25 同分不按键升序\tknowledge/retrieval/bm25.py\t        hits.sort(key=lambda h: (-h.score, str(h.key)))\t        hits.sort(key=lambda h: (-h.score, [-ord(ch) for ch in str(h.key)]))\ttests/test_retrieval.py'
  $'分词不去停用词\tknowledge/retrieval/tokenize.py\t        if w and w not in STOPWORDS and _WORDY.search(w):\t        if w and _WORDY.search(w):\ttests/test_retrieval.py'
  $'分词不去页面标记\tknowledge/retrieval/tokenize.py\t    return _MARKUP.sub("", text)\t    return text\ttests/test_retrieval.py'
  $'切块收教材位置段\tknowledge/retrieval/chunks.py\tSKIP_SECTIONS = frozenset({"textbook"})\tSKIP_SECTIONS = frozenset()\ttests/test_retrieval.py'
  $'切块收未发布地图版本\tknowledge/retrieval/chunks.py\t    if atoms.status != "published":\t    if False:\ttests/test_retrieval.py'
  $'切块收已撤下节点的页面\tknowledge/retrieval/chunks.py\t            if pp.node_id in nodes:\t            if True:\ttests/test_retrieval.py'
  $'补页面信号记原始查询\tknowledge/retrieval/pipeline.py\tself.sink.emit(GapSignal(nid, reason, self.digester.digest(text), len(terms),\tself.sink.emit(GapSignal(nid, reason, text, len(terms),\ttests/test_retrieval.py'
  $'查询摘要不带密钥\tknowledge/retrieval/signals.py\t        return hmac.new(self._key, normalize(text).strip().encode("utf-8"), hashlib.sha256).hexdigest()[:16]\t        return hashlib.sha256(normalize(text).strip().encode("utf-8")).hexdigest()[:16]\ttests/test_retrieval.py'
  $'低置信不走兜底\tknowledge/retrieval/pipeline.py\t            if conf < LOW_CONFIDENCE:\n                reason = "low_confidence"\t            if False:\n                reason = "low_confidence"\ttests/test_retrieval.py'
  $'页面覆盖率低不走兜底\tknowledge/retrieval/pipeline.py\t            elif terms and self.corpus.index.coverage(terms, page_tokens) < PAGE_COVERAGE_MIN:\t            elif False:\ttests/test_retrieval.py'
  $'兜底重复页面已给的段\tknowledge/retrieval/pipeline.py\t            hits = self.corpus.search(terms, FALLBACK_TOP_K, exclude=have)\t            hits = self.corpus.search(terms, FALLBACK_TOP_K, exclude=set())\ttests/test_retrieval.py'
  $'全文解析不设覆盖率下限\tknowledge/retrieval/resolver.py\t            if cov < MIN_COVERAGE:\t            if False:\ttests/test_retrieval.py'
  $'合并解析不取最高置信度\tknowledge/retrieval/resolver.py\t                if c.node_id not in best or c.confidence > best[c.node_id].confidence:\t                if c.node_id not in best:\ttests/test_retrieval.py'
  $'node.resolve 没接全文检索\tknowledge/service/tools.py\t        self.resolver = resolver or CombinedResolver()\t        self.resolver = resolver or CombinedResolver([CombinedResolver().resolvers[0]])\ttests/test_retrieval.py'
  # PR ③：M1 报表
  $'M1：承接关系未入库时照算\tknowledge/m1.py\t    if "curriculum_links" not in doc:\t    if False:\ttests/test_m1.py'
  $'M1：已撤下节点的承接也算\tknowledge/m1.py\t        if a.record.get("status", "active") != "active":\n            continue\t        if False:\n            continue\ttests/test_m1.py'
  $'M1：已撤下的 kp 节点进分母\tknowledge/m1.py\tif a.record.get("kind") == "kp" and a.record.get("status", "active") == "active")\tif a.record.get("kind") == "kp")\ttests/test_m1.py'
  $'M1：体检汇总与明细不核对\tknowledge/m1.py\t or blocks != counted:\t:\ttests/test_m1.py'
  $'M1：百分比截断不四舍五入\tknowledge/m1.py\t(1 if v - int(v) >= Fraction(1, 2) else 0)\t0\ttests/test_m1.py'
  # PR ③：对照评测脚本（evals/kge-compile）
  $'对照评测：全部要点判定放宽为任一要点\tevals/kge-compile/compare_retrieval.py\t    return Judgement(n == len(points), n, len(points))\t    return Judgement(n > 0, n, len(points))\tevals/kge-compile/tests/test_compare.py'
  $'对照评测：两边都空的 Jaccard 记 0\tevals/kge-compile/compare_retrieval.py\t    if not a and not b:\n        return Fraction(1)\t    if not a and not b:\n        return Fraction(0)\tevals/kge-compile/tests/test_compare.py'
  $'对照评测：一致性只比相邻两次\tevals/kge-compile/compare_retrieval.py\t    pairs = list(combinations(sets, 2))\t    pairs = list(zip(sets, sets[1:]))\tevals/kge-compile/tests/test_compare.py'
  $'对照评测：报告带出问题原文\tevals/kge-compile/compare_retrieval.py\t        row: dict[str, Any] = {"id": q.id}\t        row: dict[str, Any] = {"id": q.id, "q": q.question}\tevals/kge-compile/tests/test_compare.py'
  $'对照评测：token 估算按字母逐个计\tevals/kge-compile/compare_retrieval.py\t|[0-9A-Za-z]+|\t|[0-9A-Za-z]|\tevals/kge-compile/tests/test_compare.py'
  $'对照评测：主问题只取一次\tevals/kge-compile/compare_retrieval.py\t[r.lookup(q.question) for _ in range(runs)]\t[r.lookup(q.question) for _ in range(1)]\tevals/kge-compile/tests/test_compare.py'
  # 审查处理（specs/kge-03-compile-layer/lessons.md“审查处理”）
  $'审查：正文个人信息只扫剥掉引用后的文字\tknowledge/lint/checks.py\tif pat.search(text) or pat.search(stripped)]\tif pat.search(stripped)]\ttests/test_lint.py'
  $'审查：头部值不扫个人信息\tknowledge/lint/checks.py\t    for where, text in header_strings(page.header):\t    for where, text in []:\ttests/test_lint.py'
  $'审查：原子 ID 不按白名单校验\tknowledge/lint/checks.py\t            elif not valid_atom_id(r["type"], r["id"]):\t            elif False:\ttests/test_lint.py'
  $'审查：节点 ID 按宽松白名单校验\tknowledge/lint/checks.py\t    if atype == "node":\n        return is_node_id(aid)\t    if atype == "node":\n        return bool(ATOM_ID.fullmatch(aid))\ttests/test_lint.py'
  $'审查：体检缺引用消息回显原文\tknowledge/lint/checks.py\t"citation", "block", "自然段缺原子引用",\t"citation", "block", f"自然段缺原子引用：{para.strip()[:30]}",\ttests/test_lint.py'
  $'审查：SymPy 失败消息回显式子\tknowledge/lint/mathcheck.py\tf"第 {k + 1} 个等号两边{what}")\tf"第 {k + 1} 个等号两边{what}：{parts[k].strip()}")\ttests/test_lint.py tests/test_mathcheck_lexicon.py'
  $'审查：CI 体检不强制地图快照\tknowledge/lint/__main__.py\t        if args.require_map_snapshot and not args.map_snapshot:\t        if False:\ttests/test_lint.py'
  $'审查：快照载入不再跑页面体检\tknowledge/service/pages.py\t        if blocks:\t        if False:\ttests/test_service_unit.py'
  $'审查：快照载入放过符号链接\tknowledge/service/pages.py\t    if stat.S_ISLNK(st.st_mode):\n        raise SnapshotLoadError\t    if False:\n        raise SnapshotLoadError\ttests/test_service_unit.py'
  $'审查：快照载入放过属组可写\tknowledge/service/pages.py\t    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):\t    if False:\ttests/test_service_unit.py'
  $'审查：SymPy 小数点规则失效\tknowledge/lint/mathcheck.py\t    if _BAD_DOT.search(text):\t    if False:\ttests/test_mathcheck_lexicon.py'
  $'审查：SymPy 常数指数不限\tknowledge/lint/mathcheck.py\tor abs(v) > MAX_EXPONENT:\t:\ttests/test_mathcheck_lexicon.py'
  $'审查：SymPy 树检查放过函数与元组\tknowledge/lint/mathcheck.py\t        if not isinstance(e, (sympy.Add, sympy.Mul, sympy.Pow)):\n            raise LatexError("验算不支持的写法")\t        if False:\n            raise LatexError("验算不支持的写法")\ttests/test_mathcheck_lexicon.py'
  $'审查：parse_expr 带回 builtins\tknowledge/lint/mathcheck.py\t_GLOBALS = {"__builtins__": {}, \t_GLOBALS = {\ttests/test_mathcheck_lexicon.py'
  $'审查：验算超时不按无法验证\tknowledge/lint/mathcheck.py\t        if not recv.poll(timeout):\n            return UNVERIFIABLE_LIMIT\t        if not recv.poll(timeout):\n            return CheckOutcome(True, True, "通过")\ttests/test_mathcheck_lexicon.py'
  $'审查：全量重编不扫值里的个人信息\tknowledge/compiler/full.py\t        if hits:\t        if False:\ttests/test_full.py'
  $'审查：通配地址解析后不拒绝\tknowledge/service/config.py\t    if not ips or any(ip.is_unspecified for ip in ips):\t    if not ips:\ttests/test_service_unit.py'
  $'审查：IPv6 地址重复加方括号\tknowledge/service/mcp_server.py\t    h = _bare(host)\n    names\t    h = host.strip()\n    names\ttests/test_service_unit.py'
  $'审查：MCP 鉴权只管 http\tknowledge/service/app.py\t            if scope["type"] != "http" or caller_for(\t            if scope["type"] == "http" and caller_for(\ttests/test_service_unit.py'
  $'审查：Host 校验放过任意端口写法\tknowledge/service/mcp_server.py\t            if port.isdigit() and len(port) <= 5:\t            if value.startswith(base + ":"):\ttests/test_service_unit.py'
  $'审查：jieba 缓存回到系统临时目录\tknowledge/retrieval/tokenize.py\t            t.tmp_dir = str(cache_dir())\t            t.tmp_dir = None\ttests/test_retrieval.py'
  $'审查：jieba 缓存目录不查权限\tknowledge/retrieval/tokenize.py\t    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):\t    if False:\ttests/test_retrieval.py'
  $'审查：M1 发布率分子含落后页\tknowledge/m1.py\t    r = Ratio(len(published) - len(stale), len(kp))\t    r = Ratio(len(published), len(kp))\ttests/test_m1.py'
  $'审查：模板 CI 从头部取 lint.sh\ttools/kge-pages-template/repo/ci/github-actions.yml.example\t        run: bash main/ci/lint.sh\t        run: bash head/ci/lint.sh\ttests/test_template.py'
  $'审查：模板 CI 引用 secrets\ttools/kge-pages-template/repo/ci/github-actions.yml.example\t          persist-credentials: false\n          sparse-checkout\t          persist-credentials: false\n          token: ${{ secrets.PAGES_TOKEN }}\n          sparse-checkout\ttests/test_template.py'
  $'审查：lint.sh 用头部的地图快照\ttools/kge-pages-template/repo/ci/lint.sh\tsnapshot="$root/snapshots/current.yaml"\tsnapshot="${HEAD_PAGES_DIR:-$root/pages}/../snapshots/current.yaml"\ttests/test_template.py'
  $'审查：安装检查工具不要求哈希\ttools/kge-pages-template/repo/ci/install-tools.sh\t--require-hashes -r\t-r\ttests/test_template.py'
)

# 知识服务读工具（PR ②）：需要一次性 Postgres
M_DB=(
  $'知识服务：放过草稿与退役版本\tknowledge/service/reader.py\t WHERE subject::text = $1 AND status = \'published\'"""\t WHERE subject::text = $1"""\ttests/test_service_db.py'
  $'知识服务：放过未核对与有争议的投影\tknowledge/service/reader.py\t_VERIFIED = "mapping_state = \'verified\' AND quote_text IS NOT NULL"\t_VERIFIED = "quote_text IS NOT NULL"\ttests/test_service_db.py'
  $'知识服务：放过待审与驳回的标签\tknowledge/service/reader.py\t_APPROVED = "t.tag_review_status = \'approved\'"\t_APPROVED = "true"\ttests/test_service_db.py'
  $'知识服务：node.path 走弱前置边\tknowledge/service/reader.py\tAND type = \'prerequisite\' AND strength = \'strong\' AND\tAND type = \'prerequisite\' AND\ttests/test_service_db.py'
  $'知识服务：不核对请求体 caller 与令牌\tknowledge/service/tools.py\t        if ctx.get("caller") != caller:\t        if False:\ttests/test_service_contract.py tests/test_service_mcp.py'
  $'知识服务：跳过请求校验\tknowledge/service/tools.py\t        errors = self.contract.validate(spec.request_schema, body)\t        errors = []\ttests/test_service_contract.py'
  $'知识服务：跳过响应校验\tknowledge/service/tools.py\t        bad = self.contract.validate(spec.response_schema, envelope)\t        bad = []\ttests/test_service_db.py'
  $'知识服务：请求的 map_version 指向非发布版本也放过\tknowledge/service/tools.py\t        if v is None or ("map_version" in ctx and ctx["map_version"] != v.version):\t        if v is None:\ttests/test_service_db.py tests/test_service_e2e.py'
  $'知识服务：个人层工具不回 501\tknowledge/service/tools.py\t        if tool in PERSONAL_TOOLS:\t        if False:\ttests/test_service_contract.py'
  $'知识服务：以超级用户连接不报错\tknowledge/service/reader.py\t    if row["rolsuper"]:\t    if False:\ttests/test_service_db.py'
  $'知识服务：BYPASSRLS 角色不报错\tknowledge/service/reader.py\t    if row["rolbypassrls"]:\t    if False:\ttests/test_service_db.py'
  $'知识服务：建池不做角色断言\tknowledge/service/reader.py\t            await assert_reader_role(conn)\t            pass\ttests/test_service_db.py'
  $'知识服务：注入的连接池不做角色断言\tknowledge/service/app.py\t                    await assert_reader_role(c)\t                    pass\ttests/test_service_db.py'
  $'知识服务：page.get 未发布页回退到其他页面\tknowledge/service/tools.py\t        pp = self.pages.get(body["page_id"])\t        pp = self.pages.get(body["page_id"]) or next(iter(self.pages.pages.values()))\ttests/test_service_e2e.py'
  $'知识服务：page.get 不查节点是否在已发布版本\tknowledge/service/tools.py\t        if n is None:  # 节点已撤下\t        if False:  # 节点已撤下\ttests/test_service_e2e.py'
  $'知识服务：page.get 教材投影不按版次取\tknowledge/service/tools.py\tawait self.reader.textbook_projections(v.id, pp.node_id, edition)]\tawait self.reader.textbook_projections(v.id, pp.node_id, None)]\ttests/test_service_e2e.py'
  $'知识服务：快照导出放过体检阻断\tknowledge/compiler/snapshot.py\t        if report.blocks:\t        if False:\ttests/test_service_e2e.py'
  $'知识服务：MCP 端点不鉴权\tknowledge/service/app.py\t            if scope["type"] != "http" or caller_for(_headers(scope).get("authorization"), self.tokens) is None:\t            if False:\ttests/test_service_mcp.py'
  $'知识服务：MCP 关闭 DNS 重绑定防护\tknowledge/service/mcp_server.py\treturn TransportSecuritySettings(enable_dns_rebinding_protection=True,\treturn TransportSecuritySettings(enable_dns_rebinding_protection=False,\ttests/test_service_unit.py'
  # 审查处理
  $'审查：page.get 服务地图版本落后的页\tknowledge/service/tools.py\t        if pp.map_version != v.version:\t        if False:\ttests/test_service_e2e.py'
  $'审查：快照导出不核对受保护分支\tknowledge/compiler/snapshot.py\t    assert_on_branch(repo, sha, branch)\t    pass\ttests/test_service_e2e.py'
  $'审查：快照导出不要求地图快照\tknowledge/compiler/snapshot.py\t    if MAP_SNAPSHOT_PATH not in _git(\t    if False and MAP_SNAPSHOT_PATH not in _git(\ttests/test_service_e2e.py'
  $'审查：调 git 不带安全参数\tknowledge/compiler/snapshot.py\tGIT_SAFE = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false")\tGIT_SAFE = ()\ttests/test_service_e2e.py'
  $'审查：登录角色是 pg_ 预置角色成员也放过\tknowledge/service/reader.py\t    if row["predefined"]:\t    if False:\ttests/test_service_db.py'
  $'审查：登录角色有 CREATEDB 也放过\tknowledge/service/reader.py\t    if row["rolcreaterole"] or row["rolcreatedb"]:\t    if row["rolcreaterole"]:\ttests/test_service_db.py'
  $'审查：连接时区不固定北京时间\tknowledge/service/reader.py\t, "TimeZone": "Asia/Shanghai"}\t}\ttests/test_service_db.py'
  $'审查：rubric.get 不联查标签审核\tknowledge/service/reader.py\tAND t.item_id = s.item_id AND {_APPROVED}\tAND t.item_id = s.item_id\ttests/test_service_db.py'
  $'审查：部分给分规则的多余键被丢掉\tknowledge/service/tools.py\tset(rule) == {"mode"} and \t\ttests/test_service_db.py'
  $'审查：关键词对象写法被展开\tknowledge/service/tools.py\t    if not isinstance(value, list) or not all(isinstance(w, str) for w in value):\n        raise LossyMapping("keyword_family")\t    if isinstance(value, dict):\n        value = [w for k in sorted(value) for w in value[k]]\ttests/test_service_db.py'
  $'审查：MCP 列出个人层工具\tknowledge/service/mcp_server.py\t    public = {name: t for name, t in contract.tools.items() if name in PUBLIC_TOOLS}\t    public = dict(contract.tools)\ttests/test_service_mcp.py'
  $'审查：整个应用不校验 Host\tknowledge/service/app.py\t    app.add_middleware(HostGuard, allowed=allowed_hosts(settings.host))\n\t\ttests/test_service_mcp.py'
  $'审查：node.resolve 不带分词器版本\tknowledge/service/tools.py\t"source_version": f"{graph_source(subject, v)};{tokenizer_source()}"\t"source_version": graph_source(subject, v)\ttests/test_service_db.py'
)
if [ "$HAVE_PG" = "1" ]; then M+=("${M_DB[@]}"); else echo "knowledge-mutation: 本机没有 Postgres，知识服务的 ${#M_DB[@]} 条变异跳过"; fi

WORK="$(mktemp -d "${TMPDIR:-/tmp}/kge-mut-XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

copy_tree() {  # $1 目标根目录：services/knowledge 复制一份，其余软链接回仓库
  local dst="$1"
  mkdir -p "$dst/services" "$dst/tools"
  (cd "$SVC" && tar --exclude=.venv --exclude=__pycache__ --exclude=.cache -cf - .) | (mkdir -p "$dst/services/knowledge" && cd "$dst/services/knowledge" && tar -xf -)
  ln -s "$ROOT/services/learning" "$dst/services/learning"
  # 页面仓库模板复制一份（审查处理的 CI 样例变异要改它）
  (cd "$ROOT/tools/kge-pages-template" && tar -cf - .) | (mkdir -p "$dst/tools/kge-pages-template" && cd "$dst/tools/kge-pages-template" && tar -xf -)
  for d in docs db packages; do ln -s "$ROOT/$d" "$dst/$d"; done
  # evals：对照评测（kge-compile）复制一份以便变异，其余软链接回仓库
  mkdir -p "$dst/evals"
  for e in "$ROOT"/evals/*; do
    [ "$(basename "$e")" = "kge-compile" ] || ln -s "$e" "$dst/evals/$(basename "$e")"
  done
  (cd "$ROOT/evals/kge-compile" && tar --exclude=__pycache__ -cf - .) | (mkdir -p "$dst/evals/kge-compile" && cd "$dst/evals/kge-compile" && tar -xf -)
}

run_tests() {  # $1 副本根目录，$2 测试文件；输出 pytest 退出码（以 evals/kge-compile/ 开头的在对照评测目录下跑）
  local dir="$1/services/knowledge" t="$2"
  case "$t" in evals/kge-compile/*) dir="$1/evals/kge-compile"; t="${t#evals/kge-compile/}" ;; esac
  ( cd "$dir" && PYTHONDONTWRITEBYTECODE=1 timeout 120 "$PY" -m pytest -q -x -p no:cacheprovider $t >/dev/null 2>&1 ); echo $?
}

control="$WORK/control"; copy_tree "$control"
rc=$(run_tests "$control" "tests")
if [ "$rc" != "0" ]; then echo "knowledge-mutation: 对照（未变异）没有通过（退出码 $rc），变异结果不可信"; exit 1; fi
rc=$(run_tests "$control" "evals/kge-compile/tests")
if [ "$rc" != "0" ]; then echo "knowledge-mutation: 对照评测的对照（未变异）没有通过（退出码 $rc），变异结果不可信"; exit 1; fi

missed=0; total=0; i=0
for entry in "${M[@]}"; do
  IFS=$'\t' read -r -d '' name file orig repl tests < <(printf '%s\0' "$entry")
  # M 里用 $'...' 写的 \n 是真换行；以 NUL 为记录分隔读入，字段里的换行原样保留
  total=$((total + 1)); i=$((i + 1))
  dst="$WORK/m$i"; copy_tree "$dst"
  case "$file" in evals/*|tools/*) mut_file="$dst/$file" ;; *) mut_file="$dst/services/knowledge/$file" ;; esac
  if ! MUT_FILE="$mut_file" MUT_ORIG="$orig" MUT_REPL="$repl" "$PY" - <<'PY'
import os, sys
p = os.environ["MUT_FILE"]; o = os.environ["MUT_ORIG"]; r = os.environ["MUT_REPL"]
s = open(p, encoding="utf-8").read()
if s.count(o) != 1:
    sys.exit(1)
open(p, "w", encoding="utf-8").write(s.replace(o, r))
PY
  then echo "  变异本身写错（原文找不到或不唯一）：$name"; missed=$((missed + 1)); rm -rf "$dst"; continue; fi
  rc=$(run_tests "$dst" "$tests")
  case "$rc" in
    1) : ;;
    0) echo "  漏检：$name"; missed=$((missed + 1)) ;;
    124) echo "  超时（算漏检）：$name"; missed=$((missed + 1)) ;;
    *) echo "  变异本身写错（pytest 退出码 $rc）：$name"; missed=$((missed + 1)) ;;
  esac
  rm -rf "$dst"
done

if [ $missed -ne 0 ]; then echo "knowledge-mutation: $missed / $total 条没有被测试发现"; exit 1; fi
echo "knowledge-mutation: $total 条变异全部被测试发现"
if [ "$HAVE_PG" != "1" ]; then exit 3; fi
