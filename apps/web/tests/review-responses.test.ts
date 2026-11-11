// 审核台前后端响应类型核对（specs/review-console-responses 第二节第 4 条）：用 TypeScript 编译器 API 读 src/lib/review.ts 的 ReviewResponses，
// 与后端响应模型导出的 src/lib/review-api.responses.json 逐层递归比较——
//   接口集合相同；每层对象的字段集合相同；必填（前端不带 ?）与后端 required 一致；可空（前端带 | null）与后端 nullable 一致；
//   标量种类一致（integer / number 都对 number）；数组比元素；map（dict[str, T]）对 Record<string, T>；
//   枚举：后端有枚举时前端要么是宽泛的 string，要么是取值完全相同的字面量联合（响应是前端接收，取值少了就漏处理，多了是过期）；
//   后端没有枚举时前端不能写成字面量联合（前端以为窄，后端并不保证）。
//   后端 any（差异的旧值 / 新值、变更单签字内容、复核任务来源快照）不往下比。
// 另核对：所有调用都经 send() / fetchRoute()，路径与路由模板对得上；不再有 call<T>() 直接调用。
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import ts from 'typescript';

const WEB = new URL('..', import.meta.url).pathname;
// 变异测试（tests/review-responses-mutations.test.ts）经这两个变量指到改坏的副本；平时就是仓库里的文件
const SRC = process.env['REVIEW_RESPONSES_SRC'] ?? `${WEB}src/lib/review.ts`;
const JSON_PATH = process.env['REVIEW_RESPONSES_JSON'] ?? `${WEB}src/lib/review-api.responses.json`;

interface Node {
  type: 'string' | 'integer' | 'number' | 'boolean' | 'array' | 'object' | 'map' | 'any';
  nullable: boolean;
  enum: string[] | null;
  items?: Node;
  values?: Node;
  fields?: Record<string, Node & { required: boolean }>;
}
const BACKEND = (JSON.parse(readFileSync(JSON_PATH, 'utf8')) as { routes: Record<string, Node> }).routes;

function program(): { checker: ts.TypeChecker; decl: ts.InterfaceDeclaration } {
  const p = ts.createProgram([SRC], { strict: true, noEmit: true, allowImportingTsExtensions: true, target: ts.ScriptTarget.ES2022,
                                      module: ts.ModuleKind.ESNext, moduleResolution: ts.ModuleResolutionKind.Bundler });
  const sf = p.getSourceFile(SRC)!;
  let decl: ts.InterfaceDeclaration | undefined;
  ts.forEachChild(sf, (n) => { if (ts.isInterfaceDeclaration(n) && n.name.text === 'ReviewResponses') decl = n; });
  assert.ok(decl, 'review.ts 里没有 ReviewResponses');
  return { checker: p.getTypeChecker(), decl: decl! };
}

