// 审核台错误码与前端提示（specs/review-console-error-codes）：后端能发出的每个错误码（src/lib/review-api.errors.json，由
// services/learning/scripts/export_review_errors.py 从后端源码与库错误映射导出）在 RESULT_LABEL 里都要有中文提示——查不到时页面只能落到
// “出错了，请稍后再试”；会显示后端说明的码（DETAIL_CODES）只能是后端真会发的码。各页在 RESULT_LABEL 之上覆盖的表不在这里核对。
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const WEB = new URL('..', import.meta.url).pathname;
// 变异测试（tests/review-errors-mutations.test.ts）经这两个变量指到改坏的副本
const SRC = process.env['REVIEW_ERRORS_SRC'] ?? `${WEB}src/lib/review.ts`;
const JSON_PATH = process.env['REVIEW_ERRORS_JSON'] ?? `${WEB}src/lib/review-api.errors.json`;

test('后端每个错误码在 RESULT_LABEL 里都有提示；DETAIL_CODES 只含后端会发的码', async () => {
  const codes = Object.keys((JSON.parse(readFileSync(JSON_PATH, 'utf8')) as { codes: Record<string, number[]> }).codes);
  assert.ok(codes.length >= 19, `导出的错误码只有 ${codes.length} 个`);
  const mod = (await import(SRC)) as { RESULT_LABEL: Record<string, string>; DETAIL_CODES: Set<string> };
  const missing = codes.filter((c) => !Object.hasOwn(mod.RESULT_LABEL, c) || mod.RESULT_LABEL[c]!.trim() === '');
  assert.deepEqual(missing, [], `这些后端错误码在 RESULT_LABEL 里没有提示（页面会显示成“出错了”）：${missing.join('、')}`);
  const stray = [...mod.DETAIL_CODES].filter((c) => !codes.includes(c));
  assert.deepEqual(stray, [], `DETAIL_CODES 里有后端不会发的码：${stray.join('、')}`);
});
