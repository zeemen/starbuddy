// T35 前端核对的变异（specs/review-console-types-t35 第三节第 4 条）：把 review.ts 或导出文件改坏放进临时目录，
// 断言 tests/review-requests.test.ts 失败（退出码 1）；先跑一次不改的对照组，必须通过。
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';

const WEB = new URL('..', import.meta.url).pathname;
const SRC = readFileSync(`${WEB}src/lib/review.ts`, 'utf8');
const JSON_TEXT = readFileSync(`${WEB}src/lib/review-api.requests.json`, 'utf8');

function run(src: string, json: string): number {
  const dir = mkdtempSync(join(tmpdir(), 'review-req-mut.'));
  try {
    writeFileSync(join(dir, 'review.ts'), src);
    writeFileSync(join(dir, 'requests.json'), json);
    const r = spawnSync(process.execPath, ['--no-warnings', '--test', '--test-reporter=dot', `${WEB}tests/review-requests.test.ts`], {
      cwd: WEB, encoding: 'utf8', timeout: 120_000,
      env: { PATH: process.env['PATH'] ?? '', NODE_ENV: 'test', REVIEW_REQUESTS_SRC: join(dir, 'review.ts'), REVIEW_REQUESTS_JSON: join(dir, 'requests.json') },
    });
    return r.status ?? -1;
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

const once = (text: string, from: string, to: string): string => {
  assert.equal(text.split(from).length, 2, `变异原文不存在或不唯一：${from}`);
  return text.replace(from, to);
};

const MUTATIONS: [string, () => [string, string]][] = [
  ['前端多一个字段', () => [once(SRC, "interface DigestReq { expected_digest: string }", "interface DigestReq { expected_digest: string; note?: string }"), JSON_TEXT]],
  ['前端把枚举写成 string', () => [once(SRC, "  'POST /review/v0/feedback/{feedback_id}/status': { status: FeedbackStatus };",
                                       "  'POST /review/v0/feedback/{feedback_id}/status': { status: string };"), JSON_TEXT]],
  ['前端必填写成可选', () => [once(SRC, "interface BasisReq { basis_code: string }", "interface BasisReq { basis_code?: string }"), JSON_TEXT]],
  ['前端少登记一个接口', () => [once(SRC, "  'POST /review/v0/policy/sources/retire': { host: string };\n", ""), JSON_TEXT]],
  ['调用路径与模板不符', () => [once(SRC, "`/review/v0/map-versions/${id}/retire`, body);", "`/review/v0/map-versions/${id}/retired`, body);"), JSON_TEXT]],
  ['导出文件过期（后端加了必填字段）', () => {
    const doc = JSON.parse(JSON_TEXT) as { routes: Record<string, Record<string, unknown>> };
    doc.routes['POST /review/v0/pages/sign']!['reason'] = { required: true, type: 'string', nullable: false, enum: null };
    return [SRC, JSON.stringify(doc)];
  }],
];

test('对照组：不改时核对通过', () => {
  assert.equal(run(SRC, JSON_TEXT), 0);
});

for (const [name, make] of MUTATIONS) {
  test(`变异被发现：${name}`, () => {
    const [src, json] = make();
    assert.equal(run(src, json), 1, `${name}：没有被 review-requests.test.ts 发现`);
  });
}
