// 道法导入器用的 YAML 子集解析器（冲刺 4-K，specs/kge-daofa-slice-sprint4/spec.md 设计要点 1、4）。
// 在 src/yaml.ts（只认块状映射）的基础上多认两种写法，其余仍报错：
//   - 块状列表：`- 标量`、`- 键: 值`（列表项是映射，后续键与第一个键对齐，即缩进 = 横线缩进 + 2）；
//   - 单行流式列表：`[a, b, "c d"]`（元素只能是标量，不能嵌套）。
// 不支持锚点、多行字符串、流式映射、制表符缩进。不引入 YAML 依赖（CLAUDE.md 规则 10；KGE-01 lessons 第 3 条）。
// 数字只认十进制整数与小数；日期（2026-09-01）按字符串返回，由调用方校验格式。

export type DocValue = string | number | boolean | null | DocValue[] | DocMap;
export interface DocMap { [k: string]: DocValue }

export class YamlDocError extends Error {}

interface Line { indent: number; text: string; no: number }

const KEY = /^[A-Za-z0-9_\-一-龥]{1,64}$/;
const MAPPING_START = /^([A-Za-z0-9_\-一-龥]{1,64}):(\s|$)/;

function stripComment(line: string): string {
  let quote: string | null = null;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i]!;
    if (quote) {
      if (ch === quote) quote = null;
      continue;
    }
    if (ch === '"' || ch === "'") quote = ch;
    else if (ch === "#" && (i === 0 || /\s/.test(line[i - 1]!))) return line.slice(0, i);
  }
  return line;
}

function unquote(v: string, where: string): string | null {
  if (v.length >= 2 && ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'")))) {
    const inner = v.slice(1, -1);
    if (v.startsWith('"')) {
      if (/\\/.test(inner)) throw new YamlDocError(`${where}：双引号字符串里不支持转义`);
      if (inner.includes('"')) throw new YamlDocError(`${where}：双引号字符串没有正确结束`);
      return inner;
    }
    if (/'(?!')/.test(inner.replace(/''/g, ""))) throw new YamlDocError(`${where}：单引号字符串没有正确结束`);
    return inner.replace(/''/g, "'");
  }
  return null;
}

