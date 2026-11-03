// 单测用的加载钩子（只给 node --test 用，不进任何构建）：
//   - resolve：react、react-dom 与它们的子路径从 apps/web 已安装的 node_modules 解析（本包不装依赖，react 是同伴依赖）；
//   - load：.tsx 用 apps/web 已锁版本的 typescript 转成 ESM（jsx: react-jsx），其余文件交回 Node 默认处理（.ts 由 Node 剥离类型）。
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';

// 变异脚本（scripts/mutations.ts）把本包复制到临时目录里跑，用 STAGE_WEB_ROOT 指回仓库里的 apps/web
const WEB = process.env['STAGE_WEB_ROOT']
  ? pathToFileURL(`${process.env['STAGE_WEB_ROOT']}/package.json`)
  : new URL('../../../apps/web/package.json', import.meta.url);
const webRequire = createRequire(WEB);
const ts = webRequire('typescript');

export async function resolve(specifier, context, next) {
  if (/^react(-dom)?(\/|$)/.test(specifier)) {
    return { url: pathToFileURL(webRequire.resolve(specifier)).href, shortCircuit: true };
  }
  return next(specifier, context);
}

export async function load(url, context, next) {
  if (!url.startsWith('file:') || !url.endsWith('.tsx')) return next(url, context);
  const src = await readFile(new URL(url), 'utf8');
  const out = ts.transpileModule(src, {
    fileName: new URL(url).pathname,
    compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, verbatimModuleSyntax: false },
  });
  return { format: 'module', source: out.outputText, shortCircuit: true };
}
