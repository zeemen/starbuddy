// 审核台前端单测（不需要浏览器）：后端地址只允许 loopback、表单校验、staff 会话 cookie、开发 staff 身份桩已删除。
import assert from 'node:assert/strict';
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';
import { isLoopbackHost } from '../src/lib/dev-pages.ts';
import {
  assignmentLabel, isBasis, PAGE_PROBLEM_LABEL, pageProblemLabel, safeHttpsUrl, isCursor, isDigest, isNodeId, isQueueReason, isQueueStatus, isResolutionCode, isSubject, isTaskId, isVersionId, lines,
  problemFrom, QUEUE_REASON_LABEL, QUEUE_REASONS, QUEUE_RESULT_LABEL, QUEUE_STATUS_LABEL, QUEUE_STATUSES, queueQuery, reasonLabel, RESULT_LABEL,
  reviewBaseUrl, SOURCE_KIND_LABEL,
  ADMISSION_LABEL, ADMISSIONS, isCity, isCommit, isDay, isItemId, isReviewLevel, isSourceRef, LICENSE_STATUSES, POLICY_OBJECT_LABEL, REASON_LABEL,
  SOURCE_TYPES,
} from '../src/lib/review.ts';
import { isSessionToken, staffCookieName, staffCookieOptions } from '../src/lib/staff-cookie.ts';

test('审核台后端地址只能是 loopback 上的 http', () => {
  assert.equal(reviewBaseUrl({}), 'http://127.0.0.1:8790');
  assert.equal(reviewBaseUrl({ STARBUDDY_REVIEW_URL: 'http://localhost:9000/x' }), 'http://localhost:9000');
  for (const bad of ['https://127.0.0.1:1', 'http://10.0.0.2:8790', 'http://review.example.test', 'file:///tmp/x']) {
    assert.throws(() => reviewBaseUrl({ STARBUDDY_REVIEW_URL: bad }), bad);
  }
});

test('表单字段校验', () => {
  assert.ok(isNodeId('MATH.FUNC.CON.K_FUN_0901') && isNodeId('CHN.RC.T.A'));
  assert.ok(!isNodeId('math.func') && !isNodeId('MATH') && !isNodeId('MATH.F/../x'));
  assert.ok(isDigest('a'.repeat(64)) && !isDigest('A'.repeat(64)) && !isDigest('a'.repeat(63)));
  assert.ok(isBasis('wrong_version') && !isBasis('Wrong Version') && !isBasis(''));
  assert.ok(isVersionId('c0100000-0000-4000-8000-0000000000c1') && !isVersionId('../records'));
  assert.ok(isSubject('aya') && !isSubject('echo') && !isSubject('english'));
  assert.deepEqual(lines(' a \n\n b\r\nc  '), ['a', 'b', 'c']);
});

test('staff 会话 cookie：正式环境用 __Host- 前缀且 Secure；本地开发与测试不带前缀；都是 httpOnly、SameSite=Strict、12 小时', () => {
  assert.equal(staffCookieName({}), '__Host-sb_staff');
  assert.equal(staffCookieName({ STARBUDDY_ENV: 'staging' }), '__Host-sb_staff');
  assert.equal(staffCookieName({ STARBUDDY_ENV: 'test' }), 'sb_staff');
  assert.deepEqual(staffCookieOptions({}), { httpOnly: true, sameSite: 'strict', path: '/', secure: true, maxAge: 43200 });
  assert.equal(staffCookieOptions({ STARBUDDY_ENV: 'dev' }).secure, false);
  assert.ok(isSessionToken('a'.repeat(64)) && !isSessionToken('A'.repeat(64)) && !isSessionToken('x') && !isSessionToken(undefined));
});

test('Host 必须是本机地址（开发专用页面用，挡 DNS 重绑定）', () => {
  for (const h of ['127.0.0.1', '127.0.0.1:3100', 'localhost:3100', '[::1]:3100']) assert.ok(isLoopbackHost(h), h);
  for (const h of ['evil.example.test:3100', '127.0.0.1.evil.test', 'localhost.evil.test:80', '', null, undefined]) {
    assert.ok(!isLoopbackHost(h), String(h));
  }
});

