// 极小的 YAML 子集解析器：只认块状映射（两个空格缩进）、`键: 值` 标量、`#` 注释。
// 只用于本工具自己的配置文件（config/*.yaml）；不支持列表、锚点、多行字符串、流式写法，遇到就报错。
// 不引入 YAML 依赖（CLAUDE.md 规则 10：引入依赖前查许可证；KGE-01 lessons 第 3 条同样没有引入）。

export type YamlValue = string | number | boolean | null | YamlMap;
export interface YamlMap { [k: string]: YamlValue }

export class YamlError extends Error {}

const KEY = /^[A-Za-z0-9_\-一-龥]{1,64}$/;

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

function scalar(raw: string, where: string): YamlValue {
  const v = raw.trim();
  if (v === "" || v === "~" || v === "null") return null;
  if (v === "true") return true;
  if (v === "false") return false;
  if (/^-?\d+$/.test(v)) return Number(v);
  if ((v.startsWith('"') && v.endsWith('"') && v.length >= 2) || (v.startsWith("'") && v.endsWith("'") && v.length >= 2)) {
    const inner = v.slice(1, -1);
    if (v.startsWith('"') && /\\/.test(inner)) throw new YamlError(`${where}：双引号字符串里不支持转义`);
    return v.startsWith("'") ? inner.replace(/''/g, "'") : inner;
  }
  if (/^[[{&*!|>@`-]/.test(v)) throw new YamlError(`${where}：不支持的 YAML 写法“${v.slice(0, 16)}”（本解析器只认标量与块状映射）`);
  return v;
}

export function parseYaml(text: string): YamlMap {
  const root: YamlMap = {};
  const stack: { indent: number; map: YamlMap }[] = [{ indent: -1, map: root }];
  const lines = text.split(/\r?\n/);
  lines.forEach((orig, i) => {
    const where = `第 ${i + 1} 行`;
    if (/\t/.test(orig)) throw new YamlError(`${where}：不能用制表符缩进`);
    const line = stripComment(orig).replace(/\s+$/, "");
    if (line.trim() === "") return;
    const indent = line.length - line.trimStart().length;
    if (indent % 2 !== 0) throw new YamlError(`${where}：缩进必须是 2 的倍数`);
    const body = line.trim();
    const colon = body.indexOf(":");
    if (colon <= 0) throw new YamlError(`${where}：应为“键: 值”`);
    const key = body.slice(0, colon).trim();
    if (!KEY.test(key)) throw new YamlError(`${where}：键“${key}”不合法`);
    const rest = body.slice(colon + 1);
    if (rest !== "" && !rest.startsWith(" ")) throw new YamlError(`${where}：冒号后要有空格`);
    while (stack.length > 1 && stack[stack.length - 1]!.indent >= indent) stack.pop();
    const parent = stack[stack.length - 1]!;
    if (indent !== parent.indent + 2 && !(parent.indent === -1 && indent === 0)) throw new YamlError(`${where}：缩进层级不对`);
    if (Object.hasOwn(parent.map, key)) throw new YamlError(`${where}：键“${key}”重复`);
    if (rest.trim() === "") {
      const child: YamlMap = {};
      parent.map[key] = child;
      stack.push({ indent, map: child });
    } else {
      parent.map[key] = scalar(rest, where);
    }
  });
  return root;
}
