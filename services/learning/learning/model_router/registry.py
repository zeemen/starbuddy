"""供应商登记联动（LT-02 范围第 3 条）：路由表 staging 及以上环境登记的供应商，必须在 docs/compliance/vendors.md 的登记表里，
且两边状态一致。只在测试与评审时运行（登记表不进镜像），不参与服务启动。

登记表解析（尽量稳健）：用注释标记 `<!-- starbuddy:vendor-registry -->` 锚定登记表（标记之后的第一张表，表头必须含“供应商 ID”；
不靠“文档里第一张含供应商 ID 的表”，安全审查建议 4），按表头名定位“供应商 ID”“允许的主机名”“状态”三列（列序可变）；
单元格去掉首尾空白与反引号；“（无）”与空行跳过；同一供应商 ID 出现两次、状态不认识、表格缺列、标记缺失或重复、
主机名一栏里出现不像主机名的片段（占位整格写成 <…> 或（…）除外）都报错。
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .config import _HOST, STRICT_ENVS, is_placeholder

MARKER = "<!-- starbuddy:vendor-registry -->"

# 路由表 whitelist → 登记表里允许的状态。test_only（本地假供应商）不能出现在 staging 及以上
STATUS_FOR_WHITELIST: dict[str, frozenset[str]] = {
    "pending": frozenset({"待开通 / 待登记", "待审"}),
    "approved": frozenset({"已批准"}),
}
KNOWN_STATUS = frozenset({"待开通 / 待登记", "待审", "已批准", "停用"})
_COL_ID, _COL_HOSTS, _COL_STATUS = "供应商 ID", "允许的主机名", "状态"
_SEP_ROW = re.compile(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
_EMPTY = frozenset({"", "（无）", "(无)", "无", "-", "—"})


class RegistryError(ValueError):
    pass


@dataclass(frozen=True)
class Entry:
    vendor_id: str
    hosts: tuple[str, ...]  # 已填写的主机名；占位（<…> 或（…））不算
    hosts_placeholder: bool
    status: str


def _cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    parts = re.split(r"(?<!\\)\|", s)
    return [_clean(p) for p in parts]


def _clean(cell: str) -> str:
    c = cell.replace("\\|", "|").strip()
    if len(c) >= 2 and c[0] == "`" and c[-1] == "`":
        c = c[1:-1].strip()
    c = re.sub(r"\s+", " ", c.replace("`", ""))
    return c.replace("*", "").strip()


def _norm_status(raw: str) -> str:
    s = re.sub(r"\s*/\s*", " / ", raw.strip())
    return s


def _hosts(cell: str) -> tuple[tuple[str, ...], bool]:
    if cell in _EMPTY:
        return (), False
    if is_placeholder(cell) or re.fullmatch(r"[（(][^（）()]*[）)]", cell):
        return (), True
    tokens = [t.strip() for t in re.split(r"[,，、;；\s]+", cell) if t.strip()]
    bad = [t for t in tokens if not _HOST.match(t)]
    if bad:
        raise RegistryError(f"登记表“允许的主机名”里有不像主机名的片段（只收小写主机名，用逗号或顿号分隔；占位须整格写成 <…>）：{bad[0][:40]!r}")
    return tuple(sorted(set(tokens))), False


def parse_registry(text: str) -> dict[str, Entry]:
    lines = text.splitlines()
    marks = [i for i, line in enumerate(lines) if line.strip() == MARKER]
    if len(marks) != 1:
        raise RegistryError(f"登记表必须有且只有一个标记 {MARKER}（现有 {len(marks)} 个）")
    header_at = next((i for i in range(marks[0] + 1, len(lines)) if lines[i].lstrip().startswith("|")), None)
    if header_at is None or not any(c.startswith(_COL_ID) for c in _cells(lines[header_at])):
        raise RegistryError("登记表标记之后的第一张表，表头必须含“供应商 ID”")
    header = _cells(lines[header_at])

    def col(name: str) -> int:
        hits = [k for k, c in enumerate(header) if c.startswith(name)]
        if len(hits) != 1:
            raise RegistryError(f"登记表表头缺少或重复列“{name}”")
        return hits[0]

    ci, ch, cs = col(_COL_ID), col(_COL_HOSTS), col(_COL_STATUS)
    out: dict[str, Entry] = {}
    for line in lines[header_at + 1:]:
        if not line.lstrip().startswith("|"):
            break  # 表格结束
        if _SEP_ROW.match(line.strip()):
            continue
        cells = _cells(line)
        if len(cells) != len(header):
            raise RegistryError(f"登记表有一行列数不对（{len(cells)} ≠ {len(header)}）")
        vid = cells[ci]
        if vid in _EMPTY:
            continue
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,47}", vid):
            raise RegistryError(f"登记表的供应商 ID {vid!r} 不合格式（与路由表的键一致：小写字母、数字、连字符）")
        if vid in out:
            raise RegistryError(f"登记表里供应商 ID {vid!r} 出现了两次")
        status = _norm_status(cells[cs])
        if status not in KNOWN_STATUS:
            raise RegistryError(f"登记表里 {vid!r} 的状态 {status!r} 不认识（只收 {'、'.join(sorted(KNOWN_STATUS))}）")
        hosts, placeholder = _hosts(cells[ch])
        out[vid] = Entry(vendor_id=vid, hosts=hosts, hosts_placeholder=placeholder, status=status)
    return out


def check_routes_against_registry(route_doc: Mapping[str, Any], registry: Mapping[str, Entry]) -> list[str]:
    """返回不一致的说明（空列表 = 一致）。读路由表原文（不经 parse：staging 带占位时 parse 本来就拒绝）"""
    problems: list[str] = []
    envs = (route_doc or {}).get("environments") or {}
    for env in sorted(STRICT_ENVS):
        section = envs.get(env) or {}
        vendors = section.get("vendors") or {}
        routes = section.get("routes") or {}
        referenced = {t.get("vendor") for r in routes.values() if isinstance(r, dict)
                      for t in (r.get("primary"), r.get("backup")) if isinstance(t, dict)}
        for vid in sorted({*vendors, *(v for v in referenced if isinstance(v, str))}):
            where = f"{env}.vendors.{vid}"
            v = vendors.get(vid)
            if not isinstance(v, dict):
                problems.append(f"{where}: 路由引用了本环境没有登记的供应商")
                continue
            entry = registry.get(vid)
            if entry is None:
                problems.append(f"{where}: 不在 docs/compliance/vendors.md 的登记表里")
                continue
            wl = v.get("whitelist")
            if entry.status == "停用":
                problems.append(f"{where}: 登记表里已停用，不能出现在路由表")
            elif wl not in STATUS_FOR_WHITELIST:
                problems.append(f"{where}: whitelist={wl!r} 不能出现在 {env}（测试专用供应商只在 dev、test）")
            elif entry.status not in STATUS_FOR_WHITELIST[wl]:
                problems.append(f"{where}: 路由表 whitelist={wl} 与登记表状态“{entry.status}”不一致")
            hosts = v.get("allowed_hosts") or []
            route_placeholder = any(is_placeholder(h) for h in hosts)
            if not route_placeholder and hosts:
                if entry.hosts_placeholder or set(entry.hosts) != {str(h).lower() for h in hosts}:
                    problems.append(f"{where}: allowed_hosts 与登记表“允许的主机名”不一致")
            elif wl == "approved":
                problems.append(f"{where}: 已批准的供应商必须填好 allowed_hosts（不能是占位或空）")
    return problems
