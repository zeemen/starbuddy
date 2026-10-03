// 单测用的 .tsx 加载钩子（只给 node --test 用，不进构建）：Node 自带的类型剥离不处理 JSX，这里用已锁版本的 typescript
// 把 .tsx 转成 ESM（jsx: react-jsx，与 tsconfig.json 一致），其余文件交回 Node 默认处理。tests/star-v1-render.test.ts 用它
// 以 react-dom/server 渲染星图 v1 组件（组件级渲染测试，不需要浏览器）。
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

export async function load(url, context, next) {
  if (!url.startsWith('file:') || !url.endsWith('.tsx')) return next(url, context);
  const src = await readFile(new URL(url), 'utf8');
  const out = ts.transpileModule(src, {
    fileName: new URL(url).pathname,
    compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, verbatimModuleSyntax: false },
  });
  return { format: 'module', source: out.outputText, shortCircuit: true };
}
