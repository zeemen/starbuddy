// 复制自 legacy/aya/lib/symbolic.ts（ayaheshui 提交 0ed2fe4）。legacy 只读，本文件是副本。
// 改动：Parser 的构造参数属性改为显式字段（Node 类型擦除不支持参数属性）；新增导出 sampleCompare（返回采样点数与
// 一致点数，供验证器 v0 给出置信度与原因码），areEquivalent 改为调用它，判定结果不变；新增导出 evalConstant（常量求值）。
/**
 * 轻量符号验证引擎（设计方案 11.2「可信数学验证系统」的数值验证工具）。
 *
 * 定位：LLM 负责启发对话，符号/数值引擎负责验证——杜绝把等价的正确写法误判为错。
 * 方法：解析中学代数表达式为可求值 AST，在若干确定性采样点求值，全部一致（容差内）
 *       即判等价（多项式恒等测试的数值实现）。零依赖，不用 Math.random（会破坏可复现）。
 *
 * 支持：+ - * / ^、括号、隐式乘（2x、3(x+1)、(x+1)(x-2)、2x^2）、一元负号、
 *       单字母变量、分数/小数、√（sqrt）。不支持：三角函数、多字母函数名。
 */

type Node =
  | { t: "num"; v: number }
  | { t: "var"; name: string }
  | { t: "neg"; a: Node }
  | { t: "add"; a: Node; b: Node }
  | { t: "sub"; a: Node; b: Node }
  | { t: "mul"; a: Node; b: Node }
  | { t: "div"; a: Node; b: Node }
  | { t: "pow"; a: Node; b: Node }
  | { t: "sqrt"; a: Node };

/** 预处理：统一符号、去空白与单位/前缀，Unicode 数学符号转 ASCII */
export function normalizeExpr(raw: string): string {
  return raw
    .trim()
    .replace(/\s+/g, "")
    .replace(/，/g, ",")
    .replace(/（/g, "(")
    .replace(/）/g, ")")
    .replace(/×|·|∙/g, "*")
    .replace(/÷/g, "/")
    .replace(/[−–—]/g, "-")
    .replace(/²/g, "^2")
    .replace(/³/g, "^3")
    .replace(/√/g, "sqrt")
    .replace(/^[xyztmnk]=/, "");
}

// —— 词法 —— //
type Tok =
  | { k: "num"; v: number }
  | { k: "var"; name: string }
  | { k: "op"; v: string }
  | { k: "lp" }
  | { k: "rp" }
  | { k: "sqrt" };

function tokenize(s: string): Tok[] {
  const toks: Tok[] = [];
  let i = 0;
  while (i < s.length) {
    const c = s[i];
    if (c >= "0" && c <= "9") {
      let j = i;
      while (j < s.length && ((s[j] >= "0" && s[j] <= "9") || s[j] === ".")) j++;
      toks.push({ k: "num", v: Number(s.slice(i, j)) });
      i = j;
    } else if (s.startsWith("sqrt", i)) {
      toks.push({ k: "sqrt" });
      i += 4;
    } else if (/[a-z]/.test(c)) {
      toks.push({ k: "var", name: c });
      i++;
    } else if ("+-*/^".includes(c)) {
      toks.push({ k: "op", v: c });
      i++;
    } else if (c === "(") {
      toks.push({ k: "lp" });
      i++;
    } else if (c === ")") {
      toks.push({ k: "rp" });
      i++;
    } else {
      throw new Error(`非法字符: ${c}`);
    }
  }
  return toks;
}

// —— 语法（递归下降）——
// expr := term (('+'|'-') term)*
// term := factor (('*'|'/'| implicit) factor)*
// factor := ('-'|'+') factor | power
// power := atom ('^' factor)?
// atom := num | var | 'sqrt' atom | '(' expr ')'
class Parser {
  private p = 0;
  private toks: Tok[];
  constructor(toks: Tok[]) {
    this.toks = toks;
  }
  parse(): Node {
    const n = this.expr();
    if (this.p !== this.toks.length) throw new Error("解析未消费完");
    return n;
  }
  private peek(): Tok | undefined {
    return this.toks[this.p];
  }
  private expr(): Node {
    let a = this.term();
    for (;;) {
      const t = this.peek();
      if (t?.k === "op" && (t.v === "+" || t.v === "-")) {
        this.p++;
        const b = this.term();
        a = t.v === "+" ? { t: "add", a, b } : { t: "sub", a, b };
      } else break;
    }
    return a;
  }
  private term(): Node {
    let a = this.factor();
    for (;;) {
      const t = this.peek();
      if (t?.k === "op" && (t.v === "*" || t.v === "/")) {
        this.p++;
        const b = this.factor();
        a = t.v === "*" ? { t: "mul", a, b } : { t: "div", a, b };
      } else if (t && (t.k === "num" || t.k === "var" || t.k === "lp" || t.k === "sqrt")) {
        // 隐式乘：2x, 3(x+1), (x+1)(x-2), 2sqrt(x)
        const b = this.factor();
        a = { t: "mul", a, b };
      } else break;
    }
    return a;
  }
  private factor(): Node {
    const t = this.peek();
    if (t?.k === "op" && (t.v === "-" || t.v === "+")) {
      this.p++;
      const a = this.factor();
      return t.v === "-" ? { t: "neg", a } : a;
    }
    return this.power();
  }
  private power(): Node {
    const base = this.atom();
    const t = this.peek();
    if (t?.k === "op" && t.v === "^") {
      this.p++;
      const exp = this.factor(); // 右结合 + 允许 2^-1
      return { t: "pow", a: base, b: exp };
    }
    return base;
  }
  private atom(): Node {
    const t = this.peek();
    if (!t) throw new Error("表达式意外结束");
    if (t.k === "num") {
      this.p++;
      return { t: "num", v: t.v };
    }
    if (t.k === "var") {
      this.p++;
      return { t: "var", name: t.name };
    }
    if (t.k === "sqrt") {
      this.p++;
      return { t: "sqrt", a: this.atom() };
    }
    if (t.k === "lp") {
      this.p++;
      const n = this.expr();
      if (this.peek()?.k !== "rp") throw new Error("缺少右括号");
      this.p++;
      return n;
    }
    throw new Error("非法原子");
  }
}

