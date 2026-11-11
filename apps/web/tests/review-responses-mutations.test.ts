// 审核台响应类型核对的变异（specs/review-console-responses 第二节第 5 条）：把 review.ts 或导出文件改坏放进临时目录，
// 断言 tests/review-responses.test.ts 失败（退出码 1）；先跑一次不改的对照组，必须通过。
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';

const WEB = new URL('..', import.meta.url).pathname;
const SRC = readFileSync(`${WEB}src/lib/review.ts`, 'utf8');
const JSON_TEXT = readFileSync(`${WEB}src/lib/review-api.responses.json`, 'utf8');

function run(src: string, json: string): number {
  const dir = mkdtempSync(join(tmpdir(), 'review-resp-mut.'));
  try {
    writeFileSync(join(dir, 'review.ts'), src);
    writeFileSync(join(dir, 'responses.json'), json);
    const r = spawnSync(process.execPath, ['--no-warnings', '--test', '--test-reporter=dot', `${WEB}tests/review-responses.test.ts`], {
      cwd: WEB, encoding: 'utf8', timeout: 120_000,
      env: { PATH: process.env['PATH'] ?? '', NODE_ENV: 'test', REVIEW_RESPONSES_SRC: join(dir, 'review.ts'), REVIEW_RESPONSES_JSON: join(dir, 'responses.json') },
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
  ['前端少一个字段', () => [once(SRC, '  error_type: string | null;\n', ''), JSON_TEXT]],
  ['可空写成不可空', () => [once(SRC, "'GET /review/v0/auth/me': { account_id: string; login_name: string | null };",
                               "'GET /review/v0/auth/me': { account_id: string; login_name: string };"), JSON_TEXT]],
  ['枚举少一个取值', () => [once(SRC, "  risk: 'low' | 'medium' | 'high';\n  required: number;", "  risk: 'low' | 'high';\n  required: number;"), JSON_TEXT]],
  ['后端没有枚举，前端写成字面量联合', () => [once(SRC, '  id: string;\n  level: string;\n  parent_id', "  id: string;\n  level: 'leaf' | 'branch';\n  parent_id"), JSON_TEXT]],
  ['少登记一个接口', () => [once(SRC, "  'POST /review/v0/queue/{task_id}/release': StatusOut;\n", ''), JSON_TEXT]],
  ['调用路径与模板不符', () => [once(SRC, '`/review/v0/queue/${id}/release`', '`/review/v0/queue/${id}/released`'), JSON_TEXT]],
  ['导出文件过期（后端加了必填字段）', () => {
    const doc = JSON.parse(JSON_TEXT) as { routes: Record<string, { fields: Record<string, unknown> }> };
    doc.routes['POST /review/v0/pages/sign']!.fields['reason'] = { required: true, type: 'string', nullable: false, enum: null };
    return [SRC, JSON.stringify(doc)];
  }],
];

test('对照组：不改时核对通过', () => {
  assert.equal(run(SRC, JSON_TEXT), 0);
});

for (const [name, make] of MUTATIONS) {
  test(`变异被发现：${name}`, () => {
    const [src, json] = make();
    assert.equal(run(src, json), 1, `${name}：没有被 review-responses.test.ts 发现`);
  });
}
