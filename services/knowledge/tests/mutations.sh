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
  $'词表不收 G 类（冲刺 3-F 后复用 guard 导出）\tknowledge/lint/lexicon.py\t"F": ("aya", "wenli"), "G": ()}\t"F": ("aya", "wenli")}\ttests/test_lint.py tests/test_mathcheck_lexicon.py'
  $'词表导出不核对迁移前快照（冲刺 3-F）\tknowledge/lint/lexicon.py\t    forbidden = [Rule(x["id"], x["cls"], x["regex"], x["source"], tuple(x["subjects"])) for x in exp["forbidden"] if x["cls"] in KGE_CLASSES]\t    forbidden = [Rule(x["id"], x["cls"], x["regex"], x["source"], ()) for x in exp["forbidden"] if x["cls"] in KGE_CLASSES]\ttests/test_mathcheck_lexicon.py tests/test_lint.py'
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
  $'合并窗口不跳过对账失败的页面\tknowledge/compiler/window.py\tif not r.unchanged and r.page_id not in hold)\tif not r.unchanged)\ttests/test_compiler.py tests/test_e2e.py'
  $'对账报告自相矛盾不拒\tknowledge/compiler/window.py\t    if ok != (not held):\t    if False:\ttests/test_compiler.py tests/test_e2e.py'
  $'对账报告不查过期\tknowledge/compiler/window.py\t    if exported < now - max_age:\t    if False:\ttests/test_compiler.py tests/test_e2e.py'
  $'编译器对账报告不必填\tknowledge/compiler/__main__.py\t    hold = ap.add_mutually_exclusive_group(required=True)\t    hold = ap.add_mutually_exclusive_group(required=False)\ttests/test_e2e.py'
  $'快照放过未知字段\tknowledge/atoms.py\t    if unknown:\n        raise SnapshotError(f"{where}: 未知字段\t    if False:\n        raise SnapshotError(f"{where}: 未知字段\ttests/test_format_atoms.py'
  $'头部缺字段不查\tknowledge/lint/checks.py\t    if missing:\n        bad(f"头部缺字段\t    if False:\n        bad(f"头部缺字段\ttests/test_lint.py'
  $'知识服务：快照篡改（哈希不符）不拒绝\tknowledge/service/pages.py\t        if hashlib.sha256(raw).hexdigest() != digest:\t        if False:\ttests/test_service_unit.py'
  $'知识服务：监听地址缺省为 0.0.0.0\tknowledge/service/config.py\tDEFAULT_HOST = "127.0.0.1"\tDEFAULT_HOST = "0.0.0.0"\ttests/test_service_unit.py'
  $'知识服务：不拒绝通配监听地址\tknowledge/service/config.py\t    if h.lower() in _WILDCARD:\t    if False:\ttests/test_service_unit.py'
  $'LT-01：staging 监听地址不限内网\tknowledge/service/config.py\t        if env == STAGING and host != DEFAULT_HOST and not private_ipv4(host):\t        if False:\ttests/test_staging_env.py'
  $'LT-01：内网判断放过公网\tknowledge/service/config.py\t    return str(ip) == host and any(ip in n for n in _PRIVATE_V4)\t    return True\ttests/test_staging_env.py'
  $'LT-01：staging 收契约目录覆盖\tknowledge/service/config.py\t        if env == STAGING and e.get("KNOWLEDGE_CONTRACT_DIR"):\t        if False:\ttests/test_staging_env.py'
  $'LT-01：镜像环境标记不查\tknowledge/service/config.py\t        if marked is not None and marked != env:\t        if False:\ttests/test_staging_env.py'
  $'LT-01：pilot 也收\tknowledge/service/config.py\tSERVICE_ENVS = ALLOWED_ENVS + (STAGING,)\tSERVICE_ENVS = ALLOWED_ENVS + (STAGING, "pilot")\ttests/test_staging_env.py'
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
  $'审查：快照载入放过属组可写\tknowledge/service/pages.py\t    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):\n        raise SnapshotLoadError("页面快照的文件或目录对属组或其他用户可写")\n\n\ndef read_safe\t    if False:\n        raise SnapshotLoadError("页面快照的文件或目录对属组或其他用户可写")\n\n\ndef read_safe\ttests/test_service_unit.py'
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
  # KGE-06（specs/kge06-weekly-revision 范围 3、4）：快照热切换、manifest 签名接口、M1 / M2 快照摘要（T30、T33）
  $'KGE-06：热切换失败不计数\tknowledge/service/hotswap.py\t                self.stats.failures += 1\n                self.stats.last_error = str(e)\t                self.stats.last_error = str(e)\ttests/test_hotswap.py'
  $'KGE-06：热切换载入不用验过的那份 manifest 字节\tknowledge/service/hotswap.py\t        return load_snapshot_dir(d, lexicon=self.lexicon, manifest_raw=manifest_raw)\t        return load_snapshot_dir(d, lexicon=self.lexicon)\ttests/test_hotswap.py'
  $'KGE-06：载入函数不用调用方给的 manifest 字节\tknowledge/service/pages.py\t    raw_manifest = manifest_raw if manifest_raw is not None else read_safe(\t    raw_manifest = read_safe(\ttests/test_hotswap.py'
  $'KGE-06：读文件不用 O_NOFOLLOW\tknowledge/service/pages.py\tos.O_RDONLY | os.O_NOFOLLOW |\tos.O_RDONLY |\ttests/test_service_unit.py tests/test_hotswap.py'
  $'KGE-06：读文件不对描述符查属组可写\tknowledge/service/pages.py\t        if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):\n            raise SnapshotLoadError("页面快照的文件或目录对属组或其他用户可写")\n        chunks\t        chunks\ttests/test_service_unit.py'
  $'KGE-06：指针路径含 .. 也收\tknowledge/service/hotswap.py\t    if ".." in Path(text).parts:\t    if False:\ttests/test_hotswap.py'
  $'KGE-06：指针所在目录可写也收\tknowledge/service/hotswap.py\t    if parent.st_mode & (stat.S_IWGRP | stat.S_IWOTH):\t    if False:\ttests/test_hotswap.py'
  $'KGE-06：快照根目录不查\tknowledge/service/hotswap.py\t    if real == base or os.path.commonpath([real, base]) != base:\t    if False:\ttests/test_hotswap.py'
  $'KGE-06：快照根目录不解析符号链接\tknowledge/service/hotswap.py\t    real, base = os.path.realpath(target), os.path.realpath(root)\t    real, base = os.path.abspath(target), os.path.abspath(root)\ttests/test_hotswap.py'
  $'KGE-06：快照根目录配置不要求绝对路径\tknowledge/service/config.py\t        if root and not os.path.isabs(root):\t        if False:\ttests/test_hotswap.py'
  $'KGE-06：未配置密钥不记录未验签\tknowledge/service/hotswap.py\t        log.warning("page snapshot manifest not signature-checked (%s not configured)", HMAC_KEY_ENV)\t        pass\ttests/test_hotswap.py'
  $'KGE-06：HMAC 不比签名\tknowledge/service/hotswap.py\t        if not hmac.compare_digest(want.encode("ascii"), text.encode("ascii")):\t        if False:\ttests/test_hotswap.py'
  $'KGE-06：缺签名文件也放过\tknowledge/service/hotswap.py\t        sig = read_safe(root / SIGNATURE_FILE, \t        if not (root / SIGNATURE_FILE).exists():\n            return\n        sig = read_safe(root / SIGNATURE_FILE, \ttests/test_hotswap.py'
  $'KGE-06：密钥长度不查\tknowledge/service/hotswap.py\t        if len(key) < MIN_KEY_CHARS:\t        if False:\ttests/test_hotswap.py'
  $'KGE-06：指针文件放过相对路径与多行\tknowledge/service/hotswap.py\t    if not text or "\\n" in text or not os.path.isabs(text):\t    if not text:\ttests/test_hotswap.py'
  $'KGE-06：指针文件放过符号链接与组可写\tknowledge/service/hotswap.py\t    raw = read_safe(path, "快照指针文件读不了")\t    raw = path.read_bytes()\ttests/test_hotswap.py'
  $'KGE-06：两个快照配置项同时设也放过\tknowledge/service/config.py\t        if snap and pointer:\t        if False:\ttests/test_hotswap.py'
  $'KGE-06：M1 摘要不排序键\tknowledge/m1.py\tensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False\tensure_ascii=False, sort_keys=False, separators=(",", ":"), allow_nan=False\ttests/test_m1.py'
  $'KGE-06：M1 摘要转义中文\tknowledge/m1.py\tensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False\tensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False\ttests/test_m1.py'
  $'KGE-06：M1 不记 manifest 摘要\tknowledge/m1.py\t            "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),\n\t\ttests/test_m1.py tests/test_m2.py'
  $'KGE-06：M1 载入不用算摘要的那份 manifest 字节\tknowledge/m1.py\t        pages = load_snapshot_dir(page_snapshot_dir, manifest_raw=manifest_raw)\t        pages = load_snapshot_dir(page_snapshot_dir)\ttests/test_m1.py'
  $'KGE-06：M2 摘要口径与 M1 不同（原始字节）\tknowledge/m2.py\t        digest = snapshot_digest(map_src.data)\t        digest = map_src.sha256\ttests/test_m2.py'
  $'KGE-06：M2 不核对地图摘要\tknowledge/m2.py\t(MAP_DIGEST_MISMATCH if theirs != mine else None)\tNone\ttests/test_m2.py'
  $'KGE-06：M2 旧版 M1 没有摘要照算\tknowledge/m2.py\t        note = MAP_DIGEST_MISSING if theirs is None else\t        note = None if theirs is None else\ttests/test_m2.py'
  $'KGE-06：M2 不核对 manifest 摘要\tknowledge/m2.py\t    if pages_src is None or theirs_manifest is None or theirs_manifest == pages_src.sha256:\t    if True:\ttests/test_m2.py'
  $'KGE-06：M2 摘要不合格式也收\tknowledge/m2.py\t    if digest is not None and (not isinstance(digest, str) or not SHA256.fullmatch(digest)):\t    if False:\ttests/test_m2.py'
  # LT-03 健康端点（specs/launch-lt03-cloud-staging 范围第 4 条；假连接池，不连库）
  $'健康：就绪不等启动前置\tknowledge/service/app.py\thealth.pool_alive(state.get("ready_pool")))\thealth.pool_alive(state.get("ready_pool") or pool))\ttests/test_health.py'
  $'健康：关闭后仍就绪\tknowledge/service/app.py\t            state.pop("ready_pool", None)\n\t\ttests/test_health.py'
  $'健康：就绪不查库\tknowledge/service/health.py\t    return pool is not None and await pool.fetchval("SELECT 1") == 1\t    return pool is not None\ttests/test_health.py'
  $'健康：就绪不限时\tknowledge/service/health.py\t        return await asyncio.wait_for(probe(), timeout) is True\t        return await probe() is True\ttests/test_health.py'
  $'健康：存活也查库\tknowledge/service/health.py\t    async def healthz() -> JSONResponse:\n        return alive()\t    async def healthz() -> JSONResponse:\n        return await ready(probe)\ttests/test_health.py'
  # LT-03 决定记录（续）第 6 条：staging 连库 TLS；契约审查阻断 2：额外允许 Host（Service clusterIP）
  $'连库：staging 不查 TLS\tknowledge/service/config.py\t    if env == STAGING and not db_tls_ok(url):\t    if False:\ttests/test_staging_env.py'
  $'连库：CA 不限固定路径\tknowledge/service/config.py\t            and q.get("sslrootcert") == [RDS_CA_PATH] and os.path.isfile(RDS_CA_PATH))\t            and os.path.isfile(RDS_CA_PATH))\ttests/test_staging_env.py'
  $'连库：启动不查 TLS\tknowledge/service/app.py\t        check_db_tls(settings.database_url, settings.env)  # LT-03\t        pass  # LT-03\ttests/test_staging_env.py'
  $'额外 Host：收任意写法\tknowledge/service/config.py\t    if not private_ipv4(raw):\n        raise RuntimeError("KNOWLEDGE_EXTRA_HOST\t    if False:\n        raise RuntimeError("KNOWLEDGE_EXTRA_HOST\ttests/test_staging_env.py'
  $'额外 Host：不进允许列表\tknowledge/service/mcp_server.py\t    if extra:\n        names.append(extra)\t    pass\ttests/test_staging_env.py'
  $'额外 Host：MCP 的允许列表没带上\tknowledge/service/mcp_server.py\t    hosts = allowed_hosts(host, extra)\t    hosts = allowed_hosts(host)\ttests/test_staging_env.py'
  $'健康：不注册健康端点（落到 MCP 令牌前置）\tknowledge/service/app.py\t    cached = health.register(app, lambda: health.pool_alive(state.get("ready_pool")))\n\t    cached = None\n\ttests/test_health.py'
  # LT-05 可观测性（service/obs.py，三处逐字相同；完整用例在学习服务）：指标端点暴露范围（在 MCP 之前）、路由模板标签、脱敏
  $'可观测：业务端口的 /metrics 落到 MCP 令牌前置（不在挂载前拦下）\tknowledge/service/app.py\t    obs.register(app, "knowledge", pools=lambda: [state.get("ready_pool")], ready=cached, prefixes=(MCP_PATH,))\n\t\ttests/test_obs.py'
  $'可观测：MCP 请求不按前缀计\tknowledge/service/app.py\tready=cached, prefixes=(MCP_PATH,))\tready=cached, prefixes=())\ttests/test_obs.py'
  $'可观测：/metrics 可以缓存\tknowledge/service/obs.py\tHEADERS = {"cache-control": "no-store", "x-content-type-options": "nosniff"}\tHEADERS = {"x-content-type-options": "nosniff"}\ttests/test_obs.py'
  $'可观测：指标端口不限方法\tknowledge/service/obs.py\t    elif scope.get("method") != "GET":\t    elif False:\ttests/test_obs.py'
  $'可观测：不起指标监听\tknowledge/service/config.py\t    mport = metrics_port(\t    mport = None and metrics_port(\ttests/test_obs.py'
  $'可观测：脱敏不丢禁止字段\tknowledge/service/obs.py\t    return bool(_FORBIDDEN_KEY.fullmatch(normalize_key(key)))\t    return False\ttests/test_obs.py'
  $'可观测：脱敏不替换手机号\tknowledge/service/obs.py\t    re.compile(r"(?<!\\d)(?:\\+?86[- ]?)?1[3-9]\\d[ -]?\\d{4}[ -]?\\d{4}(?!\\d)"),\n\t\ttests/test_obs.py'
  $'可观测：手机号不认连字符分隔\tknowledge/service/obs.py\t1[3-9]\\d[ -]?\\d{4}[ -]?\\d{4}(?!\\d)\t1[3-9]\\d[ ]?\\d{4}[ ]?\\d{4}(?!\\d)\ttests/test_obs.py'
  $'可观测：键名不先驼峰归一\tknowledge/service/obs.py\t    return re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\\1", str(key)).replace("-", "_").lower()\t    return str(key).lower()\ttests/test_obs.py'
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
  $'知识服务：page.get 未发布页回退到其他页面\tknowledge/service/tools.py\t        pp = snap.get(body["page_id"])\t        pp = snap.get(body["page_id"]) or next(iter(snap.pages.values()))\ttests/test_service_e2e.py'
  $'知识服务：page.get 不查节点是否在已发布版本\tknowledge/service/tools.py\t        if n is None:  # 节点已撤下\t        if False:  # 节点已撤下\ttests/test_service_e2e.py'
  $'知识服务：page.get 教材投影不按版次取\tknowledge/service/tools.py\tawait self.reader.textbook_projections(v.id, pp.node_id, edition)]\tawait self.reader.textbook_projections(v.id, pp.node_id, None)]\ttests/test_service_e2e.py'
  $'知识服务：快照导出放过体检阻断\tknowledge/compiler/snapshot.py\t        blocks = [i for i in report.blocks if i.page_id not in held]\t        blocks = []\ttests/test_service_e2e.py'
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
  $'审查：整个应用不校验 Host\tknowledge/service/app.py\t    app.add_middleware(HostGuard, allowed=allowed_hosts(settings.host, settings.extra_host))\n\t\ttests/test_service_mcp.py'
  $'审查：node.resolve 不带分词器版本\tknowledge/service/tools.py\t"source_version": f"{graph_source(subject, v)};{tokenizer_source()}"\t"source_version": graph_source(subject, v)\ttests/test_service_db.py'
  $'页面对账：快照导出照发被扣页面的新内容\tknowledge/compiler/snapshot.py\t            if PurePosixPath(name).stem in held:\t            if False:\ttests/test_service_e2e.py'
  $'页面对账：被扣页面不沿用上一份快照\tknowledge/compiler/snapshot.py\t        for pid, (raw, entry) in sorted(carried.items()):\t        for pid, (raw, entry) in sorted({}.items()):\ttests/test_service_e2e.py'
  $'页面对账：沿用的页面不记来源提交\tknowledge/compiler/snapshot.py\t            entry["source_commit"] = e.get("source_commit") or snap.commit\t            pass\ttests/test_service_e2e.py'
  $'页面对账：快照导出不读对账报告\tknowledge/compiler/snapshot.py\t        m = export_snapshot(a.repo, a.commit, a.out, today=today, branch=a.branch, hold=held, previous=a.previous)\t        m = export_snapshot(a.repo, a.commit, a.out, today=today, branch=a.branch, previous=a.previous)\ttests/test_service_e2e.py'
  $'页面对账：page.get 不用被扣页的来源提交\tknowledge/service/tools.py\t        commit = (pp.source_commit or snap.commit)[:12]\t        commit = snap.commit[:12]\ttests/test_service_e2e.py'
  $'页面对账：载入不核对来源提交写法\tknowledge/service/pages.py\t        if source_commit is not None and (\t        if False and (\ttests/test_service_e2e.py'
  # KGE-06（specs/kge06-weekly-revision 范围 4）：热切换期间的请求（库上）
  $'KGE-06：page.get 第二次读快照（切换途中页面与提交号对不上）\tknowledge/service/tools.py\t        commit = (pp.source_commit or snap.commit)[:12]\t        commit = (pp.source_commit or self.pages.commit)[:12]\ttests/test_hotswap.py'
  $'KGE-06：不装 SIGHUP 处理器\tknowledge/service/app.py\t                asyncio.get_running_loop().add_signal_handler(signal.SIGHUP, swapper.request_reload)\t                pass\ttests/test_hotswap.py'
  $'KGE-06：只在配置了指针文件时装 SIGHUP 处理器\tknowledge/service/app.py\t                asyncio.get_running_loop().add_signal_handler(signal.SIGHUP, swapper.request_reload)\t                if settings.page_snapshot_pointer is not None:\n                    asyncio.get_running_loop().add_signal_handler(signal.SIGHUP, swapper.request_reload)\ttests/test_hotswap.py'
  $'KGE-06：关停不等进行中的 reload\tknowledge/service/app.py\t                await swapper.drain()\t                pass\ttests/test_hotswap.py'
  $'KGE-06：没配置指针文件时 SIGHUP 也排 reload\tknowledge/service/hotswap.py\t        if self.pointer is None:\n            log.warning\t        if False:\n            log.warning\ttests/test_hotswap.py'
  $'KGE-06：切换不替换已有快照（仍服务旧快照）\tknowledge/service/hotswap.py\t        self.core.pages = snap  # 一次引用赋值\t        self.core.pages = self.core.pages or snap  # 一次引用赋值\ttests/test_hotswap.py'
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
  for d in docs db packages deploy; do ln -s "$ROOT/$d" "$dst/$d"; done  # deploy：LT-05 指标登记清单（只读）
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
  if ! MUT_ROOT="$dst" MUT_FILE="$mut_file" MUT_ORIG="$orig" MUT_REPL="$repl" "$PY" - <<'PY'
import os, sys
p = os.environ["MUT_FILE"]; o = os.environ["MUT_ORIG"]; r = os.environ["MUT_REPL"]
# 变异目标必须是副本里复制出来的文件：软链接回主仓的 deploy/ 等（真实路径不在副本里）一律拒绝，免得改到主仓
if os.path.islink(p) or not os.path.realpath(p).startswith(os.path.realpath(os.environ["MUT_ROOT"]) + os.sep):
    sys.exit(1)
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
