"""GitHub Actions 全量检查（.github/workflows/check.yml）与 Makefile 分组的一致性。

CI 把 make check 拆成并行任务：check-core 按 check-group-* 分 10 组，db-mutation 按 DBMUT_SHARD 分片。
这里核对拆分没有漏掉任何检查：
- CI 矩阵里的组与 Makefile 里定义的 check-group-* 一一对应；
- check-core 恰好由各组的 CHECK_GROUP_* 变量组成，check = check-core + db-mutation；
- 各组之间没有重复的检查目标；
- db-mutation 的分片编号是 1…N 连续，DBMUT_SHARD 的分母就是 N。"""
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


def group_vars() -> dict[str, list[str]]:
    return {v: deps.split() for v, deps in re.findall(r"^(CHECK_GROUP_[A-Z0-9_]+) := (.*)$", MAKEFILE, re.M)}


def test_matrix_groups_match_makefile_groups():
    matrix = WORKFLOW["jobs"]["make-check"]["strategy"]["matrix"]["group"]
    defined = re.findall(r"^check-group-([a-z0-9-]+):", MAKEFILE, re.M)
    assert len(matrix) == len(set(matrix))
    assert sorted(matrix) == sorted(defined)
    run = " ".join(step.get("run", "") for step in WORKFLOW["jobs"]["make-check"]["steps"])
    assert "make check-group-${{ matrix.group }} CONTRACT_STRICT=1" in run


def test_check_core_is_exactly_the_union_of_groups():
    gv = group_vars()
    assert len(gv) == len(re.findall(r"^check-group-", MAKEFILE, re.M))
    assert rule("check-core") == [f"$({v})" for v in gv]
    for name in re.findall(r"^check-group-([a-z0-9-]+):", MAKEFILE, re.M):
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


def test_every_job_fails_independently():
    for name in ("make-check", "db-mutation"):
        assert WORKFLOW["jobs"][name]["strategy"]["fail-fast"] is False
