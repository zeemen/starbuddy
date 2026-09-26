// 规范化 JSON 与学习服务（Python）逐字节一致：读共享测试向量（spec 验收标准第 4 条）
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { canonical, sha256Hex } from '../src/metering/canonical.ts';
import { meteringFingerprint } from '../src/metering/fingerprint.ts';
import { imageFingerprint, itemFingerprint } from '../src/metering/content-fingerprint.ts';
import { isUuidV4, usageIdFor, uuidv5 } from '../src/metering/uuid.ts';

const VECTORS = new URL('../../../packages/contracts/testdata/canonical-vectors.json', import.meta.url);
const doc = JSON.parse(readFileSync(VECTORS, 'utf8')) as {
  vectors: { name: string; input: string; canonical: string; sha256: string }[];
};

test('共享向量：canonical 与 Python 逐字节一致', () => {
  assert.ok(doc.vectors.length >= 9);
  for (const v of doc.vectors) {
    const c = canonical(JSON.parse(v.input));
    assert.equal(c, v.canonical, v.name);
    assert.equal(sha256Hex(c), v.sha256, v.name);
  }
});

test('两种语言表示不同的值直接拒绝', () => {
  assert.throws(() => canonical(Number.NaN), TypeError);
  assert.throws(() => canonical({ a: Infinity }), TypeError);
  assert.throws(() => canonical(2 ** 53), TypeError);
  assert.throws(() => canonical('\ud800'), TypeError);
  assert.throws(() => canonical({ a: () => 1 }), TypeError);
});

test('uuidv5 与 RFC 4122 标准向量一致；usage_id 由 key 确定性派生', () => {
  assert.equal(uuidv5('6ba7b810-9dad-11d1-80b4-00c04fd430c8', 'www.example.com'), '2ed6657d-e927-568b-95e1-2665a8aea6a2');
  const k = 'b0b00000-0000-4000-8000-000000000001';
  assert.equal(usageIdFor(k), usageIdFor(k));
  assert.match(usageIdFor(k), /^[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  assert.ok(isUuidV4(k));
  assert.ok(!isUuidV4(k.toUpperCase()));
  assert.ok(!isUuidV4('b0b00000-0000-1000-8000-000000000001'));
});

test('计量指纹：不含 request_id 与 usage_id；appeal 缺省等于 false', () => {
  const base = { context: { subject: 'aya', idempotency_key: 'k', request_id: 'r1', usage_id: 'u1' }, attempt: { x: 1 } };
  const a = meteringFingerprint('grade', base);
  const b = meteringFingerprint('grade', { ...base, context: { ...base.context, request_id: 'r2', usage_id: 'u2' }, appeal: false });
  assert.equal(a, b);
  assert.notEqual(a, meteringFingerprint('grade', { ...base, attempt: { x: 2 } }));
  assert.notEqual(a, meteringFingerprint('diagnose', base));
  assert.match(a, /^[0-9a-f]{64}$/);
});

test('内容指纹满足计量表的格式约束', () => {
  const re = /^[A-Za-z0-9:_-]{8,128}$/;
  assert.match(imageFingerprint(new Uint8Array([1, 2, 3])), re);
  assert.equal(itemFingerprint('aya:synthetic-0301-07'), 'item:aya:synthetic-0301-07');
  assert.match(itemFingerprint('含中文的自由文本'), re);
  assert.match(itemFingerprint('x'.repeat(300)), re);
});
