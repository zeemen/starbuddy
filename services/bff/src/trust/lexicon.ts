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

export function parseLexicon(text: string): Lexicon {
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
  if (categories.length < MIN_CATEGORIES || new Set(categories.map((c) => c.id)).size !== categories.length
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

/** 命中的类别 ID（空 = 没有命中） */
export function forbiddenHits(text: string, lex: Lexicon = lexicon()): string[] {
  const norm = normalize(text);
  return lex.categories.filter((c) => c.patterns.some((p) => p.test(text) || p.test(norm))).map((c) => c.id);
}
