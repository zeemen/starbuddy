// 读取 legacy 只读副本的工具：注册解析钩子、安全 import、读文本、git 元数据、关键字搜索。
// 全部只读：不写 legacy 任何文件，不安装依赖（spec 设计要点 2）。
import { execFileSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { register } from "node:module";
import { join, relative } from "node:path";
import { pathToFileURL } from "node:url";

let registered = false;

export function registerLegacyHooks(aliasRoot: string | null, legacyRoots: string[]): void {
  if (registered) return;
  register(new URL("./hooks.mjs", import.meta.url), { data: { aliasRoot, legacyRoots } });
  registered = true;
}

/** import 一个 legacy 模块；失败时把原因记进 errors 并返回 null（报告里写“无法加载”）。 */
export async function importLegacy<T = Record<string, unknown>>(
  root: string,
  rel: string,
  errors: string[],
): Promise<T | null> {
  const path = join(root, rel);
  if (!existsSync(path)) {
    errors.push(`${rel}：文件不存在`);
    return null;
  }
  try {
    return (await import(pathToFileURL(path).href)) as T;
  } catch (e) {
    const msg = e instanceof Error ? e.message.split("\n")[0] : String(e);
    errors.push(`${rel}：无法加载（${msg}）`);
    return null;
  }
}

export function readText(root: string, rel: string): string | null {
  const path = join(root, rel);
  return existsSync(path) ? readFileSync(path, "utf8") : null;
}

export function readJson<T = unknown>(root: string, rel: string, errors: string[]): T | null {
  const text = readText(root, rel);
  if (text === null) {
    errors.push(`${rel}：文件不存在`);
    return null;
  }
  try {
    return JSON.parse(text) as T;
  } catch (e) {
    errors.push(`${rel}：JSON 解析失败（${e instanceof Error ? e.message : String(e)}）`);
    return null;
  }
}

/** git 元数据：提交号、工作区是否干净、提交数（浅克隆时为 1）。不是 git 目录时返回 null。 */
export function gitMeta(root: string): { commit: string | null; clean: boolean | null; commits: number | null } {
  const git = (...args: string[]) =>
    execFileSync("git", ["-C", root, ...args], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim();
  try {
    return {
      commit: git("rev-parse", "HEAD"),
      clean: git("status", "--porcelain") === "",
      commits: Number(git("rev-list", "--count", "HEAD")),
    };
  } catch {
    return { commit: null, clean: null, commits: null };
  }
}

const SKIP_DIRS = new Set(["node_modules", ".git", ".next", "dist", "build", ".turbo", "coverage"]);
const TEXT_EXT = /\.(ts|tsx|js|mjs|json|md|sql|txt|csv|yaml|yml)$/;

/** 列出目录下的文本文件（相对 root，已排序）。ext 可换成别的后缀规则（例如按文件名找 PDF）。 */
export function listTextFiles(root: string, dirs: string[], ext: RegExp = TEXT_EXT): string[] {
  const out: string[] = [];
  const walk = (abs: string) => {
    let entries: string[];
    try {
      entries = readdirSync(abs);
    } catch {
      return;
    }
    for (const name of entries) {
      if (SKIP_DIRS.has(name) || /(^|-)lock\.(json|yaml)$/.test(name)) continue;
      const p = join(abs, name);
      const st = statSync(p);
      if (st.isDirectory()) walk(p);
      else if (ext.test(name) && st.size < 5_000_000) out.push(relative(root, p));
    }
  };
  for (const d of dirs) walk(join(root, d));
  return [...new Set(out)].sort();
}

/** 在文件里找关键字，返回命中的文件（相对路径，已排序）。 */
export function filesMatching(root: string, files: string[], pattern: RegExp): string[] {
  return files.filter((f) => pattern.test(readFileSync(join(root, f), "utf8"))).sort();
}

/**
 * 取出 `export const NAME = [ ... ]` / `{ ... }`、`interface NAME { ... }` 或 `create table NAME ( ... )` 的括号正文（按括号配对）。
 * 只用于 .tsx 这类不能 import 的文件，以及提取接口字段。
 */
export function extractBlock(text: string, head: RegExp, opens = "[{"): string | null {
  const m = head.exec(text);
  if (!m) return null;
  let from = -1;
  for (let i = m.index + m[0].length; i < text.length; i++) {
    if (opens.includes(text[i]!)) {
      from = i;
      break;
    }
  }
  if (from < 0) return null;
  const open = text[from]!;
  const close = open === "[" ? "]" : open === "(" ? ")" : "}";
  let depth = 0;
  let quote: string | null = null;
  for (let i = from; i < text.length; i++) {
    const ch = text[i]!;
    if (quote) {
      if (ch === "\\") i++;
      else if (ch === quote) quote = null;
      continue;
    }
    if (ch === "/" && text[i + 1] === "/") {
      const nl = text.indexOf("\n", i);
      i = nl < 0 ? text.length : nl;
      continue;
    }
    if (ch === "/" && text[i + 1] === "*") {
      const end = text.indexOf("*/", i + 2);
      i = end < 0 ? text.length : end + 1;
      continue;
    }
    if (ch === '"' || ch === "'" || ch === "`") quote = ch;
    else if (ch === open) depth++;
    else if (ch === close && --depth === 0) return text.slice(from + 1, i);
  }
  return null;
}
