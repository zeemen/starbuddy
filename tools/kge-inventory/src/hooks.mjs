// Node 模块解析钩子（由 src/legacy.ts 的 registerLegacyHooks 注册）：只做路径解析，不改写、不写入任何 legacy 文件。
// 1. "@/..." 解析到阿鸦 legacy 根目录（阿鸦的路径别名）；
// 2. 省略后缀的相对路径补 .ts / /index.ts；
// 3. legacy 目录里的模块只允许 import 相对路径或 node: 内置模块；引用第三方包一律报错，
//    报告里记“无法加载”，而不是在 legacy 里装依赖（spec 设计要点 2）。
import { existsSync } from "node:fs";
import { pathToFileURL } from "node:url";

let aliasRoot = null;
let legacyRoots = [];

export async function initialize(data) {
  aliasRoot = data?.aliasRoot ?? null;
  legacyRoots = (data?.legacyRoots ?? []).map((r) => pathToFileURL(r.endsWith("/") ? r : `${r}/`).href);
}

export async function resolve(specifier, context, next) {
  let s = specifier;
  if (aliasRoot && s.startsWith("@/")) s = pathToFileURL(`${aliasRoot}/${s.slice(2)}`).href;
  if ((s.startsWith(".") || s.startsWith("file:")) && !/\.(ts|mjs|js|json)$/.test(s)) {
    const base = new URL(s, context.parentURL);
    for (const ext of [".ts", "/index.ts"]) {
      const candidate = new URL(base.href + ext);
      if (existsSync(candidate)) return next(candidate.href, context);
    }
  }
  const fromLegacy = context.parentURL && legacyRoots.some((r) => context.parentURL.startsWith(r));
  if (fromLegacy && !s.startsWith(".") && !s.startsWith("file:") && !s.startsWith("node:")) {
    throw new Error(`legacy 模块引用了第三方包 ${specifier}（不在 legacy 里安装依赖，此模块跳过）`);
  }
  return next(s, context);
}
