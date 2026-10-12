"""时间表配置（config/schedule.synthetic.yaml）的加载与校验。

每个任务只声明：时间表表达式、身份（identity）、子命令与参数、可选的附加环境变量、任务级超时、是否启动时补跑。身份决定一切与权限有关的东西，
配置里改不了（spec 默认设计第 2 条“各自的连接配置，按任务声明的变量白名单传递，不合并成一个连接”）：
  身份       子进程（services/<服务>/.venv/bin/python -m <模块>）  运行环境变量 / 连接串变量                          可声明的附加变量
  scheduler  insight · insight.scheduled                         SCHEDULER_ENV / SCHEDULER_DATABASE_URL              （无）
  insight    insight · insight.jobs                              INSIGHT_ENV / INSIGHT_DATABASE_URL                  INSIGHT_POLICY、INSIGHT_WEEKLY_POLICY、INSIGHT_WEEKLY_CHECK
  learning   learning · learning.jobs                            LEARNING_ENV / LEARNING_DATABASE_URL                LEARNING_MASTERY_CONFIG_*
  kge        learning · review_console                           REVIEW_CONSOLE_ENV / REVIEW_CONSOLE_KGE_DATABASE_URL （无）
- 子命令按身份限定在本步范围内（判断生成 sync、中圈指标、admission-recompute、审核台 serve 等不在里面，spec“不做”）；
- 附加环境变量是每个身份的显式白名单：精确名，或明确登记的前缀（以 * 结尾声明）。不按“前缀 + 黑词”放行；
  名字以 _URL、_DSN、_DATABASE_URL 结尾的变量（连接串）只认该身份自己的连接串变量，通配匹配也一律不收这类名字；
- 子进程退出码的“拒绝”含义按身份区分：审核台命令行以 SystemExit(说明文字) 拒绝（退出码 1），其余命令行以 2 拒绝；
- catch_up：调度器启动时立即补跑一次（以子命令幂等兜底），只允许周报（weekly）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from .schedule import TZ_NAME, Schedule, ScheduleError, parse_schedule

ROOT = Path(__file__).resolve().parents[3]
SERVICES = ROOT / "services"
DEFAULT_SCHEDULE = Path(__file__).resolve().parents[1] / "config" / "schedule.synthetic.yaml"
SCHEMA_VERSION = "starbuddy-schedule/0.1"
DEFAULT_TIMEOUT = 600          # 任务级超时默认 10 分钟（spec 默认设计第 5 条）
MAX_TIMEOUT = 3600
URL_SUFFIXES = ("_URL", "_DSN", "_DATABASE_URL")
FORBIDDEN_ENV_WORDS = ("TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "PRIVATE", "APIKEY", "API_KEY")
CATCH_UP_COMMANDS = frozenset({"weekly"})
_JOB_NAME = re.compile(r"[a-z][a-z0-9-]{1,40}")
_ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]{1,80}")
_ARG = re.compile(r"[A-Za-z0-9_.:=+/-]{1,64}")


class ConfigError(ValueError):
    """时间表配置不合法：拒绝启动"""


@dataclass(frozen=True)
class Identity:
    name: str
    service: str
    module: str
    env_var: str
    url_var: str
    commands: frozenset[str]
    extra_names: frozenset[str] = frozenset()       # 可声明的附加变量（精确名）
    extra_prefixes: tuple[str, ...] = ()             # 可声明的附加变量前缀（声明时写 <前缀>*）
    refused_exits: frozenset[int] = frozenset({2})   # 子进程表示“拒绝”的退出码


IDENTITIES: Mapping[str, Identity] = {
    "scheduler": Identity("scheduler", "insight", "insight.scheduled", "SCHEDULER_ENV", "SCHEDULER_DATABASE_URL",
                          frozenset({"meter-expire", "meter-renew", "erasures", "freeze", "spot-check"})),
    "insight": Identity("insight", "insight", "insight.jobs", "INSIGHT_ENV", "INSIGHT_DATABASE_URL", frozenset({"sweep", "weekly"}),
                        extra_names=frozenset({"INSIGHT_POLICY", "INSIGHT_WEEKLY_POLICY", "INSIGHT_WEEKLY_CHECK"})),
    "learning": Identity("learning", "learning", "learning.jobs", "LEARNING_ENV", "LEARNING_DATABASE_URL", frozenset({"mastery-sweep"}),
                         extra_prefixes=("LEARNING_MASTERY_CONFIG_",)),
    # review_console 的拒绝路径是 raise SystemExit("说明")（退出码 1）与库拒绝（退出码 2），两者都按“拒绝”记
    "kge": Identity("kge", "learning", "review_console", "REVIEW_CONSOLE_ENV", "REVIEW_CONSOLE_KGE_DATABASE_URL",
                    frozenset({"expire-affairs"}), refused_exits=frozenset({1, 2})),
}


@dataclass(frozen=True)
class Job:
    name: str
    schedule: Schedule
    identity: Identity
    module: str                       # 恒等于 identity.module（测试可以换成假的子进程模块）
    args: tuple[str, ...]
    extra_env: tuple[str, ...] = ()
    timeout_seconds: float = DEFAULT_TIMEOUT
    catch_up: bool = False

    @property
    def required_env(self) -> tuple[str, str]:
        return self.identity.env_var, self.identity.url_var


@dataclass(frozen=True)
class ScheduleConfig:
    synthetic: bool
    signed: bool
    jobs: Mapping[str, Job]


def _need(ok: bool, msg: str) -> None:
    if not ok:
        raise ConfigError(msg)


def is_url_name(name: str) -> bool:
    return name.endswith(URL_SUFFIXES)


def env_name_ok(identity: Identity, name: str) -> bool:
    """声明的附加变量：精确名在该身份的白名单里，或 <登记的前缀>*；连接串类名字一律不收"""
    if name.endswith("*"):
        return name[:-1] in identity.extra_prefixes
    return name in identity.extra_names and not is_url_name(name)


def matched_name_ok(identity: Identity, prefix: str, name: str) -> bool:
    """通配匹配到的实际变量名：属于登记的前缀、名字合法、不是连接串类、不含凭据字样"""
    return (prefix in identity.extra_prefixes and name.startswith(prefix) and len(name) > len(prefix)
            and bool(_ENV_NAME.fullmatch(name)) and not is_url_name(name) and not any(w in name for w in FORBIDDEN_ENV_WORDS))


def parse_job(name: str, doc: Any) -> Job:
    _need(bool(_JOB_NAME.fullmatch(name)), f"任务名不合法：{name!r}")
    _need(isinstance(doc, dict) and set(doc) <= {"schedule", "identity", "args", "env", "timeout_seconds", "catch_up", "$comment"}
          and {"schedule", "identity", "args"} <= set(doc),
          f"任务 {name} 的配置位不对（schedule、identity、args 必填；env、timeout_seconds、catch_up 可选）")
    try:
        sched = parse_schedule(doc["schedule"])
    except ScheduleError as e:
        raise ConfigError(f"任务 {name}：{e}") from None
    ident = IDENTITIES.get(doc["identity"]) if isinstance(doc["identity"], str) else None
    _need(ident is not None, f"任务 {name} 的 identity 必须是 {sorted(IDENTITIES)} 之一")
    assert ident is not None
    args = doc["args"]
    _need(isinstance(args, list) and 1 <= len(args) <= 32 and all(isinstance(a, str) and _ARG.fullmatch(a) for a in args),
          f"任务 {name} 的 args 必须是 1–32 个简单字符串")
    _need(args[0] in ident.commands, f"任务 {name}：身份 {ident.name} 只能调用 {sorted(ident.commands)}")
    env = doc.get("env", [])
    _need(isinstance(env, list) and all(isinstance(e, str) and env_name_ok(ident, e) for e in env),
          f"任务 {name} 的 env 只能是身份 {ident.name} 白名单里的变量名")
    timeout = doc.get("timeout_seconds", DEFAULT_TIMEOUT)
    _need(isinstance(timeout, int) and not isinstance(timeout, bool) and 1 <= timeout <= MAX_TIMEOUT,
          f"任务 {name} 的 timeout_seconds 必须是 1–{MAX_TIMEOUT} 的整数")
    catch_up = doc.get("catch_up", False)
    _need(isinstance(catch_up, bool), f"任务 {name} 的 catch_up 必须是布尔值")
    _need(not catch_up or args[0] in CATCH_UP_COMMANDS, f"任务 {name}：只有周报（weekly）可以启动时补跑")
    return Job(name, sched, ident, ident.module, tuple(args), tuple(dict.fromkeys(env)), timeout, catch_up)


def parse_config(doc: Any) -> ScheduleConfig:
    keys = {"schema_version", "synthetic", "signed_by", "signed_at", "timezone", "jobs"}
    _need(isinstance(doc, dict) and set(doc) - {"$comment"} == keys, f"时间表配置的配置位不对（应为 {sorted(keys)}）")
    _need(doc["schema_version"] == SCHEMA_VERSION, f"schema_version 必须是 {SCHEMA_VERSION}")
    _need(doc["timezone"] == TZ_NAME, f"timezone 必须是 {TZ_NAME}")
    _need(isinstance(doc["synthetic"], bool), "synthetic 必须是布尔值")
    _need(isinstance(doc["signed_by"], str) and isinstance(doc["signed_at"], str), "signed_by / signed_at 必须是字符串")
    jobs = doc["jobs"]
    _need(isinstance(jobs, dict) and 1 <= len(jobs) <= 64, "jobs 必须是 1–64 个任务")
    parsed = {str(k): parse_job(str(k), v) for k, v in jobs.items()}
    signed = bool(doc["signed_by"].strip() and doc["signed_at"].strip()) and not doc["synthetic"]
    return ScheduleConfig(doc["synthetic"], signed, parsed)


def load_config(path: str | Path = DEFAULT_SCHEDULE) -> ScheduleConfig:
    try:
        with open(path, encoding="utf-8") as f:
            doc = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as e:
        raise ConfigError(f"时间表配置读不了：{type(e).__name__}") from None
    return parse_config(doc)
