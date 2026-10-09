// BFF 自己的接口定义（openapi/*.v0.yaml）里每个操作的鉴权口径与状态码（4-H H6，契约审查 M1–M3）。
// 不引入 YAML 解析依赖：只按缩进取路径、方法、x-sb-* 扩展与 responses 下的状态码。
// x-sb-auth：public / session / guardian / student（与路由表的 auth 一致，shell-openapi.test.ts 断言）；
// x-sb-rate-buckets：挂的限流桶；x-sb-not-self：只认学生本人的路由对“不是本人”（兄妹、另一户学生、不存在）回的状态码。
import { readFileSync } from 'node:fs';

/** 接口定义文件（不含 .yaml）：v0 六份 + H5 星图 v1（开关 starmap_v1 后）+ 错题再练（开关 practice 后）+ 复测与验证题（开关 retest 后，KGE-05） */
export const SPEC_FILES = ['shell.v0', 'star-map.v0', 'onboarding.v0', 'events.v0', 'diagnosis.v0', 'trust.v0', 'star-map.v1', 'practice.v0',
                           'retest.v0'] as const;

export interface Op { auth: string | null; buckets: string[] | null; notSelf: number | null; statuses: Set<string>; file: string }

export function operations(): Map<string, Op> {
  const out = new Map<string, Op>();
  for (const name of SPEC_FILES) {
    const text = readFileSync(new URL(`../openapi/${name}.yaml`, import.meta.url), 'utf8');
    const end = text.indexOf('\ncomponents:');
    let path = '';
    let op: Op | null = null;
    for (const line of text.slice(text.indexOf('\npaths:'), end < 0 ? undefined : end).split('\n')) {
      const p = /^ {2}(\/\S+):\s*$/.exec(line);
      if (p) { path = p[1]!; op = null; continue; }
      const m = /^ {4}(get|post|put|delete|patch):\s*$/.exec(line);
      if (m && path) {
        op = { auth: null, buckets: null, notSelf: null, statuses: new Set(), file: `${name}.yaml` };
        out.set(`${m[1]!.toUpperCase()} ${path}`, op);
        continue;
      }
      if (/^ {4}\S/.test(line)) { op = null; continue; }
      if (!op) continue;
      const a = /^ {6}x-sb-auth: (\S+)\s*$/.exec(line);
      if (a) op.auth = a[1]!;
      const b = /^ {6}x-sb-rate-buckets: \[(.*)\]\s*$/.exec(line);
      if (b) op.buckets = b[1]!.split(',').map((x) => x.trim()).filter(Boolean);
      const n = /^ {6}x-sb-not-self: (\d{3})\s*$/.exec(line);
      if (n) op.notSelf = Number(n[1]);
      const s = /^ {8}'([0-9]{3})':/.exec(line);
      if (s) op.statuses.add(s[1]!);
    }
  }
  return out;
}
