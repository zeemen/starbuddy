"""体检报告的绑定字段（kge-lint-report/1.1，KGE-11 审查处理）：报告说清楚它查的是哪些页面的哪个版本、依据哪个地图版本、
用了哪些输入与配置，消费方（审核台分流建议、M1 统计）据此核对，不把别处的报告张冠李戴。

摘要一律是 SHA-256 十六进制全长；不含本机路径、不含页面原文（规则 13）。
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

from ..atoms import Snapshot
from ..pages.format import Page, serialize


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str))


def snapshot_digest(snap: Snapshot | None) -> str | None:
    """地图快照摘要：版本、学科、状态、课标年份与每个原子的哈希（原子哈希已覆盖原子全部字段）"""
    if snap is None:
        return None
    return sha256_json({"map_version": snap.map_version, "subject": snap.subject, "status": snap.status,
                        "standard_year": snap.standard_year, "atoms": {k: a.hash for k, a in snap.atoms.items()}})


def scope_pages(files: Iterable[Any], only: Iterable[str] | None = None) -> list[dict[str, Any]]:
    """检查的页面：page_id、page_version、页面原文 sha256（按页面 ID 排序）。files 是 lint.checks.PageFile；解析失败的页面按文件名记"""
    keep = None if only is None else set(only)
    out = []
    for pf in files:
        pid = pf.page.page_id if pf.page is not None else pf.rel
        if keep is not None and pid not in keep:
            continue
        out.append({"page_id": pid, "page_version": pf.page.page_version if pf.page is not None else None, "sha256": sha256_text(pf.text)})
    return sorted(out, key=lambda d: str(d["page_id"]))


def pages_digest(pages: Mapping[str, Page] | None) -> str | None:
    if not pages:
        return None
    return sha256_json({pid: sha256_text(serialize(p)) for pid, p in sorted(pages.items())})