export function parseExpr(raw: string): Node {
  return new Parser(tokenize(normalizeExpr(raw))).parse();
}

function evalNode(n: Node, env: Record<string, number>): number {
  switch (n.t) {
    case "num":
      return n.v;
    case "var":
      return env[n.name] ?? NaN;
    case "neg":
      return -evalNode(n.a, env);
    case "add":
      return evalNode(n.a, env) + evalNode(n.b, env);
    case "sub":
      return evalNode(n.a, env) - evalNode(n.b, env);
    case "mul":
      return evalNode(n.a, env) * evalNode(n.b, env);
    case "div":
      return evalNode(n.a, env) / evalNode(n.b, env);
    case "pow":
      return Math.pow(evalNode(n.a, env), evalNode(n.b, env));
    case "sqrt":
      return Math.sqrt(evalNode(n.a, env));
  }
}

function collectVars(n: Node, out: Set<string>): void {
  switch (n.t) {
    case "var":
      out.add(n.name);
      break;
    case "num":
      break;
    case "neg":
    case "sqrt":
      collectVars(n.a, out);
      break;
    default:
      collectVars(n.a, out);
      collectVars(n.b, out);
  }
}

// 确定性采样点：无理、避开常见根与极点（0/整数），保证多项式恒等测试可靠
const SAMPLES = [0.3137, 1.4142, 2.7182, -1.1235, 3.6180, 0.7071, -2.3025, 4.1231];
const TOL = 1e-6;

/**
 * 判两个表达式是否恒等（数值采样）。
 * 保守策略：有效采样点少于 4 个时返回 false，避免假阳性。
 */
export function areEquivalent(exprA: string, exprB: string): boolean {
  let r: SampleComparison;
  try {
    r = sampleCompare(exprA, exprB);
  } catch {
    return false;
  }
  return r.disagreeing === 0 && r.agreeing >= 4;
}

/** 采样比对结果（StarBuddy 新增）：采样点数、两边都有限且一致的点数、有限但不一致的点数 */
export interface SampleComparison {
  sampled: number;
  agreeing: number;
  disagreeing: number;
}

/**
 * 在全部确定性采样点上比对两个表达式（StarBuddy 新增，从原 areEquivalent 拆出）。
 * 与原实现的区别只在于不在第一处不等时提前返回，而是数完全部采样点。任一表达式无法解析时抛出异常。
 */
export function sampleCompare(exprA: string, exprB: string): SampleComparison {
  const a = parseExpr(exprA);
  const b = parseExpr(exprB);
  const vars = new Set<string>();
  collectVars(a, vars);
  collectVars(b, vars);
  const varList = [...vars];

  let agree = 0;
  let disagree = 0;
  for (const s of SAMPLES) {
    const env: Record<string, number> = {};
    // 多变量时用不同偏移，避免所有变量取同值造成的偶然相等
    varList.forEach((v, idx) => {
      env[v] = s + idx * 0.911;
    });
    const va = evalNode(a, env);
    const vb = evalNode(b, env);
    if (!Number.isFinite(va) || !Number.isFinite(vb)) continue; // 跳过极点/负数开方
    const scale = Math.max(1, Math.abs(va), Math.abs(vb));
    if (Math.abs(va - vb) > TOL * scale) disagree++;
    else agree++;
  }
  return { sampled: SAMPLES.length, agreeing: agree, disagreeing: disagree };
}

/** 表达式是否含变量（用于判分时决定是否走符号路径） */
export function isAlgebraic(raw: string): boolean {
  try {
    const vars = new Set<string>();
    collectVars(parseExpr(raw), vars);
    return vars.size > 0;
  } catch {
    return false;
  }
}

/** 解析并返回变量集（排序）；不可解析返回 null。用于教练等式校验区分恒等式与赋值。 */
export function exprVars(raw: string): string[] | null {
  try {
    const vars = new Set<string>();
    collectVars(parseExpr(raw), vars);
    return [...vars].sort();
  } catch {
    return null;
  }
}

/** 不含变量的表达式求值（StarBuddy 新增）：含变量、无法解析或结果不是有限数时返回 null。 */
export function evalConstant(raw: string): number | null {
  try {
    const n = parseExpr(raw);
    const vars = new Set<string>();
    collectVars(n, vars);
    if (vars.size > 0) return null;
    const v = evalNode(n, {});
    return Number.isFinite(v) ? v : null;
  } catch {
    return null;
  }
}
