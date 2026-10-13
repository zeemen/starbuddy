// KGE-08 第一步的全部阈值集中在这一份配置里。
// 待教研确认：下面每个判定阈值都是构建方按合成样例与“宁严勿松”原则定的初值，没有经过教研标注数据校准；
// 上线前由教研负责人确认（specs/kge08-paper-dedupe/spec.md 范围 1“阈值写在一份配置里”）。改动须同时更新单测里的预期。
// limits 是资源上限（防止输入撑爆内存或时间），不是教研阈值。

export interface DedupeConfig {
  /** 字符 n-gram 的 n（SimHash 与 Jaccard 共用）。待教研确认 */
  ngram: number;
  /** 近似题两道门：两道都过才算近似，缺一不成。待教研确认 */
  near: {
    /** 门一：64 位 SimHash 的汉明距离上限（含） */
    maxHamming: number;
    /** 门二：字符 n-gram 集合的 Jaccard 下限（含） */
    minJaccard: number;
  };
  /** 模板文本的遮蔽开关：数字遮成 #，单个拉丁字母变量遮成 @。待教研确认 */
  mask: { numbers: boolean; letters: boolean };
  /**
   * 同卷识别（宁严勿松）：只认规范文本 SHA-256 一致的题；下列条件全部满足才判“同卷”。
   * 近似匹配（两道门）只用于判“相似卷”（不复用解析）。待教研确认
   */
  paper: {
    /** 两卷题数之差的上限（含） */
    maxCountDiff: number;
    /** 按序完全一致的题数 ÷ 两卷中较大的题数，下限（含） */
    minOrderedRatio: number;
    /** 首题、尾题都必须完全一致 */
    requireFirstLast: boolean;
    /** 完全一致却不在按序匹配里的题（换序）数上限（含）；0 = 任何换序都不判同卷 */
    maxOutOfOrder: number;
    /** 部分命中：按序匹配（含近似）占比达到这个下限（含）才报“相似卷”；低于它不报 */
    similarMinRatio: number;
  };
  /** 资源上限（非教研阈值） */
  limits: {
    /** 近似对总数上限；超出即报错退出，不截断 */
    maxNearPairs: number;
  };
}

export const DEFAULT_CONFIG: DedupeConfig = {
  ngram: 3, // 待教研确认
  // 待教研确认。合成文本上的实测：30 字左右的题单字改动，汉明距离 1–18（多数 ≤ 12），Jaccard ≥ 0.79；无关文本距离约 20–45、Jaccard < 0.1。
  // 短文本的 SimHash 噪声大，门一只作粗筛（12），把关主要靠门二（Jaccard 0.7）
  near: { maxHamming: 12, minJaccard: 0.7 },
  mask: { numbers: true, letters: true }, // 待教研确认
  paper: { maxCountDiff: 1, minOrderedRatio: 0.9, requireFirstLast: true, maxOutOfOrder: 0, similarMinRatio: 0.3 }, // 待教研确认
  limits: { maxNearPairs: 50000 },
};
