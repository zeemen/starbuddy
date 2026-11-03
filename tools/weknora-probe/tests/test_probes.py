"""探针对模拟 WeKnora 的测试：如实模拟时各项按预期判定；打开缺陷开关时对应探针必须判失败（证明探针能发现问题）。
全部是合成数据，不联网（模拟服务只监听 127.0.0.1）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from weknora_probe import __main__ as cli
from weknora_probe.client import ConfigError, WeKnoraClient, WeKnoraError
from weknora_probe.fake import Defects, FakeWeKnora, chunk
from weknora_probe.probes import PROBES, V07_BODY, V16_FORMULA, V16_TABLE, ProbeContext, canary_scan_sql, run

ADMIN, READ = "sbtest-ingest-key", "sbtest-retrieve-key"


@pytest.fixture
def fake():
    with FakeWeKnora() as fk:
        fk.state.kbs.update({"kb-test", "kb-other", "kb-p1", "kb-p2"})
        fk.add_key(ADMIN, {"ingest", "retrieve"}, {"kb-test", "kb-p1", "kb-p2"})
        fk.add_key(READ, {"retrieve"}, {"kb-test"})
        yield fk


def ctx_for(fk: FakeWeKnora, **kw) -> ProbeContext:
    c = WeKnoraClient(fk.base_url, ADMIN, timeout=5)
    return ProbeContext(c, "kb-test", run_id="t0000001", retrieve_client=c.with_key(READ), other_kb_id="kb-other",
                        partition_kb_ids=("kb-p1", "kb-p2"), index_timeout=0.2, poll_interval=0.01, sleep=lambda _: None,
                        allowlist_sizes=(10, 100), **kw)


def by_id(results):
    return {r.id: r for r in results}


def test_faithful_fake_all_probes(fake):
    results, cleanup = run(ctx_for(fake))
    r = by_id(results)
    assert set(r) == set(PROBES)
    assert {k: v.status for k, v in r.items()} == {
        "V03": "pass", "V07": "pass", "V08": "pass", "V09": "pass", "V11": "pass", "V16": "pass", "V17": "manual", "V18": "pass"}
    v07 = r["V07"].evidence
    assert v07["multi_tag_semantics"] == "or"                       # 文档说的“标签之间是或”，探针能测出来
    assert v07["strategies"]["allowlist"]["cross_version_hits"] == 0
    assert v07["strategies"]["combo_tag"]["cross_version_hits"] == 0
    assert v07["strategies"]["kb_partition"]["queries"] == 2
    assert all(s["accepted"] and s["isolated"] for s in v07["allowlist_sizes"])
    assert r["V08"].evidence["fields_21_rejected"] and r["V08"].evidence["metadata_in_search_results"]
    assert r["V09"].evidence["duplicate_create"] == "accepted_new_id" and r["V09"].evidence["get_after_delete_status"] == 404
    assert r["V03"].evidence["retrieve_key_write_status"] == 403 and r["V03"].evidence["retrieve_key_other_kb_status"] == 403
    # 清理：本次写入的合成文档与标签全部删除
    assert cleanup["cleanup_failures"] == 0 and not fake.state.docs and not fake.state.tags


@pytest.mark.parametrize("defect,probe", [
    (Defects(ignore_filters=True), "V07"),
    (Defects(any_key=True), "V03"),
    (Defects(stale_replace=True), "V09"),
    (Defects(keep_deleted=True), "V09"),
    (Defects(mutate_on_search=True), "V18"),
    (Defects(split_long=30), "V16"),
])
def test_each_defect_is_detected(fake, defect, probe):
    fake.state.defects = defect
    results, _ = run(ctx_for(fake), [probe])
    assert results[0].status == "fail", results[0]


def test_v03_conditional_without_readonly_key(fake):
    c = ctx_for(fake)
    c.retrieve_client = None
    assert run(c, ["V03"])[0][0].status == "conditional"


def test_v07_without_partitions_still_judges_other_strategies(fake):
    c = ctx_for(fake)
    c.partition_kb_ids = ()
    res = run(c, ["V07"])[0][0]
    assert res.status == "pass" and "skipped" in res.evidence["strategies"]["kb_partition"]


def test_keep_leaves_synthetic_docs(fake):
    c = ctx_for(fake, keep=True)
    _, cleanup = run(c, ["V11"])
    assert cleanup == {"kept_docs": 1, "kept_tags": 0} and len(fake.state.docs) == 1
    assert all(d.title.startswith("sbprobe-t0000001-") for d in fake.state.docs.values())


def test_fake_chunker_keeps_formula_and_table_blocks():
    parts = chunk("甲\n\n" + V16_FORMULA + "\n\n" + V16_TABLE + "\n\n乙")
    assert V16_FORMULA in parts and V16_TABLE in parts and parts[0] == "甲" and parts[-1] == "乙"


# ---------- 命令行与报告 ----------
def env_for(fk: FakeWeKnora, **extra: str) -> dict[str, str]:
    e = {"WEKNORA_TARGET": "test", "WEKNORA_BASE_URL": fk.base_url, "WEKNORA_API_KEY": ADMIN, "WEKNORA_KB_ID": "kb-test",
         "WEKNORA_INDEX_TIMEOUT": "0.2"}
    e.update(extra)
    return e


def test_cli_refuses_non_test_target(fake, tmp_path, capsys):
    env = env_for(fake)
    env["WEKNORA_TARGET"] = "prod"
    assert cli.main(["--out", str(tmp_path)], env) == 2
    assert "WEKNORA_TARGET=test" in capsys.readouterr().err and not any(tmp_path.iterdir())
    env = env_for(fake)
    del env["WEKNORA_KB_ID"]
    assert cli.main(["--out", str(tmp_path)], env) == 2
    assert cli.main(["--out", str(tmp_path), "--probes", "V99"], env_for(fake)) == 2


def test_cli_report_has_no_secrets_or_text(fake, tmp_path):
    env = env_for(fake, WEKNORA_RETRIEVE_API_KEY=READ, WEKNORA_OTHER_KB_ID="kb-other", WEKNORA_PARTITION_KB_IDS="kb-p1,kb-p2")
    assert cli.main(["--out", str(tmp_path)], env) == 0
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["v17-canary-scan.sql", "weknora-probe.json", "weknora-probe.md"]
    blob = (tmp_path / "weknora-probe.md").read_text(encoding="utf-8") + (tmp_path / "weknora-probe.json").read_text(encoding="utf-8")
    for secret in (ADMIN, READ, fake.base_url, "127.0.0.1"):
        assert secret not in blob
    assert V07_BODY.split("。")[1] not in blob and V16_TABLE not in blob   # 正文不进报告
    rep = json.loads((tmp_path / "weknora-probe.json").read_text(encoding="utf-8"))
    assert rep["format"] == "weknora-probe-report/1" and rep["stop_for_user"] == [] and rep["counts"]["manual"] == 1


def test_cli_exit_1_and_stop_flag_on_key_failure(fake, tmp_path, capsys):
    fake.state.defects = Defects(ignore_filters=True)
    assert cli.main(["--out", str(tmp_path), "--probes", "V07"], env_for(fake)) == 1
    rep = json.loads((tmp_path / "weknora-probe.json").read_text(encoding="utf-8"))
    assert rep["stop_for_user"] == ["V07"] and "先停下" in capsys.readouterr().err
    assert "关键项不通过" in (tmp_path / "weknora-probe.md").read_text(encoding="utf-8")


def test_canary_scan_sql():
    sql = canary_scan_sql("sbcanaryab12")
    assert "'%sbcanaryab12%'" in sql and "information_schema.columns" in sql
    assert "UPDATE" not in sql.upper().replace("UPDATED", "") and "DELETE" not in sql.upper()
    with pytest.raises(ValueError):
        canary_scan_sql("x'; DROP TABLE t; --")


# ---------- 客户端 ----------
def test_client_config_and_secrecy(fake):
    with pytest.raises(ConfigError):
        WeKnoraClient.from_env({"WEKNORA_BASE_URL": fake.base_url, "WEKNORA_API_KEY": ADMIN})
    with pytest.raises(ConfigError):
        WeKnoraClient.from_env({"WEKNORA_TARGET": "test", "WEKNORA_BASE_URL": "ftp://x", "WEKNORA_API_KEY": ADMIN})
    c = WeKnoraClient.from_env(env_for(fake))
    assert ADMIN not in repr(c)
    with pytest.raises(WeKnoraError) as ei:
        c.with_key("wrong").knowledge_search("查询原文不进异常", kb_ids=["kb-test"])
    assert ei.value.status == 401 and "查询原文" not in str(ei.value) and "wrong" not in str(ei.value)
    with pytest.raises(WeKnoraError) as ei:
        WeKnoraClient("http://127.0.0.1:9", ADMIN, timeout=1).knowledge_search("x")
    assert ei.value.status is None


def test_readme_lists_every_probe():
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    for pid in PROBES:
        assert f"| {pid} |" in readme
