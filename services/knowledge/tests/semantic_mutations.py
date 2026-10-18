"""KGE-11 第一步：规则式语义体检、文件式编入事件与审核台分流建议的变异测试。逐条把检查规则、比对范围、报告层、分级配置、
输入校验、编入清单与分流开关改坏，断言 tests/test_semantic.py、tests/test_semantic_events.py（知识服务）与
services/learning/tests/test_review_triage.py（审核台）能发现。

做法与 tests/material_mutations.py、tests/cache_mutations.py 相同（单独成目标 knowledge-semantic-mutation）：把要改的服务目录
复制到临时目录（保持仓库结构；另一个服务与 docs、db、packages、evals、tools、adapters 软链接回仓库），改一处源码，跑指定测试。
先跑一次不变异的对照（两边都要通过）；变异后只有“pytest 退出码 1（有断言失败）”才算被发现；收集错误（退出码 2 及以上）、
原文找不到或不唯一都算变异本身写错。每条限时 180 秒，超时算漏检。并行 4 条。不连库、不联网。
审核台一侧用 services/learning/.venv 的解释器；没有时退出码 3（部分跳过，严格模式下 Makefile 判失败）。
用法（仓库根目录）：services/knowledge/.venv/bin/python services/knowledge/tests/semantic_mutations.py
退出码：0 全部被发现；1 有漏检或对照不过；3 没有安装依赖（部分跳过）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SVC = Path(__file__).resolve().parents[1]
ROOT = SVC.parents[1]
LEARNING = ROOT / "services" / "learning"
LEARNING_PY = LEARNING / ".venv" / "bin" / "python"
K_TESTS = ["tests/test_semantic.py", "tests/test_semantic_events.py"]
L_TESTS = ["tests/test_review_triage.py"]
SC, SI, SN, SF, SM = ("knowledge/semantic/checks.py", "knowledge/semantic/inputs.py", "knowledge/semantic/__init__.py",
                      "knowledge/semantic/config.py", "knowledge/semantic/__main__.py")
IS, EV, CM = "knowledge/lint/issues.py", "knowledge/compiler/events.py", "knowledge/compiler/__main__.py"
RK = "review_console/risk.py"
K, L = "knowledge", "learning"

# （名字，服务，文件（相对该服务目录），原文，替换为）
MUTATIONS: list[tuple[str, str, str, str, str]] = [
    # 与原子冲突
    ("公式只按一个方向比", K, SC, "for mine, theirs in ((left, right), (right, left)):", "for mine, theirs in ((left, right),):"),
    ("公式冲突不报", K, SC, "if not res.ok and res.verifiable:", "if False:"),
    ("公式与所有节点比（不限所引原子）", K, SC,
     'node_atoms = [a for r in page.atom_refs if r.type == "node" and (a := snap.get("node", r.id)) is not None]',
     'node_atoms = snap.of_type("node")'),
    ("权重容差放宽到 1", K, SC, "if not any(abs(float(m.group(1)) - w) < 5e-4 for w in weights):",
     "if not any(abs(float(m.group(1)) - w) < 1 for w in weights):"),
    ("占比不换算百分数", K, SC, "abs(float(m.group(1)) / 100 - w)", "abs(float(m.group(1)) - w)"),
    ("考试年份不核对", K, SC, "if int(m.group(1)) not in years:", "if False:"),
    ("课标层次不核对", K, SC, "if _VERB_ZH[zh] not in allowed:", "if False:"),
    ("页码只看起始页不看上限", K, SC, 'int(a.record["page_from"]) <= lo <= hi <= int(a.record["page_to"])', 'int(a.record["page_from"]) <= lo'),
    ("不包括：忽略否定", K, SC, "if x not in sent or _NEGATION.search(sent) or not _INCLUDE_MARK.search(sent):",
     "if x not in sent or not _INCLUDE_MARK.search(sent):"),
    ("不包括：不要求提到本节点", K, SC, "if any(n and n in rest for n in names):", "if True:"),
    # 跨页前置方向
    ("方向相反不报（降为没有边）", K, SC, "        if _edge(ctx.snapshot, post, pre):\n", "        if False:\n"),
    ("句式方向不按标记", K, SC, "pre, post = (a, b) if first_is_pre else (b, a)", "pre, post = (a, b)"),
    ("“本节”不算本节点", K, SC, "            elif mt.group(2) is not None:\n                nid = own",
     "            elif mt.group(2) is not None:\n                return \" \""),
    ("页面链接不算节点提及", K, SC, "                nid = mt.group(1)\n", "                nid = \"\"\n"),
    ("相邻页说法相反不报", K, SC, "if (pre2, post2) == (post, pre):", "if False:"),
    ("术语不按最长匹配", K, SC, "words = sorted(owner, key=lambda w: (-len(w), w))\n        pat", "words = sorted(owner)\n        pat"),
    # 相邻页术语写法
    ("术语写法不一致不报", K, SC, "odd = sorted(f for f in mine if f != name and f not in theirs)", "odd = []"),
    # 版本差异误判
    ("页码未标版次不报", K, SC, "        for m in _PAGE_REF.finditer(text):\n            if not editions:",
     "        for m in _PAGE_REF.finditer(\"\"):\n            if not editions:"),
    ("版次不符不报", K, SC, "elif ed not in editions:", "elif False:"),
    ("册名章名不算版次专属表述", K, SC, "if len(v) >= 2:", "if False:"),
    ("两版共有的章名也算专属表述", K, SC, "if len(eds) == 1}", "if eds}"),
    ("单版次也从投影推断", K, SC, 'if len({str(a.record["edition"]) for a in projections}) >= 2:', "if True:"),
    ("不剔除与节点名重合的词", K, SC, "return {w: ed for w, ed in out.items() if not any(w == n or w in n or n in w for n in names)}",
     "return out"),
    ("重合只看相等", K, SC, "if not any(w == n or w in n or n in w for n in names)}", "if not any(w == n for n in names)}"),
    # 超纲
    ("节点放行不生效", K, SI, "allow = self.node_allow.get(node_id, frozenset())", "allow = frozenset()"),
    ("学段不看适用范围", K, SI, "if s not in self.stages and t not in allow", "if t not in allow"),
    # 适龄文风与角色语言
    ("文风不去掉公式", K, SC, 'text = re.sub(r"\\$[^$]*\\$", "式", strip_refs(ln))', "text = strip_refs(ln)"),
    ("文风缺省级别改成阻断", K, SF, '        return levels["age_style.default"]', '        return "block"'),
    ("角色语言重复报程序层已报的", K, SC, "if nw and nw in flat and not any(word in ln for ln in lines):", "if nw and nw in flat:"),
    ("归一化不去标点空白", K, SC, 'if unicodedata.category(ch)[0] not in "PSZC")', "if True)"),
    ("归一化后不查角色设定元素", K, SC, "        for rule in lex.setting_patterns:\n            if re.search(rule.regex, flat)",
     "        for rule in ():\n            if re.search(rule.regex, flat)"),
    # 比对范围
    ("相邻不含同主题", K, SN, "    if topic is not None:\n", "    if False:\n"),
    ("同主题按两段前缀", K, SN, "TOPIC_SEGMENTS = 3", "TOPIC_SEGMENTS = 2"),
    ("受影响页不在目录时静默忽略", K, SN, "    missing = tuple(sorted(p for p in ctx.affected or () if p not in ctx.pages))",
     "    missing: tuple[str, ...] = ()"),
    ("合成超纲词表不提示", K, SN, "    elif ctx.scope_vocab.synthetic:", "    elif False:"),
    ("报告不标词表的 synthetic", K, SN, 'return None if x is None else bool(getattr(x, "synthetic", True))', "return None if x is None else False"),
    ("报告不列被跳过的检查", K, SN, '         "skipped_checks": sorted(skipped)})', '         "skipped_checks": []})'),
    ("报告的检查页面不限范围", K, SN, '{"pages": scope_pages(ctx.files, scope), "affected"', '{"pages": scope_pages(ctx.files), "affected"'),
    ("报告不带词表摘要", K, SN, '"scope_vocab": ctx.scope_vocab.digest if ctx.scope_vocab else None,', '"scope_vocab": None,'),
    ("报告不说明分级覆盖", K, SF, 'return {"levels_default": not overrides, "levels_overrides": overrides}',
     'return {"levels_default": True, "levels_overrides": {}}'),
    ("相邻不含易混", K, SN, 'NEIGHBOR_EDGE_TYPES = ("prerequisite", "confusable")', 'NEIGHBOR_EDGE_TYPES = ("prerequisite",)'),
    ("相邻页不进检查范围", K, SN, "                scope.add(other)\n", ""),
    ("忽略范围、每页都查", K, SN, "    for pid in scope:\n", "    for pid in ctx.pages:\n"),
    ("全量两两比对", K, SN, "        for n in skeleton_neighbors(snap, node_id):", "        for n in by_node:"),
    ("没有超纲词表不提示", K, SN, '        skipped.append("out_of_scope")\n        issues.append(', '        skipped.append("out_of_scope")\n        print('),
    # 报告层与分级配置
    ("条目层写死为 program", K, IS, '"page_version": self.page_version, "layer": self.layer,', '"page_version": self.page_version, "layer": "program",'),
    ("报告顶层层写死为 program", K, IS, '"format": REPORT_FORMAT, "layer": self.layer,', '"format": REPORT_FORMAT, "layer": "program",'),
    ("报告收别层的条目", K, IS, "if any(i.layer != self.layer for i in self.issues):", "if False:"),
    ("报告格式仍是 /1", K, IS, 'REPORT_FORMAT = "kge-lint-report/1.1"', 'REPORT_FORMAT = "kge-lint-report/1"'),
    ("程序体检不报没给快照跳过的检查", K, "knowledge/lint/__init__.py",
     "skipped = [] if ctx.snapshot is not None else list(SNAPSHOT_CHECKS)", "skipped = []"),
    ("程序体检不报没给基线跳过的检查", K, "knowledge/lint/__init__.py", "    if not ctx.baseline:\n        skipped", "    if False:\n        skipped"),
    ("程序体检报告不列页面", K, "knowledge/lint/__init__.py", '{"pages": scope_pages(ctx.files)},', '{"pages": []},'),
    ("快照摘要不含原子", K, "knowledge/lint/binding.py", '"atoms": {k: a.hash for k, a in snap.atoms.items()}}', '"atoms": {}}'),
    ("M1 收语义体检报告", K, "knowledge/m1.py", 'if report["format"] != "kge-lint-report/1" and report.get("layer") != "program":', "if False:"),
    ("M1 收语义条目", K, "knowledge/m1.py", 'if any(isinstance(i, dict) and i.get("layer", "program") != "program" for i in report["issues"]):',
     "if False:"),
    ("M1 只收 /1", K, "knowledge/m1.py", 'report.get("format") not in LINT_READ_FORMATS', 'report.get("format") != "kge-lint-report/1"'),
    ("检查类型不核对层", K, IS, "if self.check_type not in CHECK_TYPES_BY_LAYER.get(self.layer, ()):", "if False:"),
    ("分级配置收未登记的键", K, SF, '        if k not in DEFAULT_LEVELS and not (_STYLE_KEY.match(k) and k.split(".", 1)[1] in style_ids):',
     "        if False:"),
    ("分级配置收非法级别", K, SF, "        if v not in LEVEL_VALUES:", "        if False:"),
    # 输入校验
    ("别名可以重复", K, SI, "            if a in seen:\n", "            if False:\n"),
    ("文风正则可匹配空串", K, SI, 'if compiled.search(""):', "if False:"),
    ("文风词表可用保留名", K, SI, ' or r["id"] in RESERVED_STYLE_IDS:', ":"),
    ("语义命令行不核对受影响页 ID", K, SM, "            if any(not is_node_id(p) for p in affected):", "            if False:"),
    ("编入清单的 page_id 不核对规范", K, SM, 'if len(ids) != len(raw["jobs"]) or any(not is_node_id(p) for p in ids):',
     'if len(ids) != len(raw["jobs"]):'),
    ("编入清单的条目可以不是对象", K, SM, 'if len(ids) != len(raw["jobs"]) or any(', "if any("),
    ("编入清单地图版本不核对", K, SM, "if (to is None and ids) or (to is not None and to != map_version):", "if False:"),
    # 编入清单
    ("清单变更类型不核对", K, EV, "if diff.get(c.key) != c.change:", "if diff.get(c.key) is None:"),
    ("清单地图版本不核对", K, EV, "if (frm, to) != (old.map_version, new.map_version):", "if False:"),
    ("编入用整个新快照（不限清单）", K, EV,
     "    return Snapshot(new.map_version, new.subject, new.status, new.standard_year, MappingProxyType(atoms)), unlisted",
     "    return new, unlisted"),
    ("下架原子不去掉", K, EV, "            atoms.pop(c.key)", "            pass"),
    ("触发原子不带来源类型", K, EV, '"source_type": sources.get(t.key)}', '"source_type": None}'),
    ("来源类型不核对", K, EV, 'if c["source_type"] not in SOURCE_ATOM_TYPES:', "if False:"),
    ("来源类型与原子类型不交叉核对", K, EV, 'if atype not in SOURCE_ATOM_TYPES[c["source_type"]]:', "if False:"),
    ("漏列差异不报错", K, EV, "if unlisted and not allow_partial:", "if False:"),
    ("漏列数不计", K, EV, "unlisted = len(set(diff) - {c.key for c in changes})", "unlisted = 0"),
    ("信号命中键不核对", K, EV, "if not isinstance(hits, list) or not all(_valid_hit(h) for h in hits):", "if not isinstance(hits, list):"),
    ("页面命中键不核对节点 ID", K, EV, "        return is_node_id(m.group(1))", "        return True"),
    ("原子命中键不核对类型与 ID", K, EV, "return bool(m) and m.group(1) in ATOM_TYPES and valid_atom_id(m.group(1), m.group(2))", "return bool(m)"),
    ("信号 node_id 不核对", K, EV, "if nid is not None and not is_node_id(nid):", "if False:"),
    ("信号问法摘要不核对", K, EV, ' or not isinstance(d["query_digest"], str) or not _DIGEST.match(d["query_digest"]):', ":"),
    ("信号门槛只看次数", K, EV, 'if row["count"] < min_group or row["distinct_queries"] < min_group:', 'if row["count"] < min_group:'),
    ("信号不设门槛", K, EV, 'if row["count"] < min_group or row["distinct_queries"] < min_group:', "if False:"),
    ("清单可以覆盖输入文件", K, EV, "        if Path(p).resolve() == target:", "        if False:"),
    ("清单可以写进页面目录", K, EV, "if target == pages or pages in target.parents:", "if False:"),
    ("清单不用原子写", K, EV, "        os.replace(tmp, target)", "        target.write_text(text, encoding=\"utf-8\")"),
    ("信号去向一律补内容", K, EV, 'action = "review_page" if page_id else ("new_page" if nid else "triage")', 'action = "review_page"'),
    ("信号收白名单外字段", K, EV, "set(d) != set(SIGNAL_FIELDS)", "not set(SIGNAL_FIELDS) <= set(d)"),
    ("编译器命令行不接子命令", K, CM, 'if argv[:1] == ["plan-events"]:', "if False:"),
    # 审核台分流
    ("分流开关缺省开", L, RK, "SEMANTIC_TRIAGE_DEFAULT = False", "SEMANTIC_TRIAGE_DEFAULT = True"),
    ("开关按真值判断", L, RK, "    if enabled is not True:", "    if not enabled:"),
    ("不看程序体检", L, RK, 'if lint_blocks(program_report, "program", **bind) == 0 and lint_blocks(semantic_report, "semantic", **bind) == 0',
     'if lint_blocks(semantic_report, "semantic", **bind) == 0'),
    ("不看语义体检", L, RK, 'if lint_blocks(program_report, "program", **bind) == 0 and lint_blocks(semantic_report, "semantic", **bind) == 0',
     'if lint_blocks(program_report, "program", **bind) == 0'),
    ("不核对两份报告的快照摘要", L, RK, "            and _same_snapshot(program_report, semantic_report):", "            and True:"),
    ("不核对顶层层", L, RK, ' or report.get("layer") != layer:', ":"),
    ("不核对地图版本", L, RK, '    if report.get("map_version") != map_version:\n        return None', "    if False:\n        return None"),
    ("不核对被审版本", L, RK, 'and p.get("page_version") == page_version for p in covered)', "for p in covered)"),
    ("不核对被跳过的检查", L, RK, 'if config.get("skipped_checks") != []:', "if False:"),
    ("不核对语义层配置", L, RK, '    if layer == "semantic":\n        synthetic', "    if False:\n        synthetic"),
    ("词表缺失视同非合成", L, RK, 'or synthetic.get("scope_vocab") is not False or synthetic.get("style_lexicon") is not False:', "or False:"),
    ("词表缺失提示不拦", L, RK, 'if any(isinstance(i.get("detail"), dict) and i["detail"].get("rule") in SEMANTIC_SKIP_RULES for i in issues):',
     "if False:"),
    ("报告字段不核对类型", L, RK,
     "if not isinstance(issues, list) or not isinstance(summary, dict) or not isinstance(scope, dict) or not isinstance(config, dict):",
     "if not isinstance(issues, list):"),
    ("报告不核对是对象", L, RK, 'if not isinstance(report, dict) or report.get("format") != LINT_REPORT_FORMAT', 'if report.get("format") != LINT_REPORT_FORMAT'),
    ("结构性不拦", L, RK, "if level not in LEVELS or structural is not False:", "if level not in LEVELS:"),
    ("报告不核对层", L, RK, ' or i.get("layer") != layer', ""),
    ("报告不核对严重级别", L, RK, ' or i.get("severity") not in ("block", "warn")', ""),
    ("摘要与条目不核对", L, RK, 'if summary.get("blocks") != blocks or summary.get("warnings") != len(issues) - blocks:', "if False:"),
    ("报告可以零页", L, RK, " or pages < 1:", ":"),
    ("页数可以是布尔值", L, RK, " or isinstance(pages, bool)", ""),
    ("提示也算阻断", L, RK, 'blocks = sum(1 for i in issues if i["severity"] == "block")', "blocks = len(issues)"),
    ("认不出的级别不按高", L, RK, "if level not in LEVELS or structural is not False:", "if structural is not False:"),
    ("建议人数表与常量不一致", L, RK, 'SUGGESTED_REVIEWERS = {"low": 0, SPOT_CHECK: 0, "high": 2}', 'SUGGESTED_REVIEWERS = {"low": 0, SPOT_CHECK: 0, "high": 1}'),
    ("改了 review_level 的写法", L, RK, '    return "high" if level == "medium" else level', '    return level if level != "medium" else "high"'),
]


def copy_tree(dst: Path, service: str) -> None:
    (dst / "services").mkdir(parents=True)
    for name in ("knowledge", "learning"):
        if name == service:
            shutil.copytree(ROOT / "services" / name, dst / "services" / name,
                            ignore=shutil.ignore_patterns(".venv", "__pycache__", ".cache", ".pytest_cache", "*.pyc"))
        else:
            (dst / "services" / name).symlink_to(ROOT / "services" / name)
    for d in ("docs", "db", "packages", "evals", "tools", "adapters"):
        if (ROOT / d).exists():
            (dst / d).symlink_to(ROOT / d)


def run_tests(root: Path, service: str) -> int:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    py, tests = (sys.executable, K_TESTS) if service == K else (str(LEARNING_PY), L_TESTS)
    try:
        r = subprocess.run([py, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *tests], cwd=root / "services" / service,
                           env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180, check=False)
    except subprocess.TimeoutExpired:
        return 124
    return r.returncode


def one(work: Path, i: int, mutation: tuple[str, str, str, str, str]) -> str | None:
    name, service, rel, orig, repl = mutation
    dst = work / f"m{i}"
    copy_tree(dst, service)
    try:
        path = dst / "services" / service / rel
        text = path.read_text(encoding="utf-8")
        if text.count(orig) != 1:
            return f"  变异本身写错（原文找不到或不唯一）：{name}"
        path.write_text(text.replace(orig, repl), encoding="utf-8")
        rc = run_tests(dst, service)
        if rc == 0:
            return f"  漏检：{name}"
        if rc == 124:
            return f"  超时（算漏检）：{name}"
        if rc != 1:
            return f"  变异本身写错（pytest 退出码 {rc}）：{name}"
        return None
    finally:
        shutil.rmtree(dst, ignore_errors=True)


def main() -> int:
    try:
        import pytest  # noqa: F401, PLC0415
        import yaml  # noqa: F401, PLC0415
    except ImportError:
        print("knowledge-semantic-mutation: 跳过（没有安装依赖；见 services/knowledge/README.md）")
        return 3
    have_learning = LEARNING_PY.is_file()
    todo = [m for m in MUTATIONS if have_learning or m[1] != L]
    if not have_learning:
        print(f"knowledge-semantic-mutation: 没有安装审核台依赖（services/learning/.venv），审核台一侧的 {len(MUTATIONS) - len(todo)} 条跳过")
    with tempfile.TemporaryDirectory(prefix="kge-sem-mut-") as tmp:
        work = Path(tmp)
        for service in (K, L) if have_learning else (K,):
            control = work / f"control-{service}"
            copy_tree(control, service)
            rc = run_tests(control, service)
            shutil.rmtree(control, ignore_errors=True)
            if rc != 0:
                print(f"knowledge-semantic-mutation: 对照（未变异，{service}）没有通过（退出码 {rc}），变异结果不可信")
                return 1
        with ThreadPoolExecutor(max_workers=4) as ex:
            results = list(ex.map(lambda im: one(work, *im), enumerate(todo)))
    missed = [r for r in results if r]
    for r in missed:
        print(r)
    if missed:
        print(f"knowledge-semantic-mutation: {len(missed)} / {len(todo)} 条没有被测试发现")
        return 1
    print(f"knowledge-semantic-mutation: {len(todo)} 条变异全部被测试发现")
    return 0 if have_learning else 3


if __name__ == "__main__":
    sys.exit(main())
