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
  const v = JSON.stringify(pickView(raw));
  assert.ok(!/verdict|item_id|answer|correct|Q-X/.test(v), v);
  assert.equal(formatDuration(0), '00:00');
  assert.equal(formatDuration(2100), '35:00');
  assert.equal(formatDuration(3725), '1:02:05');
});
