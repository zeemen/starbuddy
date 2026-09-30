// 数学自适应诊断组件与演示页（SIE-02a）的单测与静态检查：
//   - 文案集中在 src/diagnosis/copy.ts，过点点禁语表（evals/persona/diandian/lexicon/forbidden.yaml，只读；做法同 tests/probe.test.ts），
//     另查一遍不评价、不给结论、不提对错与历史的用词；结束页只有“完成了，老师会看你的过程。”；
//   - 组件与页面源码里没有题号、答案、对错、节点等字段（组件只拿到题面与序号）；
//   - 演示页是 page.dev.tsx；它的 action 与客户端只被演示页引用；BFF 客户端按白名单取字段。
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { test } from 'node:test';
import { allDiagnosisCopy, DIAGNOSIS_COPY, formatDuration } from '../src/diagnosis/copy.ts';
import { pickView, type DiagnosisView } from '../src/lib/diagnosis.ts';

const WEB = new URL('..', import.meta.url).pathname;
const LEXICON = join(WEB, '..', '..', 'evals', 'persona', 'diandian', 'lexicon', 'forbidden.yaml');

function loadForbidden(): { id: string; rx: RegExp }[] {
  const text = readFileSync(LEXICON, 'utf8');
  const section = text.slice(text.indexOf('\nforbidden:'), text.indexOf('\npatterns:'));
  const out: { id: string; rx: RegExp }[] = [];
  for (const line of section.split('\n')) {
    const m = /^\s*- \{ id: ([A-Z]-\d+),.*?\bclass: ([A-Z]),.*?\bregex: "((?:[^"\\]|\\.)*)"/.exec(line);
    if (m) out.push({ id: m[1]!, rx: new RegExp(JSON.parse(`"${m[3]!}"`) as string) });
  }
  return out;
}

test('诊断文案不命中禁语表（禁语表能读出来且确实能命中）', () => {
  const rules = loadForbidden();
  assert.ok(rules.length >= 30, `只读出 ${rules.length} 条`);
  assert.ok(rules.some((r) => r.rx.test('合成：你总是这样')));
  for (const s of allDiagnosisCopy()) {
    assert.deepEqual(rules.filter((r) => r.rx.test(s)).map((r) => r.id), [], s);
  }
});

test('诊断中不评价、不给结论；结束页只说完成了', () => {
  const banned = /得分|分数|打分|正确率|对了|错了|答对|答错|正确|错误|上次|以前|历史|同学|别人|大家|棒|厉害|聪明|加油|自信|紧张|焦虑|心情|水平|掌握|薄弱|基础|差|优秀|结论|报告|排名/;
  for (const s of allDiagnosisCopy()) assert.ok(!banned.test(s), s);
  assert.equal(DIAGNOSIS_COPY.finished, '完成了，老师会看你的过程。');
  // 放弃、或本次没有选出过程题：中性文案，不许诺“老师会看”（契约审查 B1）；宵禁只说“今天先到这里”（安全审查 B2）
  assert.ok(!/老师|过程/.test(DIAGNOSIS_COPY.endedNeutral), DIAGNOSIS_COPY.endedNeutral);
  assert.ok(DIAGNOSIS_COPY.curfew.startsWith('今天先到这里'), DIAGNOSIS_COPY.curfew);
  const src = readFileSync(join(WEB, 'src/diagnosis/Diagnosis.tsx'), 'utf8');
  assert.match(src, /view\?\.status === 'finished' && view\.process_tasks \? C\.finished : C\.endedNeutral/);
  assert.match(src, /if \(r\.curfew\) return toCurfew\(\);/);
  assert.equal((src.match(/\.curfew\) return toCurfew\(\);/g) ?? []).length, 4);  // 开始 / 续做、取题、探针、作答
});

test('组件与演示页源码不接触题号、答案、对错、节点、选题理由', () => {
  const files = ['src/diagnosis/Diagnosis.tsx', 'src/app/dev/diagnosis/[studentId]/DiagnosisDemo.tsx',
    'src/app/dev/diagnosis/[studentId]/page.dev.tsx'];
  for (const f of files) {
    const t = readFileSync(join(WEB, f), 'utf8').replace(/^\s*\/\/.*$/gm, '');
    assert.ok(!/item_id|verdict|correct|node_id|reason_code|usage_id|answer_key|break_point/.test(t), f);
  }
  // 组件复用 2-L 的探针组件
  assert.match(readFileSync(join(WEB, 'src/diagnosis/Diagnosis.tsx'), 'utf8'), /import \{ ConfidenceProbe \} from '\.\.\/probe\/ConfidenceProbe\.tsx'/);
});

test('演示页是 page.dev.tsx；它的 action 与客户端只被演示页引用', () => {
  const walk = (dir: string): string[] => readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    return statSync(p).isDirectory() ? walk(p) : [p];
  });
  const src = walk(join(WEB, 'src')).map((p) => relative(WEB, p));
  const demoDir = 'src/app/dev/diagnosis/';
  assert.deepEqual(src.filter((p) => p.startsWith(demoDir) && /\/page\.[a-z.]+$/.test(p)), ['src/app/dev/diagnosis/[studentId]/page.dev.tsx']);
  for (const p of src.filter((f) => !f.startsWith(demoDir))) {
    assert.ok(!/dev\/diagnosis\/actions|DiagnosisDemo/.test(readFileSync(join(WEB, p), 'utf8')), `${p} 不能引用演示页的 action 或客户端`);
  }
  // 诊断组件目前只被演示页用（做题页随冲刺 4 接入）：生产页面不引用它
  for (const p of src.filter((f) => f.startsWith('src/app/') && !f.startsWith(demoDir))) {
    assert.ok(!/diagnosis\/Diagnosis/.test(readFileSync(join(WEB, p), 'utf8')), p);
  }
});

test('BFF 视图按白名单取字段；计时格式', () => {
  const raw = {
    session_id: 's', status: 'active', elapsed_sec: 61, time_budget_sec: 2100, item_count: 1, item_limit: 20, verdict: 'correct',
    step: { seq: 2, kind: 'process', item_id: 'Q-X', probe_recorded: true, step_elapsed_sec: 5,
            item: { format: 'single', stem: '合成', answer: ['A'], options: [{ key: 'A', text: '甲', correct: true }] } },
  } as unknown as DiagnosisView;
  const picked = pickView(raw);
  const v = JSON.stringify(picked);
  assert.ok(!/verdict|item_id|answer|correct|Q-X/.test(v), v);
  assert.equal(picked.process_tasks, false);  // 缺省为假：结束页不许诺“老师会看你的过程”
  assert.equal(formatDuration(0), '00:00');
  assert.equal(formatDuration(2100), '35:00');
  assert.equal(formatDuration(3725), '1:02:05');
});

test('复核详情页：诊断过程观察显示过程原文证据的引用；原文未授权时显示“原文待授权”，不另开家长 / 学生途径', () => {
  const page = readFileSync(join(WEB, 'src/app/review/queue/[id]/page.tsx'), 'utf8');
  assert.match(page, /data-testid="work-evidence-id"/);
  assert.match(page, /原文待授权/);
  // 原文只在审核台（staff 登录后的 /review 路由）出现：家长、学生页面与 BFF 客户端都不引用 work_evidence_id
  const lib = readFileSync(join(WEB, 'src/lib/diagnosis.ts'), 'utf8');
  assert.ok(!/work_evidence_id|work_text/.test(lib));
});
