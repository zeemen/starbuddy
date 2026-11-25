"""引语逐字校验在洞察服务的接线（specs/quote-verbatim-guard 第二节第 2、6 条）：首份报告、周报的一致性检查按受众传 quotes_allowed
（只有学生版为真，家长版出现引语即阻断）；没有原文核对结果，所以一旦出现引语一律按未核对阻断（fail closed）。
规则本身的用例在 services/learning/tests/test_mastery_quotes.py。全部合成数据。"""
from __future__ import annotations

import pytest

from insight import report as report_mod
from insight import weekly as weekly_mod
from insight.shared import consistency
import test_report as TR
import test_weekly as TW


@pytest.fixture
def calls(monkeypatch):
    seen: list[dict] = []
    real = consistency.check_page

    def spy(*args, **kw):
        seen.append(kw)
        return real(*args, **kw)

    monkeypatch.setattr(consistency, "check_page", spy)
    return seen


@pytest.mark.parametrize("audience,allowed", [("student", True), ("guardian", False)])
def test_report_passes_audience(calls, audience, allowed):
    assert TR.build(audience=audience).ok
    assert [c.get("quotes_allowed") for c in calls] == [allowed] and all("quote_verdicts" not in c for c in calls)


@pytest.mark.parametrize("audience,allowed", [("student", True), ("guardian", False)])
def test_weekly_passes_audience(calls, audience, allowed):
    assert TW.build(audience=audience).ok
    assert [c.get("quotes_allowed") for c in calls] == [allowed] and all("quote_verdicts" not in c for c in calls)


def test_quote_without_verdict_blocks_even_for_student():
    """接线口径：学生版允许引语，但没有原文核对结果 → quote_unverified"""
    cv = consistency.ClaimView("c", "established", "syn.math.node_mastery", evidence_ids=("e",))
    sec = consistency.Section("node_mastery", "", (consistency.Statement("c", "合成"),), (consistency.Quote("a", "c", "e", "合成"),))
    res = consistency.check_page([sec], {"c": cv}, TR.CFG, TR.FORBID, quotes_allowed=True)
    assert "quote_unverified" in res.codes


def test_modules_share_the_learning_engine():
    assert report_mod.consistency is consistency and weekly_mod.consistency is consistency
