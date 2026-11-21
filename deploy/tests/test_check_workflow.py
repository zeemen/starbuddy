"""GitHub Actions 全量检查（.github/workflows/check.yml）与 Makefile 分组的一致性。

CI 把 make check 拆成并行任务：check-core 按 check-group-* 分 11 组，db-mutation 按 DBMUT_SHARD 分片。
这里核对拆分没有漏掉任何检查：
- CI 矩阵里的组与 Makefile 里定义的 check-group-* 一一对应；
- check-core 恰好由各组的 CHECK_GROUP_* 变量组成，check = check-core + db-mutation；
- 各组之间没有重复的检查目标；
- db-mutation 的分片编号是 1…N 连续，DBMUT_SHARD 的分母就是 N；
- PR 与 push 到 main 只跑轻量检查（各组的 check-group-<组名>-light = 该组去掉 *-mutation）；变异测试（各组的
  check-group-<组名>-mutation 与 db-mutation）只在手动触发（workflow_dispatch，scope = mutation / full）时跑
  （2026-11-03 用户决定 PR 只跑轻量；PR #27、specs/ci-compute-diet 把 push 到 main 也改为只跑轻量）；
- 对每一组，-light 与 -mutation 两份恰好互补，合起来就是整组，没有检查掉出去。"""
from __future__ import annotations

import re

import yaml

from conftest import ROOT

MAKEFILE = (ROOT / "Makefile").read_text(encoding="utf-8")
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/check.yml").read_text(encoding="utf-8"))


def rule(target: str) -> list[str]:
    m = re.search(rf"^{re.escape(target)}:(.*)$", MAKEFILE, re.M)
    assert m, f"Makefile 里没有 {target}"
    return m.group(1).split()


SUFFIXES = ("-light", "-mutation")


def full_groups() -> list[str]:
    """Makefile 里的整组 check-group-<组名>（不含 -light、-mutation 两份）"""
    return [n for n in re.findall(r"^check-group-([a-z0-9-]+):", MAKEFILE, re.M) if not n.endswith(SUFFIXES)]


def group_vars() -> dict[str, list[str]]:
    return {v: deps.split() for v, deps in re.findall(r"^(CHECK_GROUP_[A-Z0-9_]+) := (.*)$", MAKEFILE, re.M)}


def test_matrix_groups_match_makefile_groups():
    matrix = WORKFLOW["jobs"]["make-check"]["strategy"]["matrix"]["group"]
    assert len(matrix) == len(set(matrix))
    assert sorted(matrix) == sorted(full_groups())
    run = " ".join(step.get("run", "") for step in WORKFLOW["jobs"]["make-check"]["steps"])
    assert 'make "check-group-${{ matrix.group }}$suffix"' in run and "CONTRACT_STRICT=1" in run
    assert 'suffix="-$SUITE"' in run and 'if [ "$SUITE" = "full" ]; then suffix=""; fi' in run


def test_check_core_is_exactly_the_union_of_groups():
    gv = group_vars()
    full = full_groups()
    assert len(gv) == len(full)
    assert rule("check-core") == [f"$({v})" for v in gv]
    for name in full:
        assert rule(f"check-group-{name}") == [f"$(CHECK_GROUP_{name.upper().replace('-', '_')})"]
    assert rule("check") == ["check-core", "db-mutation"]
    targets = [t for deps in gv.values() for t in deps]
    assert len(targets) == len(set(targets)), "有检查目标同时出现在两个组里"
    assert "db-mutation" not in targets


def test_db_mutation_shards_cover_one_to_n():
    job = WORKFLOW["jobs"]["db-mutation"]
    shards = job["strategy"]["matrix"]["shard"]
    assert shards == list(range(1, len(shards) + 1))
    run = " ".join(step.get("run", "") for step in job["steps"])
    assert f"DBMUT_SHARD=${{{{ matrix.shard }}}}/{len(shards)}" in run
    assert "CONTRACT_STRICT=1" in run


def test_fail_fast_only_on_pull_requests():
    # PR 上一组失败即早停（省计费分钟）；push 到 main 与手动触发要一次看全，不早停
    assert WORKFLOW["jobs"]["make-check"]["strategy"]["fail-fast"] == "${{ github.event_name == 'pull_request' }}"
    assert WORKFLOW["jobs"]["db-mutation"]["strategy"]["fail-fast"] is False


def test_triggers_and_suite_split():
    on = WORKFLOW.get("on", WORKFLOW.get(True))     # PyYAML 把键 on 读成 True
    assert set(on) == {"pull_request", "push", "workflow_dispatch"} and on["push"]["branches"] == ["main"]
    scope = on["workflow_dispatch"]["inputs"]["scope"]
    assert scope["default"] == "mutation" and scope["options"] == ["mutation", "light", "full"]
    # 非手动触发（PR、push 到 main）一律只跑轻量
    assert WORKFLOW["env"]["SUITE"] == "${{ github.event_name == 'workflow_dispatch' && inputs.scope || 'light' }}"
    assert WORKFLOW["jobs"]["db-mutation"]["if"] == (
        "github.event_name == 'workflow_dispatch' && (inputs.scope == 'mutation' || inputs.scope == 'full')")
    assert "if" not in WORKFLOW["jobs"]["make-check"]


