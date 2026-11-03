// 禁用推断词表的 BFF 端最小匹配（冲刺 5-H 周报；安全审查 F1）：周报句子在转给前端之前再过一次词表，命中即丢弃。
// 词表只有一个真相源：docs/design/sie/forbidden-inference.v0.yaml（SIE-00，Python 端 docs/design/sie/sie_lexicon.py 同一份）。
// 这里不引入 YAML 依赖：只按这份文件的固定写法取每类的 patterns 与 forbidden_examples、以及全局 allowed_examples；
// 规范化与 sie_lexicon.normalize 一致（NFKC、去空白与零宽字符、常用繁体转简体——对照表直接读 sie_lexicon.py），原文与规范化文本各匹配一次。
// 读不到或解析不出至少十类时抛错：调用方按“不显示”处理（fail closed）。部署时 BFF 的制品要带上这份词表（lessons 登记）。
import { readFileSync } from 'node:fs';

export const LEXICON_PATH = new URL('../../../../docs/design/sie/forbidden-inference.v0.yaml', import.meta.url);

export const LEXICON_PY_PATH = new URL('../../../../docs/design/sie/sie_lexicon.py', import.meta.url);
/** 繁简对照：直接取 sie_lexicon.py 的 _T2S（同一个出处，不在这里抄一份） */
export function parseT2S(py: string): Map<string, string> {
  const m = /_T2S = str\.maketrans\("([^"]+)", "([^"]+)"\)/.exec(py);
  if (!m || [...m[1]!].length !== [...m[2]!].length) throw new Error('sie_lexicon.py 里找不到繁简对照');
  const to = [...m[2]!];
  return new Map([...m[1]!].map((c, i) => [c, to[i]!]));
}
let t2s: Map<string, string> | null = null;
const t2sMap = (): Map<string, string> => (t2s ??= parseT2S(readFileSync(LEXICON_PY_PATH, 'utf8')));
const INVISIBLE = /[\s\u200b-\u200f\u2060\ufeff]+/gu;

/** 词表至少有这么多类（类目是否齐全由 make sie-dictionary-check 按 sie_lexicon.REQUIRED_CATEGORIES 核对；这里不写类目名，免得类目名本身被词表扫描命中） */
export const MIN_CATEGORIES = 10;

export interface Lexicon {
  categories: { id: string; patterns: RegExp[]; forbiddenExamples: string[] }[];
  allowedExamples: string[];
}

export function normalize(text: string): string {
  const map = t2sMap();
  return [...text.normalize('NFKC').replace(INVISIBLE, '')].map((c) => map.get(c) ?? c).join('');
}

const scalar = (raw: string): string => {
  const v = raw.trim();
  return v.startsWith('"') ? (JSON.parse(v) as string) : v;
};

/** minCategories：禁用推断词表至少十类（缺省）；“名次 / 概率 / 承诺”词表三类（PLT-14 第 3 步，noPromiseLexicon） */
export function parseLexicon(text: string, minCategories: number = MIN_CATEGORIES): Lexicon {
  const categories: Lexicon['categories'] = [];
  const allowedExamples: string[] = [];
  let list: string[] | null = null;
  let patterns: string[] = [];
  for (const line of text.split('\n')) {
    if (/^\s*(#.*)?$/.test(line)) continue;
    let m = /^ {2}- id: (\S+)\s*$/.exec(line);
    if (m) {
      patterns = [];
      categories.push({ id: m[1]!, patterns: [], forbiddenExamples: [] });
      list = null;
      continue;
    }
    m = /^ {4}(patterns|forbidden_examples|allowed_examples):\s*(\[\])?\s*$/.exec(line);
    if (m && categories.length > 0) {
      const cat = categories[categories.length - 1]!;
      if (m[1] === 'patterns') {
        patterns = [];
        list = patterns;
        cat.patterns = [];
      } else {
        list = m[1] === 'forbidden_examples' ? cat.forbiddenExamples : allowedExamples;
      }
      continue;
    }
    if (/^allowed_examples:\s*$/.test(line)) {
      list = allowedExamples;
      continue;
    }
    if (/^\S/.test(line)) {
      list = null;
      continue;
    }
    m = /^ +- (.+)$/.exec(line);
    if (m && list) {
      list.push(scalar(m[1]!));
      if (list === patterns) categories[categories.length - 1]!.patterns.push(new RegExp(scalar(m[1]!), 'i'));
    }
  }
  if (categories.length < minCategories || new Set(categories.map((c) => c.id)).size !== categories.length
      || categories.some((c) => c.patterns.length === 0)) {
    throw new Error('禁用推断词表不完整：检查不能缺席');
  }
  return { categories, allowedExamples };
}

let cached: Lexicon | null = null;
export function lexicon(): Lexicon {
  if (!cached) {
    t2sMap();  // 繁简对照也要读得到，否则一样 fail closed
    cached = parseLexicon(readFileSync(LEXICON_PATH, 'utf8'));
  }
  return cached;
}

// “名次 / 概率 / 承诺”最小词表（草稿，PLT-14 第 2 步新增；docs/design/sie/no-promise.v0.draft.yaml）：与禁用推断词表同一格式、
// 同一个解析与匹配（同一套规范化）。月度计划的成品句子两张表都要过（第 3 步）。读不到、少于三类、或自检不过时抛错（fail closed）：
// 自检 = 每类的禁止示例被本类命中、全局允许示例不被任何一类命中（防止 JS 与 Python 的正则语义差异让词表在这里失效）。
export const NO_PROMISE_PATH = new URL('../../../../docs/design/sie/no-promise.v0.draft.yaml', import.meta.url);
export const NO_PROMISE_MIN_CATEGORIES = 3;

export function checkNoPromise(lex: Lexicon): Lexicon {
  for (const c of lex.categories) {
    if (c.forbiddenExamples.length === 0) throw new Error('“名次 / 概率 / 承诺”词表有类别没有禁止示例');
    for (const e of c.forbiddenExamples) {
      if (!forbiddenHits(e, lex).includes(c.id)) throw new Error('“名次 / 概率 / 承诺”词表的禁止示例没有被本类命中');
    }
  }
  if (lex.allowedExamples.length === 0 || lex.allowedExamples.some((e) => forbiddenHits(e, lex).length > 0)) {
    throw new Error('“名次 / 概率 / 承诺”词表的允许示例被误伤');
  }
  return lex;
}

let noPromiseCached: Lexicon | null = null;
export function noPromiseLexicon(): Lexicon {
  if (!noPromiseCached) {
    t2sMap();
    noPromiseCached = checkNoPromise(parseLexicon(readFileSync(NO_PROMISE_PATH, 'utf8'), NO_PROMISE_MIN_CATEGORIES));
  }
  return noPromiseCached;
}

/** 命中的类别 ID（空 = 没有命中） */
export function forbiddenHits(text: string, lex: Lexicon = lexicon()): string[] {
  const norm = normalize(text);
  return lex.categories.filter((c) => c.patterns.some((p) => p.test(text) || p.test(norm))).map((c) => c.id);
}