test('后端错误只对规则类透传说明，并截断', () => {
  assert.deepEqual(problemFrom(409, { code: 'RULE_VIOLATION', detail: 'x'.repeat(300) }), { status: 409, code: 'RULE_VIOLATION', detail: 'x'.repeat(200) });
  assert.deepEqual(problemFrom(403, { code: 'FORBIDDEN', detail: 'permission denied for table y' }), { status: 403, code: 'FORBIDDEN', detail: undefined });
  assert.deepEqual(problemFrom(500, { code: '<script>', detail: 'x' }), { status: 500, code: 'INTERNAL', detail: undefined });
  assert.deepEqual(problemFrom(502, null), { status: 502, code: 'INTERNAL', detail: undefined });
});

test('结果提示覆盖后端的全部错误码', () => {
  for (const code of ['DIGEST_CHANGED', 'RULE_VIOLATION', 'FORBIDDEN', 'NOT_FOUND', 'INVALID_PARAMETER', 'UNAUTHENTICATED', 'CONFLICT',
    'INVALID_REFERENCE', 'INTERNAL', 'LOGIN_FAILED']) {
    assert.ok(Object.hasOwn(RESULT_LABEL, code), code);
  }
});

test('开发 staff 身份桩已删除（前后端源码里都找不到）', () => {
  const root = new URL('../../../', import.meta.url).pathname;
  assert.ok(!existsSync(join(root, 'apps/web/src/dev/staff.ts')));
  assert.ok(!existsSync(join(root, 'services/learning/review_console/devauth.py')));
  const files = (dir: string): string[] => readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    return n === '__pycache__' ? [] : statSync(p).isDirectory() ? files(p) : [p];
  });
  for (const f of [...files(join(root, 'apps/web/src')), ...files(join(root, 'services/learning/review_console'))]) {
    const text = readFileSync(f, 'utf8');
    assert.ok(!/x-dev-staff-id|sb_dev_staff|SYNTHETIC_STAFF|REVIEW_CONSOLE_DEV_AUTH/.test(text), f);
  }
});

// ---------- 统一复核队列（specs/review-queue-sprint2 设计要点 2） ----------
test('复核队列：原因码、状态、来源类型都有中文标签，与后端枚举一致', () => {
  // 与 services/learning/review_console/app.py 的 QUEUE_REASONS / QUEUE_STATUSES、迁移 0023 的 source_kind 一致
  const root = new URL('../../../', import.meta.url).pathname;
  const app = readFileSync(join(root, 'services/learning/review_console/app.py'), 'utf8');
  const tuple = (name: string): string[] => {
    const m = new RegExp(`${name} = \\(([^)]*)\\)`, 's').exec(app);
    assert.ok(m, name);
    return [...m[1]!.matchAll(/"([a-z_]+)"/g)].map((x) => x[1]!);
  };
  assert.deepEqual([...QUEUE_REASONS].sort(), tuple('QUEUE_REASONS').sort());
  assert.deepEqual([...QUEUE_STATUSES].sort(), tuple('QUEUE_STATUSES').sort());
  for (const r of QUEUE_REASONS) assert.ok(QUEUE_REASON_LABEL[r] && isQueueReason(r), r);
  for (const s of QUEUE_STATUSES) assert.ok(QUEUE_STATUS_LABEL[s] && isQueueStatus(s), s);
  for (const k of ['grade_result', 'skill_observation', 'error_attribution', 'claim']) assert.ok(Object.hasOwn(SOURCE_KIND_LABEL, k), k);
  assert.ok(!isQueueReason('toString') && !isQueueStatus('closed'));
  assert.equal(reasonLabel('student_appeal'), '学生申诉');
  assert.equal(reasonLabel('unknown_reason'), 'unknown_reason');
});