def test_light_and_mutation_split_each_group_exactly():
    gv = group_vars()
    for name in full_groups():
        var = f"CHECK_GROUP_{name.upper().replace('-', '_')}"
        assert " ".join(rule(f"check-group-{name}-light")) == f"$(filter-out %-mutation,$({var}))", name
        assert " ".join(rule(f"check-group-{name}-mutation")) == f"$(filter %-mutation,$({var}))", name
    light = [t for deps in gv.values() for t in deps if not t.endswith("-mutation")]
    mutations = [t for deps in gv.values() for t in deps if t.endswith("-mutation")]
    assert mutations and light and "eval-persona" in light       # 人设回归每次都跑（规则 7）
    assert " ".join(rule("check-light")) == " ".join(f"$(filter-out %-mutation,$({v}))" for v in gv)
    assert " ".join(rule("check-mutation")) == " ".join(f"$(filter %-mutation,$({v}))" for v in gv)


def test_extra_browsers_job_only_on_manual_opt_in():
    """W13 T22（specs/tech-debt-batch3 E）：WebKit、Firefox 的数据权利用例只在手动触发且勾选 extra_browsers 时跑；只装这两个浏览器、
    只跑 playwright.config.ts 的两个 @data-rights 项目；PR 与 push 到 main 不受影响。"""
    on = WORKFLOW.get("on", WORKFLOW.get(True))
    extra = on["workflow_dispatch"]["inputs"]["extra_browsers"]
    assert extra["type"] == "boolean" and extra["default"] is False
    job = WORKFLOW["jobs"]["extra-browsers"]
    assert job["if"] == "github.event_name == 'workflow_dispatch' && inputs.extra_browsers"
    assert job["env"]["STARBUDDY_E2E_BROWSERS"] == "webkit,firefox"
    runs = [s.get("run", "") for s in job["steps"]]
    assert any("playwright install --with-deps webkit firefox" in r for r in runs)
    assert not any("chromium" in r for r in runs)
    e2e = [r for r in runs if "e2e.sh" in r]
    assert len(e2e) == 1 and "--project=trust-on-webkit" in e2e[0] and "--project=trust-on-firefox" in e2e[0]
    config = (ROOT / "apps/web/playwright.config.ts").read_text(encoding="utf-8")
    assert "name: `trust-on-${b}`" in config and "grep: /@data-rights/" in config


def test_cjk_path_job_checks_out_under_a_non_ascii_directory():
    """内测前整改待确认第 2 条（specs/launch-test-2026/prelaunch-fixes.md）：cjk-path 把仓库检出到含中文的目录，
    在那里跑 make cjk-path-test；PR 与 push 到 main 跑，手动触发只在 scope 不是 mutation 时跑。"""
    job = WORKFLOW["jobs"]["cjk-path"]
    assert job["if"] == "github.event_name != 'workflow_dispatch' || inputs.scope != 'mutation'"
    checkout = next(s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout@"))
    path = checkout["with"]["path"]
    assert any(ord(c) > 127 for c in path), "检出目录要含非 ASCII 字符"
    assert checkout["with"]["persist-credentials"] is False
    assert job["defaults"]["run"]["working-directory"] == path
    runs = [s.get("run", "") for s in job["steps"]]
    make = [r for r in runs if "make cjk-path-test" in r]
    assert len(make) == 1 and "CONTRACT_STRICT=1" in make[0]
    # 目标本身：目录不含非 ASCII 字符时失败；不在 check-core 里（本机多半测不到）
    assert "cjk-path-test" not in " ".join(rule("check-core")) and "CJK_PATH_TARGETS" not in " ".join(group_vars())
    assert "ord(c) > 127" in MAKEFILE.split("\ncjk-path-test:", 1)[1].split("\n\n", 1)[0]


def test_actions_are_pinned_to_commit_hashes():
    """第三方 Actions 一律固定到 40 位提交哈希，并在同一行注释对应版本（specs/ci-supply-chain-pins）；不许出现标签或分支"""
    text = (ROOT / ".github/workflows/check.yml").read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if re.match(r"^\s*(-\s*)?uses:", ln)]
    assert len(lines) >= 12, "uses 行数对不上，扫描写错了"
    for ln in lines:
        assert re.match(r"^\s*(-\s*)?uses:\s*[\w.-]+/[\w.-]+@[0-9a-f]{40}\s+#\s*v\d+\.\d+\.\d+\s*$", ln), ln
    # 解析出的 uses 值（YAML 去掉注释）也只能是 owner/repo@哈希
    uses = [s["uses"] for job in WORKFLOW["jobs"].values() for s in job.get("steps", []) if "uses" in s]
    assert len(uses) == len(lines)
    for u in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", u), u


