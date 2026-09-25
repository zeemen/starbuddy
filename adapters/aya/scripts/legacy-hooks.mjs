// Node 模块解析钩子（见 legacy-loader.ts）。只做路径解析，不改写任何源码。
import { existsSync } from "node:fs";
import { pathToFileURL } from "node:url";

const ROOT = process.env.AYA_LEGACY_ROOT;

export async function resolve(specifier, context, next) {
  let s = specifier;
  if (ROOT && s.startsWith("@/")) s = pathToFileURL(`${ROOT}/${s.slice(2)}`).href;
  if ((s.startsWith(".") || s.startsWith("file:")) && !/\.(ts|mjs|js|json)$/.test(s)) {
    const base = new URL(s, context.parentURL);
    for (const ext of [".ts", "/index.ts"]) {
      const candidate = new URL(base.href + ext);
      if (existsSync(candidate)) return next(candidate.href, context);
    }
  }
  return next(s, context);
}
