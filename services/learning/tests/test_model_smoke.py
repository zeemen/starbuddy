"""scripts/model_smoke.py（LT-02 范围第 6 条：真实模型冒烟，人执行、限次、不进 make check）的测试。

不联网：拒绝路径不建连；“成功”路径用合成快照名与合成主机填好的 staging 路由表（写在 pytest 的临时目录），供应商换成进程内假供应商。
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest
import yaml

from learning.model_router import DEFAULT_PATH, FakeProvider, ModelRouter, ProviderError, ProviderReply, Usage

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import model_smoke  # noqa: E402

from test_model_router_staging import SYN_KEY, filled  # noqa: E402

REPLY = "合成回复-不应打印-3c9d"


def _filled_routes(tmp_path: Path, edit=None) -> Path:
    doc = filled()
    if edit:
        edit(doc)
    p = tmp_path / "routes.yaml"
    p.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return p


def _factory(behavior, calls):
    def make(cfg, sink):
        provs = {}
        for vid in {t.vendor for r in cfg.routes.values() for t in r.targets}:
            async def b(t, m, k, _vid=vid):
                calls.append((_vid, t.api_name, len(cfg.routes), next(iter(cfg.routes.values())).max_retries))
                return await behavior(t, m, k)
            provs[vid] = FakeProvider(cfg.vendors[vid], b)
        return ModelRouter(cfg, provs, sink=sink)
    return make


async def _ok(t, m, k):
    return ProviderReply(REPLY, Usage(4, 1), t.api_name)


@pytest.mark.parametrize("env", ["pilot", "prod", "test", "production"])
def test_refuses_other_envs_before_reading_anything(env):
    lines: list[str] = []

    def boom(*a, **k):
        raise AssertionError("不应建连")

    assert model_smoke.run(env, max_calls=5, factory=boom, out=lines.append) == 2
    assert "拒绝运行" in lines[-1] and "pilot、prod 拒绝" in lines[-1]


def test_refuses_repo_staging_with_placeholders():
    lines: list[str] = []
    assert model_smoke.run("staging", max_calls=5, routes_path=DEFAULT_PATH, environ={}, out=lines.append) == 2
    assert "占位" in lines[-1]


@pytest.mark.parametrize("max_calls,needle", [(None, "必须显式给 --max-calls"), (0, "应在"), (13, "应在"), (4, "超过 --max-calls 4")])
def test_call_limit(tmp_path, max_calls, needle):
    lines: list[str] = []
    calls: list = []
    assert model_smoke.run("staging", max_calls=max_calls, routes_path=_filled_routes(tmp_path), factory=_factory(_ok, calls),
                           out=lines.append) == 2
    assert needle in lines[-1] and calls == []


def test_one_call_per_distinct_target_without_retries_and_no_reply_text(tmp_path):
    lines: list[str] = []
    calls: list = []
    rc = model_smoke.run("staging", max_calls=5, routes_path=_filled_routes(tmp_path), factory=_factory(_ok, calls), out=lines.append)
    assert rc == 0, lines
    # staging 的目标：qwen3.8-flash、doubao-2.1-turbo、qwen3.8-max、deepseek-v4.1-flash、doubao-2.1-pro（T8 的两个与前面重复）
    assert sorted(c[1] for c in calls) == sorted(f"{m}-2026-09-01" for m in
                                                 ("qwen3.8-flash", "doubao-2.1-turbo", "qwen3.8-max", "deepseek-v4.1-flash", "doubao-2.1-pro"))
    assert all(c[2] == 1 and c[3] == 0 for c in calls), "每次只含一个目标、不重试"
    text = "\n".join(lines)
    assert "预计调用 5 次" in text and "通过" in lines[-1]
    assert REPLY not in text and SYN_KEY not in text


def test_failures_and_mismatch_exit_1(tmp_path):
    async def flaky(t, m, k):
        if t.vendor == "ark":
            raise ProviderError("auth")
        return ProviderReply(REPLY, Usage(1, 1), "some-other-snapshot")

    lines: list[str] = []
    rc = model_smoke.run("staging", max_calls=5, routes_path=_filled_routes(tmp_path), factory=_factory(flaky, []), out=lines.append)
    text = "\n".join(lines)
    assert rc == 1 and "失败（auth）" in text and "不一致" in text and "5/5" in lines[-1]


def test_default_factory_reads_env_and_reports_not_ready(tmp_path):
    """没给地址与 Key：每个目标报“未就绪”（不联网），错误里不带地址"""
    lines: list[str] = []
    rc = model_smoke.run("staging", max_calls=5, routes_path=_filled_routes(tmp_path), environ={}, out=lines.append)
    assert rc == 1 and sum("未就绪" in x for x in lines) == 5


def test_unapproved_or_foreign_staging_is_refused(tmp_path):
    def pending(doc):
        doc["environments"]["staging"]["vendors"]["ark"]["whitelist"] = "pending"

    lines: list[str] = []
    assert model_smoke.run("staging", max_calls=5, routes_path=_filled_routes(tmp_path, pending), factory=_factory(_ok, []),
                           out=lines.append) == 2
    assert "已批准" in lines[-1]
    doc = copy.deepcopy(filled())
    assert model_smoke.plan(doc, "staging")[1], "填好、已批准时有目标可冒烟"