function scalar(raw: string, where: string): string | number | boolean | null {
  const v = raw.trim();
  if (v === "" || v === "~" || v === "null") return null;
  if (v === "true") return true;
  if (v === "false") return false;
  if (/^-?\d{1,15}(\.\d{1,15})?$/.test(v)) return Number(v);
  const q = unquote(v, where);
  if (q !== null) return q;
  if (/^[[\]{}&*!|>@`%"']/.test(v) || v.startsWith("- ") || v === "-") {
    throw new YamlDocError(`${where}：不支持的 YAML 写法“${v.slice(0, 16)}”`);
  }
  if (/:\s/.test(v)) throw new YamlDocError(`${where}：值里的“: ”需要加引号`);
  return v;
}

/** 单行流式列表：只认标量元素；逗号在引号里不断开 */
function flowList(raw: string, where: string): DocValue[] {
  const v = raw.trim();
  const body = v.slice(1, -1);
  if (/[[\]{}]/.test(body.replace(/"[^"]*"|'(?:[^']|'')*'/g, ""))) throw new YamlDocError(`${where}：流式列表不能嵌套`);
  if (body.trim() === "") return [];
  const out: DocValue[] = [];
  let cur = "";
  let quote: string | null = null;
  for (const ch of body) {
    if (quote) {
      cur += ch;
      if (ch === quote) quote = null;
      continue;
    }
    if (ch === '"' || ch === "'") { quote = ch; cur += ch; continue; }
    if (ch === ",") {
      if (cur.trim() === "") throw new YamlDocError(`${where}：流式列表里有空元素`);
      out.push(scalar(cur, where));
      cur = "";
      continue;
    }
    cur += ch;
  }
  if (quote) throw new YamlDocError(`${where}：流式列表里的引号没有闭合`);
  if (cur.trim() === "") throw new YamlDocError(`${where}：流式列表里有空元素`);
  out.push(scalar(cur, where));
  return out;
}

function value(raw: string, where: string): DocValue {
  const v = raw.trim();
  if (v.startsWith("[")) {
    if (!v.endsWith("]")) throw new YamlDocError(`${where}：流式列表必须写在一行里`);
    return flowList(v, where);
  }
  return scalar(v, where);
}

function lex(text: string): Line[] {
  const out: Line[] = [];
  text.split(/\r?\n/).forEach((orig, i) => {
    const where = `第 ${i + 1} 行`;
    if (/\t/.test(orig)) throw new YamlDocError(`${where}：不能用制表符`);
    const line = stripComment(orig).replace(/\s+$/, "");
    if (line.trim() === "") return;
    if (line.trim() === "---" && out.length === 0) return;
    const indent = line.length - line.trimStart().length;
    if (indent % 2 !== 0) throw new YamlDocError(`${where}：缩进必须是 2 的倍数`);
    out.push({ indent, text: line.trim(), no: i + 1 });
  });
  return out;
}

const isSeq = (l: Line) => l.text === "-" || l.text.startsWith("- ");

class Parser {
  private lines: Line[];
  private i = 0;
  constructor(lines: Line[]) {
    this.lines = lines;
  }

  parse(): DocMap {
    if (this.lines.length === 0) return {};
    const first = this.lines[0]!;
    if (first.indent !== 0 || isSeq(first)) throw new YamlDocError(`第 ${first.no} 行：文档顶层必须是映射`);
    const v = this.mapping(0);
    if (this.i < this.lines.length) throw new YamlDocError(`第 ${this.lines[this.i]!.no} 行：缩进层级不对`);
    return v;
  }

  private block(indent: number): DocValue {
    const l = this.lines[this.i];
    if (!l || l.indent !== indent) throw new YamlDocError(`第 ${l?.no ?? "末"} 行：缩进层级不对（应为 ${indent} 个空格）`);
    return isSeq(l) ? this.sequence(indent) : this.mapping(indent);
  }

  private mapping(indent: number): DocMap {
    const out: DocMap = {};
    while (this.i < this.lines.length) {
      const l = this.lines[this.i]!;
      if (l.indent < indent) break;
      if (l.indent > indent) throw new YamlDocError(`第 ${l.no} 行：缩进层级不对`);
      if (isSeq(l)) throw new YamlDocError(`第 ${l.no} 行：映射里不能直接出现列表项`);
      const where = `第 ${l.no} 行`;
      const colon = l.text.indexOf(":");
      if (colon <= 0) throw new YamlDocError(`${where}：应为“键: 值”`);
      const key = l.text.slice(0, colon).trim();
      if (!KEY.test(key)) throw new YamlDocError(`${where}：键“${key}”不合法`);
      const rest = l.text.slice(colon + 1);
      if (rest !== "" && !rest.startsWith(" ")) throw new YamlDocError(`${where}：冒号后要有空格`);
      if (Object.hasOwn(out, key)) throw new YamlDocError(`${where}：键“${key}”重复`);
      this.i++;
      if (rest.trim() === "") {
        const next = this.lines[this.i];
        out[key] = next && next.indent > indent ? this.block(indent + 2) : null;
      } else {
        out[key] = value(rest, where);
      }
    }
    return out;
  }

  private sequence(indent: number): DocValue[] {
    const out: DocValue[] = [];
    while (this.i < this.lines.length) {
      const l = this.lines[this.i]!;
      if (l.indent < indent) break;
      if (l.indent > indent || !isSeq(l)) throw new YamlDocError(`第 ${l.no} 行：列表里的缩进层级不对`);
      const rest = l.text === "-" ? "" : l.text.slice(2).trim();
      if (rest === "") {
        this.i++;
        const next = this.lines[this.i];
        out.push(next && next.indent > indent ? this.block(indent + 2) : null);
      } else if (MAPPING_START.test(rest) && unquote(rest, "") === null) {
        // “- 键: 值”：把这一行改写成缩进 +2 的映射首行，后续键与它对齐
        this.lines[this.i] = { indent: indent + 2, text: rest, no: l.no };
        out.push(this.mapping(indent + 2));
      } else {
        this.i++;
        out.push(value(rest, `第 ${l.no} 行`));
      }
    }
    return out;
  }
}

export function parseYamlDoc(text: string): DocMap {
  return new Parser(lex(text)).parse();
}
