// 开关矩阵（deploy/flags/staging.yaml）对 BFF 与前端开关的语义核对：用两边真实的解析函数，不另抄规则。
// 由 deploy/tests/test_flags_matrix.py 调用：标准输入是 JSON {"bff": {变量: 值|null}, "web": {变量: 值|null}}；
// 核对：staging 下“开”的取值被接受且解析出的集合与矩阵一致；“关”（null）在 staging 解析为关；同样的取值在 pilot / 生产被拒绝或按关。
// 输出一行 JSON {"ok": true, "checked": n} 或 {"ok": false, "problems": [...]}，退出码 0 / 1。
import { readFileSync } from 'node:fs';
import { parseTrustFeatures } from '../../services/bff/src/trust/features.ts';
import { parseStarMapFeatures } from '../../services/bff/src/star-map/features.ts';
import { parsePracticeFeature } from '../../services/bff/src/practice/features.ts';
import { parseRetestFeature } from '../../services/bff/src/retest/features.ts';
import { parseTodayOrchFeature } from '../../services/bff/src/shell/today-orch.ts';
import { parseFamilyGoalsFeature } from '../../services/bff/src/family/features.ts';
import { parseMonthlyPlanFeature } from '../../services/bff/src/family/monthly-plan.ts';
import { parseSubjectGateFeature } from '../../services/bff/src/gate/subject-gate.ts';
import type { RunEnv } from '../../services/bff/src/http/config.ts';
import { trustFeatures } from '../../apps/web/src/shell/features.ts';
import { starMapFeatures } from '../../apps/web/src/star/features.ts';
import { practiceEnabled } from '../../apps/web/src/practice/features.ts';
import { retestEnabled } from '../../apps/web/src/retest/features.ts';
import { todayOrchEnabled } from '../../apps/web/src/shell/today-orch.ts';
import { monthlyPlanEnabled } from '../../apps/web/src/monthly-plan/features.ts';
import { familyGoalsEnabled } from '../../apps/web/src/family-goals/features.ts';
import { internalTestEnabled } from '../../apps/web/src/internal-test/features.ts';
import { parseInternalTestFeature } from '../../services/bff/src/internal-test/features.ts';

type Vals = Record<string, string | null>;
const input = JSON.parse(readFileSync(0, 'utf8')) as { bff: Vals; web: Vals };
const problems: string[] = [];
let checked = 0;

const list = (v: string | null): string[] => (v ?? '').split(',').map((s) => s.trim()).filter((s) => s !== '');
const sameSet = (a: Iterable<string>, b: string[]) => { const x = [...a].sort(); const y = [...b].sort(); return x.length === y.length && x.every((v, i) => v === y[i]); };

// BFF：解析函数 (env, raw) → 集合或布尔；非法即抛错
const bff: Record<string, (env: RunEnv, raw: string | undefined) => ReadonlySet<string> | boolean> = {
  STARBUDDY_TRUST_UI: parseTrustFeatures, STARBUDDY_STARMAP: parseStarMapFeatures, STARBUDDY_PRACTICE: parsePracticeFeature,
  STARBUDDY_RETEST_UI: parseRetestFeature, STARBUDDY_TODAY_ORCH: parseTodayOrchFeature, STARBUDDY_FAMILY_GOALS: parseFamilyGoalsFeature,
  STARBUDDY_MONTHLY_PLAN: parseMonthlyPlanFeature, STARBUDDY_SUBJECT_GATE: parseSubjectGateFeature,
  STARBUDDY_INTERNAL_TEST: parseInternalTestFeature,
};
// 前端：(env 对象) → 集合或布尔；不认识的环境按关
const web: Record<string, (env: Record<string, string | undefined>) => ReadonlySet<string> | boolean> = {
  STARBUDDY_TRUST_UI: trustFeatures, STARBUDDY_STARMAP: starMapFeatures, STARBUDDY_PRACTICE: practiceEnabled,
  STARBUDDY_RETEST_UI: retestEnabled, STARBUDDY_TODAY_ORCH: todayOrchEnabled, STARBUDDY_MONTHLY_PLAN: monthlyPlanEnabled,
  STARBUDDY_FAMILY_GOALS: familyGoalsEnabled, STARBUDDY_INTERNAL_TEST: internalTestEnabled,
};
const on = (r: ReadonlySet<string> | boolean, want: string[]): boolean => (typeof r === 'boolean' ? r === want.length > 0 : sameSet(r, want));
const off = (r: ReadonlySet<string> | boolean): boolean => (typeof r === 'boolean' ? !r : r.size === 0);
// 闸门在非本地环境照样可开（是否开由人定，矩阵里关）：不做 pilot / 生产拒绝核对
const PILOT_EXEMPT = new Set(['STARBUDDY_SUBJECT_GATE']);

