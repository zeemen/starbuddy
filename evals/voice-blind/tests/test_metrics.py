"""指标与手算一致：编辑距离、CER / WER（含中英混排、空参考、全错、规范化开关）、分位数、相关系数、音素一致性、自助法区间。"""
from __future__ import annotations

import math

import pytest

from voiceblind.metrics import (EditCounts, PhoneCounts, PhoneJudgement, align, corpus_rate, edit_counts, pearson,
                                phone_counts, quantile, ranks, spearman)
from voiceblind.stats import bootstrap_ci
from voiceblind.text import EnNorm, ZhNorm, en_words, zh_units


def cer(ref: str, hyp: str, cfg: ZhNorm = ZhNorm()) -> EditCounts:
    return edit_counts(zh_units(ref, cfg), zh_units(hyp, cfg))


def wer(ref: str, hyp: str, cfg: EnNorm = EnNorm()) -> EditCounts:
    return edit_counts(en_words(ref, cfg), en_words(hyp, cfg))


# ---------- 编辑距离 ----------
def test_edit_distance_textbook():
    c = edit_counts(list("kitten"), list("sitting"))
    assert (c.sub, c.dele, c.ins, c.ref_len) == (2, 0, 1, 6) and c.errors == 3
    assert edit_counts(list("abc"), list("abc")) == EditCounts(0, 0, 0, 3)
    assert edit_counts(list("abcd"), list("acd")) == EditCounts(0, 1, 0, 4)
    assert edit_counts([], []) == EditCounts(0, 0, 0, 0)


def test_align_pairs_are_deterministic():
    assert align(list("ab"), list("xb")) == [(0, 0), (1, 1)]
    assert align(list("abc"), list("ac")) == [(0, 0), (1, None), (2, 1)]
    assert align(list("ac"), list("abc")) == [(0, 0), (None, 1), (1, 2)]


def test_cer_hand_computed():
    c = cer("今天天气很好。", "今天天汽很好啊")
    assert (c.sub, c.dele, c.ins, c.ref_len) == (1, 0, 1, 6)
    assert c.rate == pytest.approx(2 / 6)


def test_cer_mixed_chinese_english():
    # 缺省：拉丁字母逐字符计 → 我 们 用 a i 学 习（7）；“爱”替换 a，i 删除
    c = cer("我们用AI学习", "我们用爱学习")
    assert (c.sub, c.dele, c.ins, c.ref_len) == (1, 1, 0, 7)
    # 拉丁串按一个单位计 → 我 们 用 ai 学 习（6）；1 个替换
    c2 = cer("我们用AI学习", "我们用爱学习", ZhNorm(latin_as_word=True))
    assert (c2.sub, c2.dele, c2.ins, c2.ref_len) == (1, 0, 0, 6)
    # 大小写：缺省不敏感
    assert cer("用AI学", "用ai学").errors == 0
    assert cer("用AI学", "用ai学", ZhNorm(lowercase=False)).errors == 2


def test_cer_empty_reference_and_all_wrong():
    empty = cer("", "嗯")
    assert (empty.ins, empty.ref_len) == (1, 0) and empty.rate is None
    assert cer("", "").rate is None
    # 空参考的插入仍计入合计：1 个插入 / 4 个参考字
    assert corpus_rate([empty, cer("你好世界", "你好世界")]) == pytest.approx(1 / 4)
    assert corpus_rate([empty]) is None
    assert cer("你好", "再见").rate == 1.0
    assert cer("你好", "").rate == 1.0
    assert cer("你好", "再见再见").rate == 2.0  # 插入可以让错误率超过 1


def test_cer_normalization_switches():
    assert cer("ＡＢ测试，", "ab测试").errors == 0
    assert cer("ＡＢ测试，", "ab测试", ZhNorm(nfkc=False)).sub == 2
    c = cer("你好，世界。", "你好世界", ZhNorm(strip_punct=False))
    assert (c.dele, c.ref_len) == (2, 6)
    assert cer("你好，世界。", "你好世界").errors == 0
    assert cer("你 好", "你好").errors == 0  # 空白不计


def test_wer_hand_computed_and_rules():
    c = wer("I don't KNOW.", "i dont know")
    assert (c.sub, c.ref_len) == (1, 3)
    assert wer("I don't KNOW.", "i dont know", EnNorm(keep_apostrophe=False)).errors == 0
    assert wer("I don’t know", "I don't know").errors == 0  # 弯撇号统一
    assert wer("Hello world", "hello world").errors == 0
    assert wer("Hello world", "hello world", EnNorm(lowercase=False)).rate == pytest.approx(1 / 2)
    assert wer("Hello, world!", "Hello world", EnNorm(strip_punct=False)).rate == pytest.approx(2 / 2)
    assert wer("a well-known fact", "a well known fact").errors == 0
    c2 = wer("well-known", "well known", EnNorm(split_hyphen=False))
    assert (c2.sub, c2.ins, c2.ref_len) == (1, 1, 1) and c2.rate == 2.0
    assert wer("", "uh").rate is None
    assert wer("the cat sat", "a dog ran").rate == 1.0