/** 比较一个前端类型与后端节点，把每处不一致写进 out */
function compare(checker: ts.TypeChecker, t: ts.Type, b: Node, where: string, out: string[]): void {
  const parts = t.isUnion() ? t.types : [t];
  const hasNull = parts.some((x) => x.flags & ts.TypeFlags.Null);
  const core = parts.filter((x) => !(x.flags & (ts.TypeFlags.Null | ts.TypeFlags.Undefined)));
  if (b.type === 'any') return;  // 自由结构：不往下比
  if (hasNull !== b.nullable) out.push(`${where}：可空不一致（后端 nullable=${b.nullable}）`);
  if (core.length === 0) { out.push(`${where}：前端只有 null`); return; }
  if (core.some((x) => x.flags & (ts.TypeFlags.Any | ts.TypeFlags.Unknown))) {
    out.push(`${where}：前端是 any / unknown，后端是 ${b.type}`);
    return;
  }
  const literals = core.every((x) => x.isStringLiteral()) ? core.map((x) => (x as ts.StringLiteralType).value).sort() : null;
  const isString = literals !== null || (core.length === 1 && (core[0]!.flags & ts.TypeFlags.String) !== 0);
  const isNumber = core.length === 1 && (core[0]!.flags & (ts.TypeFlags.Number | ts.TypeFlags.NumberLiteral)) !== 0;
  const isBool = core.every((x) => x.flags & (ts.TypeFlags.Boolean | ts.TypeFlags.BooleanLiteral));
  switch (b.type) {
    case 'string':
      if (!isString) { out.push(`${where}：后端是字符串，前端是 ${checker.typeToString(t)}`); return; }
      if (b.enum && literals && JSON.stringify(literals) !== JSON.stringify(b.enum)) {
        out.push(`${where}：枚举不同（后端 ${b.enum.join('、')}；前端 ${literals.join('、')}）`);
      }
      if (!b.enum && literals) out.push(`${where}：后端没有枚举（宽泛的字符串），前端写成了字面量联合 ${literals.join('、')}`);
      return;
    case 'integer':
    case 'number':
      if (!isNumber) out.push(`${where}：后端是数字，前端是 ${checker.typeToString(t)}`);
      return;
    case 'boolean':
      if (!isBool) out.push(`${where}：后端是布尔，前端是 ${checker.typeToString(t)}`);
      return;
    case 'array': {
      const c = core[0]!;
      if (core.length !== 1 || !checker.isArrayType(c)) { out.push(`${where}：后端是数组，前端是 ${checker.typeToString(t)}`); return; }
      compare(checker, checker.getTypeArguments(c as ts.TypeReference)[0]!, b.items!, `${where}[]`, out);
      return;
    }
    case 'map': {
      const c = core[0]!;
      const idx = core.length === 1 ? checker.getIndexInfoOfType(c, ts.IndexKind.String) : undefined;
      if (!idx || checker.getPropertiesOfType(c).length > 0) { out.push(`${where}：后端是 map，前端应为 Record<string, …>，现为 ${checker.typeToString(t)}`); return; }
      compare(checker, idx.type, b.values!, `${where}{}`, out);
      return;
    }
    case 'object': {
      if (core.length !== 1 || !(core[0]!.flags & ts.TypeFlags.Object) || checker.isArrayType(core[0]!)) {
        out.push(`${where}：后端是对象，前端是 ${checker.typeToString(t)}`);
        return;
      }
      const props = checker.getPropertiesOfType(core[0]!);
      const front = new Set(props.map((p) => p.name));
      const back = new Set(Object.keys(b.fields!));
      for (const n of back) if (!front.has(n)) out.push(`${where}.${n}：前端缺这个字段`);
      for (const n of front) if (!back.has(n)) out.push(`${where}.${n}：后端没有这个字段`);
      for (const p of props) {
        const f = b.fields![p.name];
        if (!f) continue;
        const optional = (p.flags & ts.SymbolFlags.Optional) !== 0;
        if (optional === f.required) out.push(`${where}.${p.name}：必填不一致（后端 required=${f.required}）`);
        compare(checker, checker.getTypeOfSymbol(p), f, `${where}.${p.name}`, out);  // 可选带来的 undefined 在 compare 里去掉
      }
      return;
    }
  }
}

function differences(): string[] {
  const { checker, decl } = program();
  const out: string[] = [];
  const seen = new Set<string>();
  for (const m of decl.members) {
    assert.ok(ts.isPropertySignature(m) && m.type && ts.isStringLiteral(m.name), 'ReviewResponses 的键必须是字符串字面量');
    const key = (m.name as ts.StringLiteral).text;
    seen.add(key);
    const b = BACKEND[key];
    if (!b) { out.push(`${key}：后端没有这个接口`); continue; }
    compare(checker, checker.getTypeAtLocation(m.type!), b, key, out);
  }
  for (const k of Object.keys(BACKEND)) if (!seen.has(k)) out.push(`${k}：ReviewResponses 没有登记`);
  return out;
}

test('ReviewResponses 与后端响应模型逐层一致：接口、字段、必填、可空、类型、枚举', () => {
  const diff = differences();
  assert.deepEqual(diff, [], `前端响应类型与后端响应模型有 ${diff.length} 处不一致：\n${diff.join('\n')}`);
});

test('调用都经 send() / fetchRoute()，路径与路由模板对得上；不再直接用 call<T>()', () => {
  const src = readFileSync(SRC, 'utf8');
  const uses = [...src.matchAll(/\b(?:send|fetchRoute)\(\s*(?:staff|null|session),\s*'([A-Z]+ [^']+)',\s*(`(?:[^`$]|\$\{(?:[^{}`]|\{[^{}]*\}|`[^`]*`)*\})*`|'[^']*')/g)];
  const used = new Set<string>();
  for (const [, route, rawPath] of uses) {
    assert.ok(route! in BACKEND, `调用了后端没有的路由：${route}`);
    used.add(route!);
    const template = route!.slice(route!.indexOf(' ') + 1).replace(/\{[^}]+\}/g, '{}');
    // 去掉查询串（含 ${…} 里拼的查询串），参数统一成 {}
    const path = rawPath!.slice(1, -1)
      .replace(/\$\{[^{}]*`\?(?:[^`]|\$\{[^}]*\})*`[^{}]*\}$/, '')  // 末尾按条件拼的查询串：${x ? `?k=${x}` : ''}
      .replace(/\?.*$/, '').replace(/\$\{[^}]+\}/g, '{}');
    assert.equal(path, template, `${route}：实际路径 ${rawPath} 与模板不一致`);
  }
  assert.deepEqual([...used].sort(), Object.keys(BACKEND).sort(), '有接口没有经 send() / fetchRoute() 调用');
  const direct = [...src.matchAll(/(?<!function )\bcall<(?!ReviewResponses\[K\]>)[^>(\n]*>\(/g)].map((m) => m[0]);
  assert.deepEqual(direct, [], '还有不经 send() / fetchRoute() 直接用 call<T>() 的调用');
});
