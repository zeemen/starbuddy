"""PLT-17 成本仪表盘第一步（specs/cost-dashboard-plt17/spec.md）：离线汇总消耗聚合行与模型调用记录，只出聚合。

成本口径不另立，复用 cost.py：额度（load_quotas，迁移 0015 草稿）、价格表与输入核对（validate）、token 来源（token_stats /
lookup_tokens）、单次调用成本（per_call_cost）、“资源 → 任务 × 每单位调用次数”的月成本（monthly_cost，取 cost_profile.yaml 的各科
activities；未归因消耗的计价见 config/cost_dashboard.yaml）。分位数按安全审查 B1 改为相邻两秩线性插值后再粗化（不再用最近秩，
最近秩公开的是某一名学生的精确值）。

输入（本步只收合成样例；真实导出另起，RUNBOOK 第 4 节）：
- 消耗聚合行（CSV，首行元数据）：
    # format=cost-dashboard-usage/1 synthetic=true quota_config=v12-pilot@1
    pseudonym,month,tier,subject,resource,quantity
  一行 = 伪名 × 计费周期（month 列为周期开始的月份）× 科目（或 unattributed）× 资源的结算量；同一伪名在全部月份里档位必须一致。
  守卫同 tools/kge-calibrate（正则逐字复制，测试核对）：synthetic=true 才收、合成伪名只能 syn- 开头、拒绝个人信息列名与单元格、
  只收普通文件、大小与行数上限；错误信息只给行号与原因，不回显输入内容。
- 模型调用记录（JSON Lines）：沿用 loadtest 的 call-records 格式（科目、任务、结果、token、尝试次数）。可另带 fallback（布尔，成功那次是
  不是备选供应商；真实日志里目前没有这个字段，见 RUNBOOK 第 4 节）；tier 为与 cost-report 共享格式的保留字段，本工具只校验、不使用。
  拒绝任何模型标识、关联摘要与个人字段。

防回推（安全审查 B1，对齐 specs/kge09-map-calibration 的先例；表键扰动不是差分隐私）：
- 每名学生只算一个点：多月输入先按人取月均，门槛按人数计；
- 组人数加由成员集合决定的确定性扰动（−2…+2，同一成员集合的各组共用同一份扰动，防多表平均还原人数）；扰动后 ≥ min_group + 2
  才公开（保证真实 ≥ min_group），否则只写“样本不足”；公开的人数是扰动后人数向下取整到 round_to；
- P50 / P90 为相邻两秩线性插值，再粗化：消耗到额度的 5% 一档（至少 1）、使用率到 5%、成本到 0.1 元；
  P90 以上（严格大于插值 P90）的人数 < 5 时 P90 只写“样本不足”；
- 调用记录按任务聚合：调用数 < 20 只写“样本不足”，调用数取整到 10。
输出不含伪名（出口检查）；写出时目录须不存在或为空，文件以 0600、O_EXCL、O_NOFOLLOW 新建。
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import stat
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from .cost import (CONFIG, SOURCE_ASSUMED, TIER_LABELS, TIERS, TokenStat, load_quotas, load_yaml, lookup_tokens, monthly_cost,
                   per_call_cost, token_stats, validate)

DASH_CONFIG = CONFIG / "cost_dashboard.yaml"
UNATTR = "unattributed"
ALL = "all"
INSUFFICIENT = "样本不足"
ZERO_QUOTA = "额度为 0"
NOT_AVAILABLE = "记录不可得"
NO_PRICE = "无价格"
D5 = "D5 待产品确认"
MIN_GROUP_FLOOR = 20
ROUND_TO_FLOOR = 10
MIN_CALLS = 20
MIN_TAIL = 5
STEP_COST = 0.1
STEP_UTIL = 0.05
STEP_QUOTA_SHARE = 0.05
STEP_SHARE = 0.01
NOISE_TABLE = ((-2, 0.1), (-1, 0.3), (0, 0.7), (1, 0.9), (2, 1.0))
NOISE_MAX = 2
KEY_MOD = 1 << 64

MAX_USAGE_BYTES = 64 * 1024 * 1024
MAX_USAGE_ROWS = 2_000_000
MAX_RECORDS_BYTES = 256 * 1024 * 1024
MAX_RECORDS = 5_000_000
FILE_MODE = 0o600
DIR_MODE = 0o700

USAGE_FORMAT = "cost-dashboard-usage/1"
META_KEYS = ("format", "synthetic", "quota_config")
USAGE_COLUMNS = ("pseudonym", "month", "tier", "subject", "resource", "quantity")
MONTH = re.compile(r"^20\d{2}-(0[1-9]|1[0-2])$")
QUANTITY = re.compile(r"^\d{1,15}$")
SIGNOFF = re.compile(r"^[A-Za-z0-9._@-]{3,64}$")

# ---- 与 tools/kge-calibrate/kge_calibrate/inputs.py 逐字相同（测试核对） ----
FORBIDDEN_COLUMN = re.compile(
    r"name|姓名|名字|school|学校|class|班级|grade_?level|年级|phone|mobile|tel|手机|电话|e-?mail|邮箱|address|住址|地址"
    r"|id_?card|identity|身份证|证件|birth|生日|出生|guardian|parent|家长|监护|wechat|微信|qq|student_?id|user_?id|account|账号"
    r"|gender|sex|性别|region|district|city|地区|区县|城市",
    re.IGNORECASE)
PSEUDONYM = re.compile(r"^(syn|pil)-[a-z]{10,24}$")
SYNTHETIC_PREFIX = "syn-"
PERSONAL = (re.compile(r"1[3-9]\d{9}"), re.compile(r"\d{17}[\dXx]"), re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+\.[\w.]+"),
            re.compile(r"[㐀-鿿]"))
# ---- 以上 ----
PSEUDONYM_ANYWHERE = re.compile(r"(syn|pil)-[a-z]{10,24}")

# 调用记录：只收这些键（stack.call_records 的输出 + 可选 fallback；tier 为与 cost-report 共享格式的保留字段，不使用）
RECORD_KEYS = ("subject", "task", "data_class", "outcome", "input_tokens", "output_tokens", "elapsed_ms", "attempts", "fallback", "tier")
RECORD_REQUIRED = ("task", "outcome", "input_tokens", "output_tokens")
MODEL_KEY = re.compile(r"model|vendor|provider|usage_?ref|idempotency|source_version|api_name", re.IGNORECASE)
OUTCOMES = ("ok", "unavailable", "rejected", "error")
TASK = re.compile(r"^T\d{1,2}$")
DATA_CLASS = re.compile(r"^L\d$")


class InputRefused(ValueError):
    """输入不合格或疑似含个人信息：拒绝运行（消息只含行号、字段位置与原因）"""


class OutputRefused(ValueError):
    """输出目录不合适：拒绝写出"""


def _need(ok: bool, msg: str) -> None:
    if not ok:
        raise InputRefused(msg)


def read_limited(path: str | Path, limit: int, what: str) -> bytes:
    """先 stat（只收普通文件、不超过上限），再最多读 limit + 1 字节"""
    try:
        st = os.stat(path)
    except OSError:
        raise InputRefused(f"{what}读不了") from None
    _need(stat.S_ISREG(st.st_mode), f"{what}必须是普通文件")
    _need(st.st_size <= limit, f"{what}超过大小上限")
    with open(path, "rb") as fh:
        data = fh.read(limit + 1)
    _need(len(data) <= limit, f"{what}超过大小上限")
    return data


def looks_personal(value: str) -> bool:
    t = re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))
    return any(r.search(t) for r in PERSONAL)


# ---------------- 配置 ----------------
def check_dash_config(doc: Any) -> dict:
    _need(isinstance(doc, dict), "仪表盘配置必须是对象")
    _need(set(doc) == {"min_group", "round_to", "unattributed"}, "仪表盘配置只收 min_group、round_to、unattributed")
    mg, rt = doc["min_group"], doc["round_to"]
    _need(isinstance(mg, int) and not isinstance(mg, bool) and MIN_GROUP_FLOOR <= mg <= 100_000,
          f"min_group 必须是不小于 {MIN_GROUP_FLOOR} 的整数")
    _need(isinstance(rt, int) and not isinstance(rt, bool) and ROUND_TO_FLOOR <= rt <= mg, f"round_to 必须是 {ROUND_TO_FLOOR}…min_group 的整数")
    u = doc["unattributed"]
    _need(isinstance(u, dict) and isinstance(u.get("label"), str) and isinstance(u.get("activities"), list), "unattributed 须有 label 与 activities")
    return doc


# ---------------- 消耗聚合行 ----------------
@dataclass(frozen=True)
class Usage:
    synthetic: bool
    quota_config: str
    months: tuple[str, ...]
    # 伪名 → (档, {科目: {资源: 月均数量}})；每名学生一个点
    students: Mapping[str, tuple[str, Mapping[str, Mapping[str, float]]]] = field(repr=False)

    @property
    def pseudonyms(self) -> frozenset[str]:
        return frozenset(self.students)


def _meta(line: str) -> dict[str, str]:
    _need(line.startswith("# "), f"消耗表第 1 行必须是元数据行（# format={USAGE_FORMAT} synthetic=true quota_config=…）")
    out: dict[str, str] = {}
    for tok in line[2:].split():
        k, sep, v = tok.partition("=")
        _need(sep == "=" and k in META_KEYS and k not in out, "消耗表元数据行有不认识或重复的键")
        out[k] = v
    _need(set(out) == set(META_KEYS), f"消耗表元数据行必须含 {META_KEYS}")
    _need(out["format"] == USAGE_FORMAT, f"消耗表 format 必须是 {USAGE_FORMAT}")
    _need(out["synthetic"] in ("true", "false"), "消耗表 synthetic 只能是 true / false")
    return out


def parse_usage(text: str, quotas: Mapping[str, Mapping[str, float]], quota_config: str, subjects: tuple[str, ...]) -> Usage:
    lines = text.split("\n", 1)
    meta = _meta(lines[0].rstrip("\r"))
    synthetic = meta["synthetic"] == "true"
    _need(synthetic, "本步只接受合成样例（synthetic=true）；试点导出的运行另起任务、由人确认（spec 范围 1）")
    _need(meta["quota_config"] == quota_config, "消耗表的额度配置与成本画像（cost_profile.yaml 的 quota_config）不一致")
    reader = csv.reader(io.StringIO(lines[1] if len(lines) > 1 else ""), strict=True)
    try:
        header = next(reader)
    except (StopIteration, csv.Error):
        raise InputRefused("消耗表缺表头") from None
    for i, col in enumerate(header):
        _need(not FORBIDDEN_COLUMN.search(col), f"消耗表第 {i + 1} 列疑似个人信息字段（姓名、学校、手机号、证件、住址、账号等一律拒绝）")
        _need(col in USAGE_COLUMNS, f"消耗表第 {i + 1} 列不认识（只收 {'、'.join(USAGE_COLUMNS)}）")
    _need(len(set(header)) == len(header), "消耗表列重复")
    _need(set(header) == set(USAGE_COLUMNS), f"消耗表必须有 {'、'.join(USAGE_COLUMNS)} 六列")
    idx = {c: header.index(c) for c in header}
    resources = set(next(iter(quotas.values())))
    allowed_subjects = set(subjects) | {UNATTR}

    tiers: dict[str, str] = {}
    months: dict[str, set[str]] = {}
    total: dict[str, dict[str, dict[str, float]]] = {}
    seen: set[tuple[str, str, str, str]] = set()
    rows = 0
    try:
        for row in reader:
            line_no = reader.line_num + 1
            if not row:
                continue
            rows += 1
            _need(rows <= MAX_USAGE_ROWS, "消耗表超过行数上限")
            _need(len(row) == len(header), f"消耗表第 {line_no} 行列数不对")
            for cell in row:
                _need(not looks_personal(cell), f"消耗表第 {line_no} 行疑似含个人信息（手机号、证件号、邮箱或汉字）")
            pid, month, tier = row[idx["pseudonym"]], row[idx["month"]], row[idx["tier"]]
            subject, res, qty = row[idx["subject"]], row[idx["resource"]], row[idx["quantity"]]
            _need(PSEUDONYM.fullmatch(pid) is not None, f"消耗表第 {line_no} 行伪名格式不对（syn- 或 pil- 加 10–24 个小写字母）")
            _need(not synthetic or pid.startswith(SYNTHETIC_PREFIX), f"消耗表第 {line_no} 行：合成样例的伪名只能是 syn- 开头")
            _need(MONTH.fullmatch(month) is not None, f"消耗表第 {line_no} 行月份格式不对（YYYY-MM）")
            _need(tier in TIERS, f"消耗表第 {line_no} 行档位不认识")
            _need(subject in allowed_subjects, f"消耗表第 {line_no} 行科目不认识（平台学科编码或 {UNATTR}）")
            _need(res in resources, f"消耗表第 {line_no} 行资源不在额度配置里")
            _need(QUANTITY.fullmatch(qty) is not None, f"消耗表第 {line_no} 行数量必须是非负整数")
            _need(tiers.setdefault(pid, tier) == tier, f"消耗表第 {line_no} 行：同一伪名的档位不一致（每名学生一个点，期内升降档未拆分）")
            _need((pid, month, subject, res) not in seen, f"消耗表第 {line_no} 行重复（同一伪名、月、科目、资源只能一行）")
            seen.add((pid, month, subject, res))
            months.setdefault(pid, set()).add(month)
            per = total.setdefault(pid, {}).setdefault(subject, {})
            per[res] = per.get(res, 0.0) + float(int(qty))
    except csv.Error:
        raise InputRefused("消耗表不是合法的 CSV") from None
    _need(rows > 0, "消耗表没有数据行")
    students = {}
    for pid in sorted(total):
        k = len(months[pid])   # 按人取月均：该生出现的月数
        students[pid] = (tiers[pid], {s: {r: v / k for r, v in sorted(rs.items())} for s, rs in sorted(total[pid].items())})
    all_months = tuple(sorted({m for ms in months.values() for m in ms}))
    return Usage(synthetic, meta["quota_config"], all_months, students)


def load_usage(path: str | Path, quotas: Mapping[str, Mapping[str, float]], quota_config: str, subjects: tuple[str, ...]) -> Usage:
    data = read_limited(path, MAX_USAGE_BYTES, "消耗表")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise InputRefused("消耗表不是 UTF-8") from None
    return parse_usage(text, quotas, quota_config, subjects)


# ---------------- 调用记录 ----------------
def _nonneg_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 10**12


def parse_records(text: str, subjects: tuple[str, ...]) -> list[dict]:
    out: list[dict] = []
    for i, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        _need(len(out) < MAX_RECORDS, "调用记录超过条数上限")
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            raise InputRefused(f"调用记录第 {i} 行不是合法的 JSON") from None
        _need(isinstance(r, dict), f"调用记录第 {i} 行必须是对象")
        for k in r:
            if k in RECORD_KEYS:   # 白名单键（data_class 含 “class” 字样，先放行）
                continue
            _need(not MODEL_KEY.search(k), f"调用记录第 {i} 行含模型标识或关联摘要字段（报告不写模型标识，导出不带关联摘要）")
            _need(not FORBIDDEN_COLUMN.search(k) and not re.search(r"pseudonym|student|household", k, re.IGNORECASE),
                  f"调用记录第 {i} 行含疑似个人信息字段")
            raise InputRefused(f"调用记录第 {i} 行有不认识的字段（只收 {'、'.join(RECORD_KEYS)}）")
        _need(all(k in r for k in RECORD_REQUIRED), f"调用记录第 {i} 行缺字段（{'、'.join(RECORD_REQUIRED)} 必有）")
        for v in r.values():
            _need(not (isinstance(v, str) and looks_personal(v)), f"调用记录第 {i} 行疑似含个人信息")
        _need(r.get("subject") is None or r["subject"] in subjects, f"调用记录第 {i} 行科目不认识（平台学科编码或空）")
        _need(isinstance(r["task"], str) and TASK.fullmatch(r["task"]) is not None, f"调用记录第 {i} 行任务编码不对（T 加数字）")
        _need(r["outcome"] in OUTCOMES, f"调用记录第 {i} 行结果不认识")
        _need(_nonneg_int(r["input_tokens"]) and _nonneg_int(r["output_tokens"]), f"调用记录第 {i} 行 token 数必须是非负整数")
        _need("attempts" not in r or _nonneg_int(r["attempts"]), f"调用记录第 {i} 行尝试次数必须是非负整数")
        _need("elapsed_ms" not in r or _nonneg_int(r["elapsed_ms"]), f"调用记录第 {i} 行耗时必须是非负整数")
        _need("fallback" not in r or isinstance(r["fallback"], bool), f"调用记录第 {i} 行 fallback 必须是布尔")
        _need("tier" not in r or r["tier"] in TIERS, f"调用记录第 {i} 行档位不认识（保留字段，本工具不使用）")
        _need("data_class" not in r or (isinstance(r["data_class"], str) and DATA_CLASS.fullmatch(r["data_class"]) is not None),
              f"调用记录第 {i} 行数据级别不对")
        out.append(r)
    return out


def load_call_records(path: str | Path, subjects: tuple[str, ...]) -> list[dict]:
    data = read_limited(path, MAX_RECORDS_BYTES, "调用记录")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise InputRefused("调用记录不是 UTF-8") from None
    return parse_records(text, subjects)


# ---------------- 防回推：扰动、插值、粗化 ----------------
def record_key(pid: str) -> int:
    """学生的记录键：由伪名派生（只在内存里用；试点伪名由库内一次性 salt 生成，攻击者不知道）"""
    return int.from_bytes(hashlib.sha256(b"cost-dashboard/record-key\x00" + pid.encode("utf-8")).digest()[:8], "big")


def set_key(pids: Iterable[str]) -> int:
    return sum(record_key(p) for p in set(pids)) % KEY_MOD


def noise(key: int) -> int:
    """由成员集合的表键决定的人数扰动（−2…+2）；同一成员集合的各组共用"""
    h = hashlib.sha256(f"cost-dashboard/noise\x00{key}".encode("utf-8")).digest()
    u = int.from_bytes(h[:8], "big") / KEY_MOD
    for value, cum in NOISE_TABLE:
        if u < cum:
            return value
    return NOISE_TABLE[-1][0]


def floor_to(n: int, step: int) -> int:
    return (n // step) * step


def noisy_count(members: Iterable[str], dcfg: Mapping) -> int | None:
    """扰动后人数 ≥ min_group + NOISE_MAX 才公开（真实人数 ≥ min_group），返回取整后的扰动人数；否则 None"""
    ms = set(members)
    n = len(ms) + noise(set_key(ms))
    if n < dcfg["min_group"] + NOISE_MAX:
        return None
    return floor_to(n, dcfg["round_to"])


def interp_quantile(values: list[float], q: float) -> float:
    """相邻两秩线性插值（h = (n − 1)q）"""
    v = sorted(values)
    h = (len(v) - 1) * q
    lo = math.floor(h)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (h - lo) * (v[hi] - v[lo])


def coarsen(x: float, step: float) -> float:
    """四舍五入到 step 的整数倍（半数进位，不用银行家舍入）"""
    return round(math.floor(x / step + 0.5) * step, 6)


def group(points: list[tuple[str, float]], step: float, dcfg: Mapping) -> dict:
    """points = [(伪名, 值)]，每名学生一个点。人数扰动后不够门槛 → 只写“样本不足”；P90 以上不足 MIN_TAIL 人 → P90 写“样本不足”"""
    n = noisy_count([p for p, _ in points], dcfg)
    if n is None:
        return {"status": INSUFFICIENT}
    vals = [v for _, v in points]
    p50, p90 = interp_quantile(vals, 0.5), interp_quantile(vals, 0.9)
    tail = sum(1 for v in vals if v > p90 + 1e-12)
    return {"status": "ok", "n_students": n, "p50": coarsen(p50, step),
            "p90": coarsen(p90, step) if tail >= MIN_TAIL else INSUFFICIENT}


def quota_step(quota: float) -> float:
    return max(1.0, quota * STEP_QUOTA_SHARE)


# ---------------- 计算 ----------------
def allocation_shares(records: list[dict], subjects: tuple[str, ...]) -> dict[str, dict[str, float]]:
    """两种学科分摊口径（D5）：成功调用里各科的调用次数占比、token（输入 + 输出）占比；无科目的调用不参与"""
    calls = {s: 0 for s in subjects}
    tokens = {s: 0 for s in subjects}
    for r in records:
        s = r.get("subject")
        if r.get("outcome") == "ok" and s in calls:
            calls[s] += 1
            tokens[s] += int(r["input_tokens"]) + int(r["output_tokens"])
    out: dict[str, dict[str, float]] = {}
    for name, acc in (("calls", calls), ("tokens", tokens)):
        tot = sum(acc.values())
        out[name] = {s: (acc[s] / tot if tot else 0.0) for s in subjects}
    return out


@dataclass
class Prepared:
    """build_dashboard 的中间结果（报表与差分测试共用）：各组的点、粗化步长与汇总"""
    points: dict[tuple, tuple[list[tuple[str, float]], float]]
    present: list[str]
    res_of: dict[str, list[str]]
    pooled: dict
    stats: dict
    shares: dict[str, dict[str, float]]
    per_student: dict[str, dict]
    unalloc: dict[str, float]


def prepare(usage: Usage, records: list[dict], prices: dict, profile: dict, quotas: dict, dcfg: dict) -> Prepared:
    check_dash_config(dcfg)
    validate(prices, profile, quotas)
    ucfg = dcfg["unattributed"]
    validate(prices, {"subjects": {UNATTR: ucfg}, "fallback_tokens": profile["fallback_tokens"]}, quotas)
    fallback = profile["fallback_tokens"]
    subjects = tuple(profile["subjects"])
    # token：分科统计用于各科活动；不分科（全部记录按任务汇总）用于未归因活动与按任务的单次成本
    pooled = token_stats([{**r, "subject": UNATTR} for r in records], fallback)
    stats = {**token_stats(records, fallback), **pooled}

    def cost_of(s: str, use: Mapping[str, float]) -> float:
        cfg = ucfg if s == UNATTR else profile["subjects"][s]
        return monthly_cost(cfg, s, "_", 1.0, {"_": dict(use)}, stats, fallback, prices)

    st = usage.students
    present = [s for s in (*subjects, UNATTR) if any(s in u for _, u in st.values())]
    res_of = {s: sorted({r for _, u in st.values() for r in u.get(s, {})}) for s in present}
    res_of[ALL] = sorted({r for s in present for r in res_of[s]})
    shares = allocation_shares(records, subjects)

    per_student: dict[str, dict] = {}
    unalloc = {"calls": 0.0, "tokens": 0.0}
    for pid, (tier, use) in st.items():
        costs = {s: cost_of(s, use[s]) for s in use}
        un = costs.get(UNATTR, 0.0)
        active = [s for s in use if s != UNATTR]
        alloc: dict[str, dict[str, float]] = {}
        for m in ("calls", "tokens"):
            w = {s: shares[m][s] for s in active}
            tot = sum(w.values())
            if tot <= 0:
                unalloc[m] += un
            alloc[m] = {s: costs[s] + (un * w[s] / tot if tot > 0 else 0.0) for s in active}
        per_student[pid] = {"tier": tier, "costs": costs, "total": sum(costs.values()), "alloc": alloc}

    points: dict[tuple, tuple[list[tuple[str, float]], float]] = {}
    for tier in TIERS:
        members = [(p, u) for p, (t, u) in st.items() if t == tier]
        for s in (ALL, *present):
            pop = members if s == ALL else [(p, u) for p, u in members if s in u]
            for res in res_of[s]:
                vals = [(p, sum(u[x].get(res, 0.0) for x in u) if s == ALL else u[s].get(res, 0.0)) for p, u in pop]
                quota = quotas[tier][res]
                points[("consumption", tier, s, res)] = (vals, quota_step(quota))
                if quota > 0:
                    points[("utilization", tier, s, res)] = ([(p, v / quota) for p, v in vals], STEP_UTIL)
        points[("tier_cost", tier)] = ([(p, c["total"]) for p, c in per_student.items() if c["tier"] == tier], STEP_COST)
        for s in (x for x in present if x != UNATTR):
            pop2 = [(p, c) for p, c in per_student.items() if c["tier"] == tier and s in c["alloc"]["calls"]]
            for m in ("calls", "tokens"):
                points[(f"alloc_{m}", tier, s)] = ([(p, c["alloc"][m][s]) for p, c in pop2], STEP_COST)
    return Prepared(points, present, res_of, pooled, stats, shares, per_student, unalloc)


def price_label(prices: dict) -> str:
    """“签字价”只在价格表 example: false 且带签字版本号时成立，否则一律“示例价”"""
    ver = prices.get("signoff_version")
    if prices.get("example") is False and isinstance(ver, str) and SIGNOFF.fullmatch(ver):
        return f"签字价（{ver}）"
    return "示例价"


def banner(synthetic: bool, prices: dict) -> str:
    data = "合成，不是试点结论" if synthetic else "试点数据，须经人核对后才可对外"
    return f"{data}；价格为{price_label(prices)}"


def _ratio(x: float, step: float) -> float:
    return coarsen(x, step)


def build_dashboard(usage: Usage, records: list[dict], prices: dict, profile: dict, quotas: dict, dcfg: dict) -> dict:
    P = prepare(usage, records, prices, profile, quotas, dcfg)
    ucfg = dcfg["unattributed"]
    subjects = tuple(profile["subjects"])
    labels = {**{s: c["label"] for s, c in profile["subjects"].items()}, UNATTR: ucfg["label"], ALL: "全部（含未归因）"}

    consumption = []
    for tier in TIERS:
        for s in (ALL, *P.present):
            for res in P.res_of[s]:
                quota = quotas[tier][res]
                pts, step = P.points[("consumption", tier, s, res)]
                util = group(*P.points[("utilization", tier, s, res)], dcfg) if quota > 0 else {"status": ZERO_QUOTA}
                consumption.append({"tier": tier, "subject": s, "resource": res, "quota": quota, "step": step,
                                    "consumption": group(pts, step, dcfg), "utilization": util})

    tier_cost = []
    for tier in TIERS:
        g = group(*P.points[("tier_cost", tier)], dcfg)
        if g["status"] == "ok":
            mine = [c for c in P.per_student.values() if c["tier"] == tier]
            tot = sum(c["total"] for c in mine)
            un = sum(c["costs"].get(UNATTR, 0.0) for c in mine)
            g["unattributed_share"] = _ratio(un / tot, STEP_UTIL) if tot > 0 else None
        tier_cost.append({"tier": tier, **g})

    allocation = []
    for tier in TIERS:
        for s in (x for x in P.present if x != UNATTR):
            allocation.append({"tier": tier, "subject": s, "by_calls": group(*P.points[("alloc_calls", tier, s)], dcfg),
                               "by_tokens": group(*P.points[("alloc_tokens", tier, s)], dcfg)})
    un_total = sum(c["costs"].get(UNATTR, 0.0) for c in P.per_student.values())

    def call_stats(rs: list[dict]) -> dict[str, Any]:
        if len(rs) < MIN_CALLS:
            return {"status": INSUFFICIENT}
        ok = [r for r in rs if r["outcome"] == "ok"]
        with_att = [r for r in rs if "attempts" in r]
        with_fb = [r for r in rs if "fallback" in r]
        return {"status": "ok", "calls": floor_to(len(rs), ROUND_TO_FLOOR), "ok": floor_to(len(ok), ROUND_TO_FLOOR),
                "failure_share": _ratio((len(rs) - len(ok)) / len(rs), STEP_SHARE),
                "multi_attempt_share": _ratio(sum(1 for r in with_att if r["attempts"] > 1) / len(with_att), STEP_SHARE) if with_att else None,
                "fallback_share": _ratio(sum(1 for r in with_fb if r["fallback"]) / len(with_fb), STEP_SHARE) if with_fb else None}

    tasks = []
    for task in sorted({r["task"] for r in records}):
        rs = [r for r in records if r["task"] == task]
        row: dict[str, Any] = {"task": task, **call_stats(rs)}
        price = prices["per_million_tokens"].get(task)
        row["priced"] = price is not None
        if row["status"] == "ok" and price is not None:
            tok = P.pooled.get((UNATTR, task))
            spent = sum(per_call_cost(TokenStat(float(r["input_tokens"]), float(r["output_tokens"]), 1, ""), price) for r in rs)
            row["cost_per_ok_call"] = round(per_call_cost(tok, price), 6) if tok else None
            row["cost_per_call"] = round(spent / len(rs), 6)
        tasks.append(row)
    overall = call_stats(records)

    token_sources = []
    fallback = profile["fallback_tokens"]
    for s in (*subjects, UNATTR):
        if s not in P.present:
            continue
        cfg = ucfg if s == UNATTR else profile["subjects"][s]
        for a in cfg.get("activities", []):
            if a["task"] and a["task"] != "voice" and a["calls_per_unit"] and a["resource"] in P.res_of[s]:
                t = lookup_tokens(P.stats, fallback, s, a["task"])
                token_sources.append({"subject": s, "task": a["task"], "input_tokens": round(t.input, 1), "output_tokens": round(t.output, 1),
                                      "calls_observed": floor_to(t.calls, ROUND_TO_FLOOR), "source": t.source,
                                      "per_call_cost": round(per_call_cost(t, prices["per_million_tokens"][a["task"]]), 6)})
    token_sources = list({(t["subject"], t["task"]): t for t in token_sources}.values())
    unpriced = []
    for s in P.present:
        cfg = ucfg if s == UNATTR else profile["subjects"][s]
        for r in P.res_of[s]:
            acts = [a for a in cfg.get("activities", []) if a["resource"] == r]
            if not any(a["task"] and a["calls_per_unit"] for a in acts):
                why = next((a["note"] for a in acts if a.get("note")), "") or "成本画像里没有该资源"
                unpriced.append({"subject": s, "resource": r, "reason": why})

    n_all = noisy_count(usage.pseudonyms, dcfg)
    return {
        "banner": banner(usage.synthetic, prices),
        "synthetic": usage.synthetic, "example_prices": price_label(prices) == "示例价", "currency": prices.get("currency", "CNY"),
        "quota_config": usage.quota_config, "min_group": dcfg["min_group"], "round_to": dcfg["round_to"],
        "cohort": {"n_students": INSUFFICIENT if n_all is None else n_all, "months": list(usage.months)},
        "labels": labels, "present": P.present,
        "consumption": consumption, "tier_cost": tier_cost,
        "tasks": tasks, "tasks_overall": overall,
        "allocation": {"note": D5, "shares": {m: {s: _ratio(v, STEP_SHARE) for s, v in P.shares[m].items()} for m in P.shares},
                       "rows": allocation,
                       "unallocated_share": {m: (_ratio(P.unalloc[m] / un_total, STEP_UTIL) if un_total > 0 else None) for m in P.unalloc}},
        "token_sources": token_sources, "unpriced": unpriced,
        "unattributed_assumptions": [{"resource": a["resource"], "task": a["task"], "calls_per_unit": a["calls_per_unit"],
                                      "note": a.get("note", "")} for a in ucfg["activities"]],
        "prices_ref": {t: {k: p[k] for k in ("input", "output")} for t, p in sorted(prices["per_million_tokens"].items())},
        "voice_price": prices["voice_per_minute"]["price"],
    }


# ---------------- 报表 ----------------
def _pct(v: Any) -> str:
    if isinstance(v, str):
        return v
    return "—" if v is None else f"{v:.0%}"


def _num(v: Any) -> str:
    if isinstance(v, str):
        return v
    return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}"


def _yuan(v: Any) -> str:
    return v if isinstance(v, str) else f"{v:,.1f}"


def _g(g: dict, fmt) -> tuple[str, str, str]:
    if g["status"] != "ok":
        return g["status"], g["status"], g["status"]
    return f"约 {g['n_students']}", fmt(g["p50"]), fmt(g["p90"])


def render_dashboard_md(r: dict) -> str:
    lb = r["labels"]
    cur = r["currency"]
    source = ("消耗聚合行与调用记录都是固定种子的合成队列（`python -m loadtest dashboard-synth`），只用于检验口径与报表"
              if r["synthetic"] else "消耗聚合行来自试点只读导出、调用记录来自结构化日志的元数据导出")
    price_note = "价格表是占位示例，不是任何真实供应商的价格" if r["example_prices"] else "价格取签字价格表"
    n_all = r["cohort"]["n_students"]
    lines = [f"# 成本仪表盘（PLT-17 第一步）：{r['banner']}", "",
             f"> **{r['banner']}**。{source}；{price_note}。"
             "档位为导出时档位，期内升降档未拆分；统计窗口为每名学生的当前计费周期（滚动 30 天，与自然月不对齐，导出时可能未满；"
             "month 列为周期开始的月份），多个周期时按人取月均。", "",
             f"> 只出聚合：每名学生一个点；组人数加 ±{NOISE_MAX} 的确定性扰动，扰动后 < {r['min_group'] + NOISE_MAX} 只写“{INSUFFICIENT}”"
             f"（保证真实人数 ≥ {r['min_group']}），公开的人数为扰动后人数向下取整到 {r['round_to']}；P50 / P90 为相邻两秩插值后粗化"
             f"（消耗到该档额度的 5% 一档、使用率到 5%、成本到 0.1 {cur}），P90 以上不足 {MIN_TAIL} 人时 P90 写“{INSUFFICIENT}”；"
             f"不含伪名。额度取草稿配置 `{r['quota_config']}`（迁移 0015，未发布）。单位：{cur} / 学生 / 月。", "",
             f"队列：学生数 {n_all if isinstance(n_all, str) else '约 ' + str(n_all)}，月份 {'、'.join(r['cohort']['months'])}。", "",
             "## 1. 各档月人均模型成本", "",
             "| 会员档 | 学生数 | P50 | P90 | 其中未归因成本占比 |", "|---|---|---|---|---|"]
    for t in r["tier_cost"]:
        n, p50, p90 = _g(t, _yuan)
        lines.append(f"| {TIER_LABELS[t['tier']]} | {n} | {p50} | {p90} | "
                     f"{_pct(t.get('unattributed_share')) if t['status'] == 'ok' else t['status']} |")
    lines += ["", "## 2. 各档 × 各科 月人均消耗与额度使用率", "",
              "“全部”= 该档全部学生（各科与未归因相加，与额度直接可比）；单科行只含在读该科的学生（有任何该科消耗）。"
              "使用率 = 消耗 ÷ 该档额度（该科消耗占全科共用额度的比例）。消耗一档 = 该档额度的 5%（至少 1）。", "",
              "| 会员档 | 科目 | 资源 | 额度 | 学生数 | 消耗 P50 | 消耗 P90 | 使用率 P50 | 使用率 P90 |", "|---|---|---|---|---|---|---|---|---|"]
    for c in r["consumption"]:
        n, p50, p90 = _g(c["consumption"], _num)
        u = c["utilization"]
        u50, u90 = (_pct(u["p50"]), _pct(u["p90"])) if u["status"] == "ok" else (u["status"], u["status"])
        lines.append(f"| {TIER_LABELS[c['tier']]} | {lb[c['subject']]} | {c['resource']} | {_num(c['quota'])} | {n} | {p50} | {p90} | {u50} | {u90} |")
    lines += ["", "## 3. 每次调用平均成本（按任务）与失败、多次尝试、回退", "",
              "每次成功调用成本 = 该任务成功调用的平均 token × 价格（不分科）；每次调用平均成本 = 全部调用（含失败）花费 ÷ 调用数。"
              f"调用数 < {MIN_CALLS} 的任务只写“{INSUFFICIENT}”，调用数取整到 10，占比到 1%。"
              "多次尝试占比 = 尝试次数 > 1 的调用占比（含重试后仍失败的调用，与失败占比重叠）；回退占比 = 由备选供应商完成的调用占比。"
              f"真实调用日志目前没有“回退”字段（只记尝试次数），真实导出时回退占比写“{NOT_AVAILABLE}”。", "",
              "| 任务 | 调用数 | 成功 | 失败占比 | 多次尝试占比 | 回退占比 | 每次成功调用成本 | 每次调用平均成本 |", "|---|---|---|---|---|---|---|---|"]

    def call_row(name: str, t: dict, priced: bool | None) -> str:
        if t["status"] != "ok":
            return f"| {name} | {INSUFFICIENT} | {INSUFFICIENT} | {INSUFFICIENT} | {INSUFFICIENT} | {INSUFFICIENT} | — | — |"
        ma = NOT_AVAILABLE if t["multi_attempt_share"] is None else _pct(t["multi_attempt_share"])
        fb = NOT_AVAILABLE if t["fallback_share"] is None else _pct(t["fallback_share"])
        if priced is None:
            costs = "— | —"
        elif priced:
            ok_c = "—" if t["cost_per_ok_call"] is None else f"{t['cost_per_ok_call']:.6f}"
            costs = f"{ok_c} | {t['cost_per_call']:.6f}"
        else:
            costs = f"{NO_PRICE} | {NO_PRICE}"
        return f"| {name} | 约 {t['calls']} | 约 {t['ok']} | {_pct(t['failure_share'])} | {ma} | {fb} | {costs} |"

    lines += [call_row(t["task"], t, t["priced"]) for t in r["tasks"]]
    lines.append(call_row("全部", r["tasks_overall"], None))
    a = r["allocation"]
    lines += ["", f"## 4. 学科分摊（{D5}）：两种口径并列", "",
              "未归因成本（讲解、语音等归不到学科的消耗）按成功调用里各科的占比，分到该学生在读的科目（占比在在读科目间重新归一）；"
              "该生没有在读科目、或在读科目在调用记录里占比为 0 时算“未能分摊”。下表为分摊后各科的学生月成本。", "",
              "| 科目 | 按调用次数占比 | 按 token 占比 |", "|---|---|---|"]
    for s, v in a["shares"]["calls"].items():
        if s not in r["present"] and v == 0 and a["shares"]["tokens"][s] == 0:
            continue
        lines.append(f"| {lb[s]} | {_pct(v)} | {_pct(a['shares']['tokens'][s])} |")
    lines += ["", f"未能分摊的未归因成本占比：按调用次数 {_pct(a['unallocated_share']['calls'])}，按 token {_pct(a['unallocated_share']['tokens'])}。", "",
              "| 会员档 | 科目 | 学生数 | 按调用次数 P50 | 按调用次数 P90 | 按 token P50 | 按 token P90 |", "|---|---|---|---|---|---|---|"]
    for x in a["rows"]:
        n, c50, c90 = _g(x["by_calls"], _yuan)
        _, t50, t90 = _g(x["by_tokens"], _yuan)
        lines.append(f"| {TIER_LABELS[x['tier']]} | {lb[x['subject']]} | {n} | {c50} | {c90} | {t50} | {t90} |")
    lines += ["", "## 5. 模型成本按 0 计的消耗", "",
              "下列 科目 × 资源 在成本画像里没有模型调用（不调模型，或本版没有价格口径），模型成本按 0 计；原因取画像的说明：", ""]
    lines += [f"- {lb[u['subject']]} · {u['resource']}：{u['reason']}" for u in r["unpriced"]] or ["- 无"]
    lines += ["", "## 6. token 来源与价格（" + ("示例价" if r["example_prices"] else "签字价") + "）", "",
              "| 科目 | 任务 | 平均输入 token | 平均输出 token | 观测调用数 | 单次成本 | 来源 |", "|---|---|---|---|---|---|---|"]
    for t in r["token_sources"]:
        src = t["source"] if t["source"] == SOURCE_ASSUMED else ("调用记录（合成）" if r["synthetic"] else "调用记录")
        lines.append(f"| {lb[t['subject']]} | {t['task']} | {t['input_tokens']} | {t['output_tokens']} | 约 {t['calls_observed']} | "
                     f"{t['per_call_cost']:.6f} | {src} |")
    lines += ["", "| 任务 | 输入 / 百万 token | 输出 / 百万 token |", "|---|---|---|"]
    lines += [f"| {t} | {p['input']} | {p['output']} |" for t, p in r["prices_ref"].items()]
    lines += [f"| 语音（每分钟） | {r['voice_price']} / 分钟 | — |", "",
              "## 7. 口径与待人确认", "",
              "- 各科“资源 → 任务 × 每单位调用次数”沿用 `config/cost_profile.yaml`（全是假设）；未归因消耗的计价见 `config/cost_dashboard.yaml`：",
              ]
    lines += [f"  - {u['resource']}：{u['task'] or '—'} × {u['calls_per_unit']}（{u['note']}）" for u in r["unattributed_assumptions"]]
    lines += [f"- 学科分摊口径、未归因消耗如何计入：{D5}；价格表（D7）与模型调用日志落库方式待人确认；最小组人数、扰动幅度与粗化粒度待人确认；",
              "- 档位为导出时的 `subscription.tier`（升级在原行上改、没有历史），期内升降档未拆分；计费周期滚动 30 天，与自然月不对齐；",
              "- 失败调用的 token 按记录计（模型路由对失败调用记 0，供应商可能计费的失败尝试未计入，同成本报告）；",
              "- 表键扰动挡住“删一人重跑”的差分，但不是差分隐私；组成员身份（某人在哪些组）仍可能由前后两次输出是否变化看出；",
              "- 试点导出函数与授权另起 [NEEDS-HUMAN] PR（RUNBOOK 第 4 节，SQL 草案 `sql/cost-usage-export.draft.sql`，不是迁移）。", ""]
    return "\n".join(lines)


def assert_no_pseudonyms(text: str) -> None:
    """出口检查：输出里不得出现任何伪名形状的串"""
    if PSEUDONYM_ANYWHERE.search(text):
        raise RuntimeError("输出里出现了伪名，拒绝写出")


def render_files(r: dict) -> dict[str, str]:
    files = {"cost-dashboard.json": json.dumps(r, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
             "cost-dashboard.md": render_dashboard_md(r)}
    for text in files.values():
        assert_no_pseudonyms(text)
    return files


def write_outputs(files: dict[str, str], out_dir: Path) -> list[Path]:
    """同 tools/kge-calibrate report.write_files：目录须不存在或为空、不是符号链接；文件 0600、O_EXCL、O_NOFOLLOW 新建"""
    if out_dir.is_symlink():
        raise OutputRefused("输出目录不能是符号链接")
    if out_dir.exists():
        if not out_dir.is_dir():
            raise OutputRefused("输出路径已存在且不是目录：请指定新目录或空目录")
        if any(out_dir.iterdir()):
            raise OutputRefused("输出目录非空：请指定新目录或空目录")
    out_dir.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    written = []
    for name in sorted(files):
        path = out_dir / name
        try:
            fd = os.open(path, flags, FILE_MODE)
        except FileExistsError:
            raise OutputRefused("输出文件已存在：请指定新目录或空目录") from None
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(files[name])
        written.append(path)
    return written


def load_inputs(usage_path: Path, records_path: Path, prices_path: Path, profile_path: Path, dash_path: Path):
    profile = load_yaml(profile_path)
    quotas = load_quotas(profile["quota_config"])
    subjects = tuple(profile["subjects"])
    usage = load_usage(usage_path, quotas, profile["quota_config"], subjects)
    records = load_call_records(records_path, subjects)
    return usage, records, load_yaml(prices_path), profile, quotas, check_dash_config(load_yaml(dash_path))


def run_dashboard(usage_path: Path, records_path: Path, prices_path: Path, profile_path: Path, dash_path: Path,
                  out: Path | None) -> dict:
    inputs = load_inputs(usage_path, records_path, prices_path, profile_path, dash_path)
    if not inputs[0].synthetic and out is None:
        raise OutputRefused("非合成输入必须用 --out 指定受控目录（不写到系统临时目录）")
    r = build_dashboard(*inputs)
    files = render_files(r)
    out = Path(tempfile.mkdtemp(prefix="starbuddy-cost-dashboard.")) if out is None else out
    write_outputs(files, out)
    return {**r, "out": str(out)}