def test_corpus_rate_is_ratio_of_sums_not_mean():
    a = EditCounts(1, 0, 0, 10)
    b = EditCounts(2, 0, 0, 2)
    assert corpus_rate([a, b]) == pytest.approx(3 / 12)
    assert corpus_rate([]) is None


# ---------- 分位数 ----------
def test_quantile_nearest_rank():
    xs = list(range(10, 0, -1))  # 乱序输入
    assert quantile(xs, 0.5) == 5 and quantile(xs, 0.95) == 10
    ys = list(range(1, 21))
    assert quantile(ys, 0.5) == 10 and quantile(ys, 0.95) == 19
    assert quantile([7.0], 0.95) == 7.0 and quantile([], 0.5) is None
    assert quantile([0.3, 0.1, 0.2], 0.5) == 0.2
    with pytest.raises(ValueError):
        quantile([1], 0)


# ---------- 相关系数 ----------
def test_pearson_hand_computed():
    assert pearson([1, 2, 3, 4], [2, 4, 6, 8]) == pytest.approx(1.0)
    assert pearson([1, 2, 3, 4], [1, 3, 2, 4]) == pytest.approx(0.8)
    assert pearson([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert pearson([1, 2, 3], [5, 5, 5]) is None
    assert pearson([1], [1]) is None


def test_spearman_uses_average_ranks():
    assert ranks([10, 20, 20, 30]) == [1, 2.5, 2.5, 4]
    assert ranks([3, 1, 2]) == [3, 1, 2]
    x, y = [1, 2, 3, 4], [1, 8, 27, 64]
    assert spearman(x, y) == pytest.approx(1.0) and pearson(x, y) < 0.99
    assert spearman([1, 2, 3, 4], [1, 2, 2, 3]) == pytest.approx(4.5 / math.sqrt(22.5))


# ---------- 音素一致性 ----------
def P(*spec: str) -> list[PhoneJudgement]:
    return [PhoneJudgement(s.rstrip("!"), not s.endswith("!")) for s in spec]


def test_phone_counts_hand_computed():
    c = phone_counts(P("a", "b!", "c", "d!"), P("a", "b!", "c!", "d"))
    assert (c.agree, c.total, c.tp, c.fp, c.fn) == (2, 4, 1, 1, 1)
    assert (c.accuracy, c.recall, c.precision) == (0.5, 0.5, 0.5)


def test_phone_counts_missing_and_extra_phones():
    # 供应商漏了 b（专家标错）：不计一致、计漏检；多出的 x 不计
    c = phone_counts(P("a", "b!", "c", "d!"), P("a", "x", "c", "d!"))
    assert (c.agree, c.total, c.tp, c.fp, c.fn) == (3, 4, 1, 0, 1)
    c2 = phone_counts(P("a", "b!", "c"), P("a", "c", "z!"))
    assert (c2.agree, c2.total, c2.tp, c2.fp, c2.fn) == (2, 3, 0, 0, 1)
    assert c2.precision is None and c2.recall == 0.0
    s = c + c2
    assert (s.agree, s.total, s.tp, s.fp, s.fn) == (5, 7, 1, 0, 2)
    assert PhoneCounts().accuracy is None


# ---------- 自助法 ----------
def mean(xs):
    return sum(xs) / len(xs)


def test_bootstrap_reproducible_and_seeded():
    items = [0.1, 0.4, 0.2, 0.9, 0.5, 0.3, 0.7, 0.05]
    a = bootstrap_ci(items, mean, seed="s1", resamples=500)
    assert a == bootstrap_ci(items, mean, seed="s1", resamples=500)
    assert a != bootstrap_ci(items, mean, seed="s2", resamples=500)
    assert a["ci_low"] <= a["value"] <= a["ci_high"] and a["n"] == 8
    assert a["value"] == round(mean(items), 6)


def test_bootstrap_percentiles_hand_checked():
    import random
    items = [1.0, 2.0, 3.0, 4.0, 5.0]
    rng = random.Random("fixed")
    means = sorted(mean([items[rng.randrange(5)] for _ in range(5)]) for _ in range(200))
    b = bootstrap_ci(items, mean, seed="fixed", resamples=200)
    assert b["ci_low"] == round(means[5], 6) and b["ci_high"] == round(means[194], 6)


def test_bootstrap_degenerate():
    assert bootstrap_ci([2.0, 2.0, 2.0], mean, seed="x", resamples=100) == {"value": 2.0, "ci_low": 2.0, "ci_high": 2.0, "n": 3}
    assert bootstrap_ci([], mean, seed="x")["value"] is None
    assert bootstrap_ci([1.0], lambda xs: None, seed="x")["value"] is None
