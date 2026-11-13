// 审核台前后端请求体核对（W13 T35，specs/review-console-types-t35 第三节）：
// 用 TypeScript 编译器 API 读 src/lib/review.ts 的 ReviewRequests，与后端导出的 src/lib/review-api.requests.json 逐项比较——
// 接口集合相同、每个接口的字段集合相同、必填一致；后端有枚举的字段，前端必须是字符串字面量联合，且每个取值后端都接受
// （前端可以更窄，例如搁置的英语科不出现在审核台里）。另核对带请求体的调用都经 send()，且路径与路由模板对得上。
// 格式（pattern、长度）不在这里核对：前端不重复后端的格式校验。
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import ts from 'typescript';

const WEB = new URL('..', import.meta.url).pathname;
// 变异测试（tests/review-requests-mutations.test.ts）把改坏的副本放在临时目录，经这两个变量指过去；平时就是仓库里的文件
const SRC = process.env['REVIEW_REQUESTS_SRC'] ?? `${WEB}src/lib/review.ts`;
const JSON_PATH = process.env['REVIEW_REQUESTS_JSON'] ?? `${WEB}src/lib/review-api.requests.json`;

interface BackendField { required: boolean; type: string; nullable: boolean; enum: string[] | null; items?: { type: string; enum: string[] | null } }
const BACKEND = (JSON.parse(readFileSync(JSON_PATH, 'utf8')) as { routes: Record<string, Record<string, BackendField>> }).routes;

interface FrontField { optional: boolean; kind: 'string' | 'array' | 'other'; literals: string[] | null; itemLiterals: string[] | null }

function frontend(): Record<string, Record<string, FrontField>> {
  const program = ts.createProgram([SRC], { strict: true, noEmit: true, allowImportingTsExtensions: true, target: ts.ScriptTarget.ES2022,
                                           module: ts.ModuleKind.ESNext, moduleResolution: ts.ModuleResolutionKind.Bundler });
  const checker = program.getTypeChecker();
  const sf = program.getSourceFile(SRC)!;
  let decl: ts.InterfaceDeclaration | undefined;
  ts.forEachChild(sf, (n) => { if (ts.isInterfaceDeclaration(n) && n.name.text === 'ReviewRequests') decl = n; });
  assert.ok(decl, 'review.ts 里没有 ReviewRequests');
  const literalsOf = (t: ts.Type): string[] | null => {
    const parts = (t.isUnion() ? t.types : [t]).filter((x) => !(x.flags & (ts.TypeFlags.Undefined | ts.TypeFlags.Null)));
    if (parts.length > 0 && parts.every((x) => x.isStringLiteral())) return parts.map((x) => (x as ts.StringLiteralType).value).sort();
    return null;
  };
  const out: Record<string, Record<string, FrontField>> = {};
  for (const m of decl!.members) {
    assert.ok(ts.isPropertySignature(m) && m.type && ts.isStringLiteral(m.name), 'ReviewRequests 的键必须是字符串字面量');
    const key = (m.name as ts.StringLiteral).text;
    const body = checker.getTypeAtLocation(m.type!);
    const fields: Record<string, FrontField> = {};
    for (const p of checker.getPropertiesOfType(body)) {
      const t = checker.getNonNullableType(checker.getTypeOfSymbol(p));
      const elem = checker.isArrayType(t) ? checker.getTypeArguments(t as ts.TypeReference)[0]! : null;
      const lits = elem ? null : literalsOf(t);
      fields[p.name] = {
        optional: (p.flags & ts.SymbolFlags.Optional) !== 0,
        kind: elem ? 'array' : lits || (t.flags & ts.TypeFlags.String) ? 'string' : 'other',
        literals: lits,
        itemLiterals: elem ? literalsOf(elem) : null,
      };
    }
    out[key] = fields;
  }
  return out;
}

test('ReviewRequests 与后端导出的请求体一致：接口、字段、必填、枚举', () => {
  const front = frontend();
  assert.deepEqual(Object.keys(front).sort(), Object.keys(BACKEND).sort(), '带请求体的接口集合（ReviewRequests 的键）与后端不同');
  for (const [route, fields] of Object.entries(BACKEND)) {
    const f = front[route]!;
    assert.deepEqual(Object.keys(f).sort(), Object.keys(fields).sort(), `${route}：字段集合不同`);
    for (const [name, b] of Object.entries(fields)) {
      const where = `${route} 的 ${name}`;
      assert.equal(!f[name]!.optional, b.required, `${where}：必填不一致（后端 required=${b.required}）`);
      if (b.type === 'array') {
        assert.equal(f[name]!.kind, 'array', `${where}：后端是数组`);
        if (b.items?.enum) {
          const lits = f[name]!.itemLiterals;
          assert.ok(lits, `${where}：后端元素有枚举（${b.items.enum.join('、')}），前端不能是宽泛的 string[]`);
          for (const v of lits!) assert.ok(b.items.enum.includes(v), `${where}：前端取值 ${v} 后端不接受`);
        }
        continue;
      }
      if (b.type === 'string') assert.equal(f[name]!.kind, 'string', `${where}：后端是字符串`);
      if (b.enum) {
        const lits = f[name]!.literals;
        assert.ok(lits, `${where}：后端有枚举（${b.enum.join('、')}），前端不能是宽泛的 string`);
        for (const v of lits!) assert.ok(b.enum.includes(v), `${where}：前端取值 ${v} 后端不接受`);
      }
    }
  }
});

test('带请求体的调用都经 send()，路径与路由模板对得上；不再直接用 call() 发请求体', () => {
  const src = readFileSync(SRC, 'utf8');
  const sends = [...src.matchAll(/\bsend(?:<[^(]*>)?\(\s*(?:staff|null),\s*'([A-Z]+ [^']+)',\s*(`[^`]*`|'[^']*')/g)];
  assert.ok(sends.length >= Object.keys(BACKEND).length, `send() 调用只找到 ${sends.length} 处`);
  const used = new Set<string>();
  for (const [, route, rawPath] of sends) {
    assert.ok(route! in BACKEND, `send() 用了后端没有请求体的路由：${route}`);
    used.add(route!);
    const template = route!.slice(route!.indexOf(' ') + 1);
    const path = rawPath!.slice(1, -1).replace(/\$\{[^}]+\}/g, '{}');
    assert.equal(path, template.replace(/\{[^}]+\}/g, '{}'), `${route}：实际路径 ${rawPath} 与模板不一致`);
  }
  assert.deepEqual([...used].sort(), Object.keys(BACKEND).sort(), '有带请求体的接口没有经 send() 调用');
  // call() 只剩 send() 里那一处带请求体（第 4 个参数）
  const direct = [...src.matchAll(/\bcall<[^(]*>\(\s*(?:staff|null|session),\s*'(POST|PATCH|PUT)',\s*[^,)]+,\s*[^)]/g)];
  assert.deepEqual(direct.map((m) => m[0]), [], '还有不经 send() 直接用 call() 发请求体的调用');
});
