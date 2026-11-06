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
