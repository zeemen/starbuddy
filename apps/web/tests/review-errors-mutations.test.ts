// 审核台错误码核对的变异（specs/review-console-error-codes）：把 review.ts 或导出文件改坏放进临时目录，断言 tests/review-errors.test.ts 失败；
// 先跑一次不改的对照组，必须通过。
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';

const WEB = new URL('..', import.meta.url).pathname;
const SRC = readFileSync(`${WEB}src/lib/review.ts`, 'utf8');
const JSON_TEXT = readFileSync(`${WEB}src/lib/review-api.errors.json`, 'utf8');

function run(src: string, json: string): number {
  const dir = mkdtempSync(join(tmpdir(), 'review-err-mut.'));
  try {
    writeFileSync(join(dir, 'review.ts'), src);
    writeFileSync(join(dir, 'errors.json'), json);
    const r = spawnSync(process.execPath, ['--no-warnings', '--test', '--test-reporter=dot', `${WEB}tests/review-errors.test.ts`], {
      cwd: WEB, encoding: 'utf8', timeout: 120_000,
      env: { PATH: process.env['PATH'] ?? '', NODE_ENV: 'test', REVIEW_ERRORS_SRC: join(dir, 'review.ts'), REVIEW_ERRORS_JSON: join(dir, 'errors.json') },
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
  ['删掉一个错误码的提示', () => [once(SRC, " MISDIRECTED: '请求地址不对，请联系技术负责人',", ''), JSON_TEXT]],
  ['后端加了错误码、前端没有提示', () => {
    const doc = JSON.parse(JSON_TEXT) as { codes: Record<string, number[]> };
    doc.codes['RATE_LIMITED'] = [429];
    return [SRC, JSON.stringify(doc)];
  }],
  ['显示说明的码后端不会发', () => [once(SRC, "'NOT_FOUND', 'PUBLISH_FAILED_UNSIGNED']);", "'NOT_FOUND', 'PUBLISH_FAILED_UNSIGNED', 'STALE']);"), JSON_TEXT]],
];

test('对照组：不改时核对通过', () => {
  assert.equal(run(SRC, JSON_TEXT), 0);
});

for (const [name, make] of MUTATIONS) {
  test(`变异被发现：${name}`, () => {
    const [src, json] = make();
    assert.equal(run(src, json), 1, `${name}：没有被 review-errors.test.ts 发现`);
  });
}