for (const [name, value] of Object.entries(input.bff)) {
  const f = bff[name];
  if (!f) { problems.push(`bff ${name}：没有对应的解析函数（矩阵写错或开关已改名）`); continue; }
  checked += 1;
  try {
    const r = f('staging', value ?? undefined);
    if (value === null ? !off(r) : !on(r, typeof r === 'boolean' ? [value] : list(value))) problems.push(`bff ${name}：staging 解析结果与矩阵不一致`);
  } catch (e) {
    problems.push(`bff ${name}：staging 拒绝矩阵取值（${(e as Error).message}）`);
  }
  if (value !== null && !PILOT_EXEMPT.has(name)) {
    for (const env of ['pilot', 'production'] as const) {
      let refused = false;
      try { refused = off(f(env, value)); } catch { refused = true; }
      if (!refused) problems.push(`bff ${name}：${env} 没有拒绝`);
    }
  }
}
for (const [name, value] of Object.entries(input.web)) {
  const f = web[name];
  if (!f) { problems.push(`web ${name}：没有对应的开关函数（矩阵写错或开关已改名）`); continue; }
  checked += 1;
  const r = f({ STARBUDDY_ENV: 'staging', [name]: value ?? undefined });
  if (value === null ? !off(r) : !on(r, typeof r === 'boolean' ? [value] : list(value))) problems.push(`web ${name}：staging 结果与矩阵不一致`);
  if (value !== null) {
    for (const env of ['pilot', 'production']) if (!off(f({ STARBUDDY_ENV: env, [name]: value }))) problems.push(`web ${name}：${env} 没有按关`);
  }
}
// LT-06 内部成员测试运营：矩阵里关（待人执行迁移 0059）；执行之后 staging 要能开，pilot / 生产仍拒绝或按关（两边一起核对）
{
  let staged = false;
  try { staged = parseInternalTestFeature('staging', 'internal_test') === true; } catch { staged = false; }
  if (!staged) problems.push('bff STARBUDDY_INTERNAL_TEST：staging 打不开');
  if (!internalTestEnabled({ STARBUDDY_ENV: 'staging', STARBUDDY_INTERNAL_TEST: 'internal_test' })) problems.push('web STARBUDDY_INTERNAL_TEST：staging 打不开');
  for (const env of ['pilot', 'production'] as const) {
    let refused = false;
    try { refused = !parseInternalTestFeature(env, 'internal_test'); } catch { refused = true; }
    if (!refused) problems.push(`bff STARBUDDY_INTERNAL_TEST：${env} 没有拒绝`);
    if (internalTestEnabled({ STARBUDDY_ENV: env, STARBUDDY_INTERNAL_TEST: 'internal_test' })) problems.push(`web STARBUDDY_INTERNAL_TEST：${env} 没有按关`);
  }
}
process.stdout.write(JSON.stringify(problems.length ? { ok: false, problems } : { ok: true, checked }) + '\n');
process.exit(problems.length ? 1 : 0);
