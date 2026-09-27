// Node 模块解析钩子（由 src/legacy.ts 的 registerLegacyHooks 在隔离子进程里注册）：只做路径解析，不改写任何 legacy 文件。
// 1. "@/..." 解析到阿鸦 legacy 根目录（阿鸦的路径别名）；
// 2. 省略后缀的相对路径补 .ts / /index.ts；
// 3. legacy 目录里的模块只能 import 同样位于 legacy 目录内的文件：第三方包、node: 内置模块、跳出 legacy 的路径一律拒绝，
//    报告里记“无法加载”（spec 设计要点 2；安全审查）。这只是约束，不是安全边界；安全边界是子进程的权限与空环境变量。
import { existsSync } from "node:fs";
import { pathToFileURL } from "node:url";

let aliasRoot = null;
let legacyRoots = [];

export async function initialize(data) {
  aliasRoot = data?.aliasRoot ?? null;
  legacyRoots = (data?.legacyRoots ?? []).map((r) => pathToFileURL(r.endsWith("/") ? r : `${r}/`).href);
}

const inLegacy = (url) => legacyRoots.some((r) => url.startsWith(r));

export async function resolve(specifier, context, next) {
  const fromLegacy = Boolean(context.parentURL) && inLegacy(context.parentURL);
  let s = specifier;
  if (aliasRoot && s.startsWith("@/")) s = pathToFileURL(`${aliasRoot}/${s.slice(2)}`).href;
  const isPath = s.startsWith(".") || s.startsWith("file:");
  if (fromLegacy && !isPath) {
    throw new Error(`legacy 模块引用了 ${specifier}（只允许 legacy 目录内的相对路径，不安装依赖、不开放内置模块，此模块跳过）`);
  }
  let target = s;
  if (isPath && !/\.(ts|mjs|js|json)$/.test(s)) {
    const base = new URL(s, context.parentURL);
    for (const ext of [".ts", "/index.ts"]) {
      const candidate = new URL(base.href + ext);
      if (existsSync(candidate)) {
        target = candidate.href;
        break;
      }
    }
  }
  if (fromLegacy) {
    const abs = new URL(target, context.parentURL).href;
    if (!inLegacy(abs)) throw new Error(`legacy 模块引用了 legacy 目录以外的文件 ${specifier}，此模块跳过`);
  }
  return next(target, context);
}