# 各 Action 最早用 Node 24 运行时的大版本（2026-11-19 读各版本 action.yml 的 runs.using 核对，specs/ci-actions-node24）
MIN_NODE24_MAJOR = {"actions/checkout": 5, "actions/setup-python": 6, "actions/setup-node": 5, "actions/upload-artifact": 6}


def test_actions_not_below_node24_majors():
    """版本注释的大版本号不低于最早用 Node 24 的版本，防止退回 node20 的旧版本（specs/ci-actions-node24）"""
    text = (ROOT / ".github/workflows/check.yml").read_text(encoding="utf-8")
    seen = set()
    for m in re.finditer(r"uses:\s*(actions/[\w.-]+)@[0-9a-f]{40}\s+#\s*v(\d+)\.", text):
        name, major = m.group(1), int(m.group(2))
        assert name in MIN_NODE24_MAJOR, f"{name} 没登记最低大版本：先核对它的 runs.using 再加进表"
        assert major >= MIN_NODE24_MAJOR[name], f"{name} v{major} 低于 Node 24 的最低版本 v{MIN_NODE24_MAJOR[name]}"
        seen.add(name)
    assert seen == set(MIN_NODE24_MAJOR), seen


def test_setup_node_disables_auto_cache():
    """setup-node v5 起没给 cache 就自动开缓存；本仓不加缓存（check.yml 文件头），每处都要显式关掉"""
    steps = [s for job in WORKFLOW["jobs"].values() for s in job.get("steps", [])
             if str(s.get("uses", "")).startswith("actions/setup-node@")]
    assert steps, "没找到 setup-node，扫描写错了"
    for s in steps:
        assert s.get("with", {}).get("package-manager-cache") is False, s
        assert "cache" not in s.get("with", {}), s


def test_contracts_dev_deps_install_from_hashed_lock():
    """契约检查依赖（.venv-contracts）按带哈希的锁文件装（specs/ci-supply-chain-pins）：锁覆盖 requirements-dev.txt 的全部直接依赖、
    每个包都带哈希；CI 与 scripts/bootstrap-deps.sh 都用 --require-hashes 装这份锁，不再直接装 requirements-dev.txt"""
    req = (ROOT / "packages/contracts/requirements-dev.txt").read_text(encoding="utf-8")
    lock = (ROOT / "packages/contracts/requirements-dev.lock").read_text(encoding="utf-8")
    norm = lambda n: re.sub(r"[-_.]+", "-", n).lower()
    direct = {norm(m.group(1)) for m in re.finditer(r"^([A-Za-z0-9_.-]+)\s*(?:==|>=|<|~=)", req, re.M)}
    pinned: dict[str, int] = {}
    current = None
    for line in lock.splitlines():
        m = re.match(r"^([A-Za-z0-9_.-]+)==\S+", line)
        if m:
            current = norm(m.group(1))
            pinned[current] = 0
        elif current and re.search(r"--hash=sha256:[0-9a-f]{64}", line):
            pinned[current] += 1
    assert direct and direct <= set(pinned), f"锁文件缺直接依赖：{direct - set(pinned)}"
    assert all(n > 0 for n in pinned.values()), [k for k, n in pinned.items() if n == 0]
    ci = (ROOT / ".github/workflows/check.yml").read_text(encoding="utf-8")
    boot = (ROOT / "scripts/bootstrap-deps.sh").read_text(encoding="utf-8")
    assert ".venv-contracts/bin/pip install -q --require-hashes --only-binary=:all: -r packages/contracts/requirements-dev.lock" in ci
    assert "-r packages/contracts/requirements-dev.txt" not in ci
    assert 'local req="packages/contracts/requirements-dev.lock"' in boot and '"${PIP_LOCKED[@]}" -r "$req"' in boot
    assert "PIP_LOCKED=(--require-hashes --only-binary=:all: --no-binary=jieba)" in boot


def test_pip_installs_only_prebuilt_wheels():
    """按锁文件装 Python 依赖时一律只装预编译包（PR 28 合并前安全审查 N6）：缺预编译包时 pip 会退回构建源码包，构建依赖不校验哈希。
    只有 jieba（只发布源码包）单独放开，且只在用到它的知识服务与安装脚本里"""
    texts = {p: p.read_text(encoding="utf-8") for p in [ROOT / ".github/workflows/check.yml", ROOT / "scripts/bootstrap-deps.sh",
                                                          *sorted((ROOT / "deploy/docker").glob("*.Dockerfile")), ROOT / "services/learning/Dockerfile"]}
    seen = 0
    for p, t in texts.items():
        for line in t.splitlines():
            if "pip install" in line and "--require-hashes" in line:
                seen += 1
                assert "--only-binary=:all:" in line, f"{p.name}：{line.strip()}"
            if "--no-binary=" in line:
                assert "--no-binary=jieba" in line and (p.name in ("knowledge.Dockerfile", "bootstrap-deps.sh")), f"{p.name}：只许给 jieba 开例外"
    assert seen >= 7, seen
    boot = texts[ROOT / "scripts/bootstrap-deps.sh"]
    assert boot.count('"${PIP_LOCKED[@]}"') == 2, "安装脚本两处 pip install 都用 PIP_LOCKED"
