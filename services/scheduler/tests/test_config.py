"""时间表配置的加载与校验：缺省配置与 spec 默认设计第 3 条逐项一致；身份决定子进程模块、变量前缀与可调用的子命令，配置改不了。"""
from __future__ import annotations

import copy

import pytest
import yaml

from scheduler.config import (DEFAULT_SCHEDULE, IDENTITIES, ConfigError, env_name_ok, is_url_name, load_config, matched_name_ok,
                              parse_config)

DOC = yaml.safe_load(DEFAULT_SCHEDULE.read_text(encoding="utf-8"))


def test_default_schedule_matches_spec():
    cfg = load_config()
    assert cfg.synthetic is True and cfg.signed is False
    got = {n: (j.schedule.text, j.identity.name, j.module, j.args[0]) for n, j in cfg.jobs.items()}
    assert got == {
        "meter-expire": ("every 5m", "scheduler", "insight.scheduled", "meter-expire"),
        "meter-renew": ("hourly :07", "scheduler", "insight.scheduled", "meter-renew"),
        "erasures": ("hourly :17", "scheduler", "insight.scheduled", "erasures"),
        "freeze": ("hourly :01", "scheduler", "insight.scheduled", "freeze"),
        "sweep": ("daily 02:30", "insight", "insight.jobs", "sweep"),
        "mastery-sweep": ("daily 02:30", "learning", "learning.jobs", "mastery-sweep"),
        "expire-affairs": ("daily 02:30", "kge", "review_console", "expire-affairs"),
        "spot-check": ("daily 02:30", "scheduler", "insight.scheduled", "spot-check"),
        "metrics": ("daily 03:20", "insight", "insight.jobs", "metrics"),
        "weekly": ("weekly mon 03:00", "insight", "insight.jobs", "weekly"),
        "model-call-purge": ("daily 04:10", "scheduler", "insight.scheduled", "model-call-purge"),
        "partition-ensure": ("daily 03:10", "scheduler", "insight.scheduled", "partition-ensure"),
        "retention-purge": ("daily 03:40", "scheduler", "insight.scheduled", "retention-purge"),
    }
    # 分区续建与保留期清理（0062、0063，specs/plt23-data-maintenance）：调度器身份、不补跑；合成时间表里的默认值，签字后才在 staging 启用
    for name, args in (("partition-ensure", ("partition-ensure",)), ("retention-purge", ("retention-purge", "--batch", "1000"))):
        assert cfg.jobs[name].args == args and cfg.jobs[name].extra_env == () and cfg.jobs[name].catch_up is False
        assert "待人签字" in DOC["jobs"][name]["$comment"]
    assert "0062" in DOC["jobs"]["partition-ensure"]["$comment"] and "0063" in DOC["jobs"]["retention-purge"]["$comment"]
    # 模型调用记录留存删除（0060，specs/plt-model-call-log）：调度器身份、每批 1000、不补跑；合成时间表里的默认值，签字后才在 staging 启用
    p = cfg.jobs["model-call-purge"]
    assert p.args == ("model-call-purge", "--batch", "1000") and p.extra_env == () and p.catch_up is False
    assert "待人签字" in DOC["jobs"]["model-call-purge"]["$comment"] and "0060" in DOC["jobs"]["model-call-purge"]["$comment"]
    # 中圈指标静默重算（SIE-05）：insight 身份、全部学生、只带判断策略变量，不补跑
    m = cfg.jobs["metrics"]
    # 定时作业暂不重算学习持续性（与迁移 0025 的关系待人确认）：参数校验放行这组参数
    assert m.args == ("metrics", "--all", "--skip", "persistence") and m.extra_env == ("INSIGHT_POLICY",) and m.catch_up is False
    assert m.required_env == ("INSIGHT_ENV", "INSIGHT_DATABASE_URL")
    assert "待人确认" in DOC["jobs"]["metrics"]["$comment"] and "0025" in DOC["jobs"]["metrics"]["$comment"]
    assert all(j.timeout_seconds == 600 for j in cfg.jobs.values())  # 任务级超时默认 10 分钟
    assert cfg.jobs["mastery-sweep"].extra_env == ("LEARNING_MASTERY_CONFIG_*",)
    sc = cfg.jobs["spot-check"].args
    assert sc[sc.index("--ratio") + 1] == "0.1" and "echo" not in sc


def bad(mut) -> None:
    doc = copy.deepcopy(DOC)
    mut(doc)
    with pytest.raises(ConfigError):
        parse_config(doc)