test('编译页面：对账问题码都有中文说明（与后端 pages.py 一致）；托管平台链接只认 https', () => {
  const root = new URL('../../../', import.meta.url).pathname;
  const src = readFileSync(join(root, 'services/learning/review_console/pages.py'), 'utf8');
  const codes = new Set([...src.matchAll(/(?:problems\.append\(|"problems": \[)"([a-z_]+)"/g)].map((m) => m[1]!));
  assert.ok(codes.size >= 6, [...codes].join(','));
  assert.deepEqual([...codes].sort(), Object.keys(PAGE_PROBLEM_LABEL).sort());
  assert.equal(pageProblemLabel('no_merge'), PAGE_PROBLEM_LABEL['no_merge']);
  assert.equal(pageProblemLabel('toString'), 'toString');
  assert.equal(safeHttpsUrl('https://git.example.cn/kge/-/merge_requests/1'), 'https://git.example.cn/kge/-/merge_requests/1');
  for (const bad of ['http://git.example.cn/1', 'javascript:alert(1)', 'https://u:p@git.example.cn/1', 'not a url']) {
    assert.equal(safeHttpsUrl(bad), null, bad);
  }
});

test('复核队列：领取情况三种文字', () => {
  assert.equal(assignmentLabel({ assigned: true, assigned_to_me: true }), '我已领取');
  assert.equal(assignmentLabel({ assigned: true, assigned_to_me: false }), '他人已领取');
  assert.equal(assignmentLabel({ assigned: false, assigned_to_me: false }), '未领取');
});

test('复核队列：查询串只拼校验过的值，翻页游标编码；游标只收 ISO 时间', () => {
  assert.equal(queueQuery({ subject: 'aya', status: 'open' }), 'subject=aya&status=open');
  const before = '2026-10-15T10:00:00.123456+00:00';
  assert.ok(isCursor(before) && isCursor('2026-10-15T10:00:00Z'));
  const before_id = '5c0e0000-0000-4000-8000-0000000000a3';
  assert.equal(queueQuery({ subject: 'zhixing', status: 'resolved', reason: 'spot_check', before, before_id, limit: 50 }),
    `subject=zhixing&status=resolved&reason=spot_check&before=2026-10-15T10%3A00%3A00.123456%2B00%3A00&before_id=${before_id}&limit=50`);
  // 游标两个参数只一起出现
  assert.equal(queueQuery({ subject: 'aya', status: 'open', before }), 'subject=aya&status=open');
  assert.equal(queueQuery({ subject: 'aya', status: 'open', before_id }), 'subject=aya&status=open');
  for (const bad of ['', 'yesterday', '2026-10-15', '2026-10-15T10:00:00', '2026-10-15T10:00:00+00:00&limit=200', '2026-13-45T99:00:00Z']) assert.ok(!isCursor(bad), bad);
});

test('复核队列：结案码格式、任务 id；成功与错误提示齐全，ActionForm 把成功码当成功', () => {
  assert.ok(isResolutionCode('upheld') && isResolutionCode('regrade_required'));
  assert.ok(!isResolutionCode('Upheld') && !isResolutionCode('') && !isResolutionCode('a'.repeat(49)) && !isResolutionCode('up held'));
  assert.ok(isTaskId('5c0e0000-0000-4000-8000-0000000000a3') && !isTaskId('../take'));
  for (const code of ['taken', 'released', 'resolved', 'RULE_VIOLATION', 'FORBIDDEN', 'NOT_FOUND', 'INVALID_PARAMETER', 'UNAUTHENTICATED', 'INTERNAL']) {
    assert.ok(Object.hasOwn(QUEUE_RESULT_LABEL, code), code);
  }
  const form = readFileSync(new URL('../src/app/review/ActionForm.tsx', import.meta.url), 'utf8');
  for (const code of ['taken', 'released', 'resolved']) assert.ok(form.includes(`'${code}'`), code);
});

// ---------- 冲刺 4 · G 流第二部分 PR D：内容版本、考情、页面签字与事后抽检 ----------
test('PR D：风险理由码都有中文说明（与后端 risk.py 一致），新错误码有提示、发布失败回滚的说明透传', () => {
  const root = new URL('../../../', import.meta.url).pathname;
  const risk = readFileSync(join(root, 'services/learning/review_console/risk.py'), 'utf8');
  const reasons = new Set([...risk.matchAll(/Risk\("(?:low|medium|high)", "([a-z_]+)"/g)].map((m) => m[1]!));
  for (const m of risk.matchAll(/: "(policy_[a-z_]+|content_[a-z_]+)"/g)) reasons.add(m[1]!);
  for (const r of reasons) {
    if (r.startsWith('node_') || r.startsWith('change_')) continue;  // 地图版本的节点与变更单理由由类型名拼出，界面显示原码
    assert.ok(Object.hasOwn(REASON_LABEL, r), r);
  }
  for (const code of ['PUBLISH_FAILED_UNSIGNED', 'PAGES_EXPORT_UNAVAILABLE', 'applied', 'recorded', 'SELF_SIGN', 'AUTHOR_UNKNOWN',
    'SPOT_CHECK_UNCONFIGURED']) assert.ok(Object.hasOwn(RESULT_LABEL, code), code);
  // 后端 service.py 抛出的每个错误码都有中文提示（提交方不能自签、作者判不出、抽检没配种子）
  const svc = readFileSync(join(root, 'services/learning/review_console/service.py'), 'utf8');
  for (const m of svc.matchAll(/ReviewError\(\d{3}, "([A-Z_]+)"/g)) assert.ok(Object.hasOwn(RESULT_LABEL, m[1]!), m[1]!);
  // 对账问题码都有中文说明（含 self_approval）
  const pagesPy = readFileSync(join(root, 'services/learning/review_console/pages.py'), 'utf8');
  for (const m of pagesPy.matchAll(/problems\.append\("([a-z_]+)"\)/g)) assert.ok(Object.hasOwn(PAGE_PROBLEM_LABEL, m[1]!), m[1]!);
  assert.equal(problemFrom(409, { code: 'PUBLISH_FAILED_UNSIGNED', detail: '签字没有保存（来源文件未发布）' }).detail, '签字没有保存（来源文件未发布）');
  const form = readFileSync(new URL('../src/app/review/ActionForm.tsx', import.meta.url), 'utf8');
  assert.ok(form.includes("'applied'") && form.includes("'recorded'"));
});

test('PR D：准入、考情、抽检表单字段校验与后端一致', () => {
  assert.ok(isSourceRef('SYN-SRC-RC-1') && isSourceRef('a:b.c_d-1') && !isSourceRef('-x') && !isSourceRef('a b') && !isSourceRef('x'.repeat(65)));
  assert.ok(isItemId('SYN-RC-I1') && !isItemId('SYN:RC') && !isItemId(''));
  assert.ok(isReviewLevel('Q2') && !isReviewLevel('Q6') && !isReviewLevel('q2'));
  assert.ok(isCity('synthcity') && isCity('synth-rc') && !isCity('Bad City') && !isCity('x'));
  assert.ok(isCommit('a'.repeat(40)) && !isCommit('A'.repeat(40)) && !isCommit('a'.repeat(39)));
  assert.ok(isDay('2026-10-02') && !isDay('2026-13-40') && !isDay('yesterday'));
  for (const a of ADMISSIONS) assert.ok(Object.hasOwn(ADMISSION_LABEL, a), a);
  for (const k of ['source_document', 'exam_scheme', 'admission_line', 'school', 'subject', 'batch', 'total']) {
    assert.ok(Object.hasOwn(POLICY_OBJECT_LABEL, k), k);
  }
  // 与迁移 0039 的来源类型、授权状态枚举一致
  const root = new URL('../../../', import.meta.url).pathname;
  const mig = readFileSync(join(root, 'db/migrations/0039_platform__content_release.up.sql'), 'utf8');
  for (const t of SOURCE_TYPES) assert.ok(mig.includes(`'${t}'`), t);
  for (const t of LICENSE_STATUSES) assert.ok(mig.includes(`'${t}'`), t);
});

test('复核队列页面不取、不显示学生 id 与作答原文（只用 student_ref）', () => {
  for (const f of ['../src/app/review/queue/page.tsx', '../src/app/review/queue/[id]/page.tsx']) {
    const text = readFileSync(new URL(f, import.meta.url), 'utf8');
    assert.ok(text.includes('student_ref'), f);
    assert.ok(!/student_id|payload|response_text|\.text\b/.test(text), f);
  }
});