@pytest.mark.parametrize("mut", [
    lambda d: d.update(timezone="UTC"),
    lambda d: d.update(schema_version="starbuddy-schedule/9"),
    lambda d: d.update(extra=1),
    lambda d: d.pop("signed_by"),
    lambda d: d.update(synthetic="yes"),
    lambda d: d.update(jobs={}),
    lambda d: d["jobs"].update({"Bad Name": d["jobs"]["sweep"]}),
    lambda d: d["jobs"]["sweep"].update(identity="root"),
    lambda d: d["jobs"]["sweep"].update(schedule="monthly 1 03:00"),
    lambda d: d["jobs"]["sweep"].update(args=["sync", "--all"]),                 # 判断生成 sync 不在本步范围
    lambda d: d["jobs"]["metrics"].update(args=["sync", "--all"]),               # 放行 metrics 不等于放行 sync
    lambda d: d["jobs"]["metrics"].update(args=["admission-recompute"]),
    lambda d: d["jobs"]["metrics"].update(identity="scheduler"),                 # 指标重算只授 insight 身份
    lambda d: d["jobs"]["model-call-purge"].update(identity="insight"),          # 调用记录留存删除只授调度器身份（0060）
    lambda d: d["jobs"]["model-call-purge"].update(identity="learning"),
    lambda d: d["jobs"]["model-call-purge"].update(catch_up=True),
    lambda d: d["jobs"]["model-call-purge"].update(args=["model-call-purge", "--batch", "1000; rm"]),
    lambda d: d["jobs"]["partition-ensure"].update(identity="insight"),          # 分区续建只授调度器身份（0062）
    lambda d: d["jobs"]["partition-ensure"].update(identity="kge"),
    lambda d: d["jobs"]["retention-purge"].update(identity="learning"),          # 保留期清理只授调度器身份（0063）
    lambda d: d["jobs"]["retention-purge"].update(catch_up=True),
    lambda d: d["jobs"]["retention-purge"].update(args=["retention-purge", "--batch", "1000 && x"]),
    lambda d: d["jobs"]["metrics"].update(identity="learning"),
    lambda d: d["jobs"]["metrics"].update(catch_up=True),                        # 只有周报可以启动补跑
    lambda d: d["jobs"]["metrics"].update(env=["INSIGHT_DATABASE_URL"]),
    lambda d: d["jobs"]["metrics"].update(args=["metrics", "--student", "x; rm"]),
    lambda d: d["jobs"]["metrics"].update(args=["metrics", "--all", "--skip", "persistence; rm"]),
    lambda d: d["jobs"]["expire-affairs"].update(args=["serve", "--port", "1"]), # 审核台后端不能由调度器起
    lambda d: d["jobs"]["mastery-sweep"].update(args=["admission-recompute"]),
    lambda d: d["jobs"]["meter-expire"].update(identity="insight"),              # 计量函数只授调度器身份：换身份即子命令不匹配
    lambda d: d["jobs"]["sweep"].update(args=["sweep", "--limit", "1; rm"]),
    lambda d: d["jobs"]["sweep"].update(args=[]),
    lambda d: d["jobs"]["sweep"].update(args=["sweep", 5]),
    lambda d: d["jobs"]["sweep"].update(env=["SCHEDULER_DATABASE_URL"]),          # 别的身份的连接串
    lambda d: d["jobs"]["sweep"].update(env=["INSIGHT_TOKEN_BFF"]),               # 凭据字样
    lambda d: d["jobs"]["sweep"].update(env=["INSIGHT_*"]),                       # 只有前缀的通配
    lambda d: d["jobs"]["sweep"].update(env=["PYTHONPATH"]),
    lambda d: d["jobs"]["sweep"].update(env=["INSIGHT_LEARNING_URL"]),            # 别的服务的地址
    lambda d: d["jobs"]["sweep"].update(env=["INSIGHT_EXTRA"]),                   # 带前缀但不在白名单
    lambda d: d["jobs"]["expire-affairs"].update(env=["REVIEW_CONSOLE_REVIEW_DATABASE_URL"]),
    lambda d: d["jobs"]["expire-affairs"].update(env=["REVIEW_CONSOLE_R*"]),
    lambda d: d["jobs"]["mastery-sweep"].update(env=["LEARNING_ADAPTER_*"]),
    lambda d: d["jobs"]["sweep"].update(timeout_seconds=0),
    lambda d: d["jobs"]["sweep"].update(timeout_seconds=7200),
    lambda d: d["jobs"]["sweep"].update(timeout_seconds=True),
    lambda d: d["jobs"]["sweep"].update(module="os"),                              # 模块由身份决定，配置里不能写
    lambda d: d["jobs"]["sweep"].pop("schedule"),
])
def test_rejects(mut):
    bad(mut)


def test_identity_table_is_fixed():
    assert {i.name: (i.service, i.module, i.env_var, i.url_var, i.extra_names, i.extra_prefixes, i.refused_exits)
            for i in IDENTITIES.values()} == {
        "scheduler": ("insight", "insight.scheduled", "SCHEDULER_ENV", "SCHEDULER_DATABASE_URL", frozenset(), (), frozenset({2})),
        "insight": ("insight", "insight.jobs", "INSIGHT_ENV", "INSIGHT_DATABASE_URL",
                    frozenset({"INSIGHT_POLICY", "INSIGHT_WEEKLY_POLICY", "INSIGHT_WEEKLY_CHECK"}), (), frozenset({2})),
        "learning": ("learning", "learning.jobs", "LEARNING_ENV", "LEARNING_DATABASE_URL", frozenset(), ("LEARNING_MASTERY_CONFIG_",),
                     frozenset({2})),
        "kge": ("learning", "review_console", "REVIEW_CONSOLE_ENV", "REVIEW_CONSOLE_KGE_DATABASE_URL", frozenset(), (), frozenset({1, 2})),
    }
    # 子命令按身份固定：insight 放行 sweep、weekly 与中圈指标 metrics（SIE-05）；sync、admission-recompute、serve 等仍不放行
    assert {i.name: i.commands for i in IDENTITIES.values()} == {
        "scheduler": frozenset({"meter-expire", "meter-renew", "erasures", "freeze", "spot-check", "model-call-purge",
                                "partition-ensure", "retention-purge"}),
        "insight": frozenset({"sweep", "weekly", "metrics"}),
        "learning": frozenset({"mastery-sweep"}),
        "kge": frozenset({"expire-affairs"}),
    }
    for i in IDENTITIES.values():                                  # 白名单里没有连接串类名字
        assert not any(is_url_name(n) for n in i.extra_names) and not any(is_url_name(p) for p in i.extra_prefixes)


@pytest.mark.parametrize("identity, name, ok", [
    ("learning", "LEARNING_MASTERY_CONFIG_*", True), ("learning", "LEARNING_*", False), ("learning", "LEARNING_ADAPTER_*", False),
    ("kge", "REVIEW_CONSOLE_R*", False), ("kge", "REVIEW_CONSOLE_REVIEW_DATABASE_URL", False), ("insight", "INSIGHT_LEARNING_URL", False),
    ("insight", "INSIGHT_POLICY", True), ("insight", "INSIGHT_DATABASE_URL", False), ("scheduler", "SCHEDULER_ENV", False),
])
def test_env_whitelist(identity, name, ok):
    assert env_name_ok(IDENTITIES[identity], name) is ok


@pytest.mark.parametrize("name, ok", [("LEARNING_MASTERY_CONFIG_AYA", True), ("LEARNING_MASTERY_CONFIG_DATABASE_URL", False),
                                      ("LEARNING_MASTERY_CONFIG_X_URL", False), ("LEARNING_MASTERY_CONFIG_X_DSN", False),
                                      ("LEARNING_MASTERY_CONFIG_SECRET", False), ("LEARNING_MASTERY_CONFIG_", False),
                                      ("LEARNING_ADAPTER_AYA", False), ("LEARNING_MASTERY_CONFIG_aya", False)])
def test_wildcard_matches(name, ok):
    assert matched_name_ok(IDENTITIES["learning"], "LEARNING_MASTERY_CONFIG_", name) is ok
    assert matched_name_ok(IDENTITIES["insight"], "LEARNING_MASTERY_CONFIG_", name) is False   # 别的身份没登记这个前缀


def test_catch_up_only_for_weekly():
    assert load_config().jobs["weekly"].catch_up is True
    bad(lambda d: d["jobs"]["sweep"].update(catch_up=True))
    bad(lambda d: d["jobs"]["meter-expire"].update(catch_up=True))
    bad(lambda d: d["jobs"]["weekly"].update(catch_up="yes"))


def test_signed_only_when_not_synthetic():
    doc = copy.deepcopy(DOC)
    doc.update(signed_by="someone", signed_at="2026-10-12")
    assert parse_config(doc).signed is False
    doc["synthetic"] = False
    assert parse_config(doc).signed is True
