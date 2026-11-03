// JSON Schema（schema/stage-dsl-0.1.0.schema.json）与校验器口径一致：版本、字段白名单（必填 / 可选）、动作类型、枚举、上限、id 格式。
// 不引入 JSON Schema 校验库（零新依赖）：逐项对照常量；动态规则（唯一、坐标、引用）只在校验器里，schema 的 description 写明。
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { ACTION_FIELDS, ACTION_TYPES, DSL_VERSION, FONT_SIZE, ID_RE, LIMITS, ROLES } from '../src/index.ts';
import { CANVAS_FIELDS, STEP_FIELDS, TOP_FIELDS } from '../src/dsl.ts';

const schema = JSON.parse(readFileSync(new URL('../schema/stage-dsl-0.1.0.schema.json', import.meta.url), 'utf8'));
const defs = schema.$defs;
const sorted = (a: readonly string[]) => [...a].sort();

test('顶层、画布、步骤：字段白名单与上限', () => {
  assert.equal(schema.$schema, 'https://json-schema.org/draft/2020-12/schema');
  assert.equal(schema.properties.dsl_version.const, DSL_VERSION);
  assert.equal(schema.additionalProperties, false);
  assert.deepEqual(sorted(schema.required), sorted(TOP_FIELDS));
  assert.deepEqual(sorted(Object.keys(schema.properties)), sorted(TOP_FIELDS));
  assert.equal(schema.properties.title.maxLength, LIMITS.maxTitleChars);
  const cv = schema.properties.canvas;
  assert.equal(cv.additionalProperties, false);
  assert.deepEqual(sorted(cv.required), sorted(CANVAS_FIELDS));
  assert.deepEqual([cv.properties.width.minimum, cv.properties.width.maximum], [...LIMITS.canvasWidth]);
  assert.deepEqual([cv.properties.height.minimum, cv.properties.height.maximum], [...LIMITS.canvasHeight]);
  assert.deepEqual(schema.properties.roles.items.enum, [...ROLES]);
  assert.equal(schema.properties.roles.maxItems, ROLES.length);
  assert.equal(schema.properties.steps.maxItems, LIMITS.maxSteps);
  assert.equal(defs.step.additionalProperties, false);
  assert.deepEqual(sorted(defs.step.required), sorted(STEP_FIELDS));
  assert.equal(defs.step.properties.actions.maxItems, LIMITS.maxActionsPerStep);
  assert.equal(defs.id.pattern, ID_RE.source);
  assert.deepEqual(defs.size.enum, Object.keys(FONT_SIZE));
});

test('每个动作：类型、必填与可选字段、上限与枚举', () => {
  const refs = defs.action.oneOf.map((r: { $ref: string }) => r.$ref.replace('#/$defs/', ''));
  assert.deepEqual(refs, [...ACTION_TYPES]);
  for (const t of ACTION_TYPES) {
    const d = defs[t];
    assert.equal(d.additionalProperties, false, t);
    assert.equal(d.properties.type.const, t);
    assert.deepEqual(sorted(d.required), sorted(ACTION_FIELDS[t].required), `${t} 必填`);
    assert.deepEqual(sorted(Object.keys(d.properties)), sorted([...ACTION_FIELDS[t].required, ...ACTION_FIELDS[t].optional]), `${t} 字段`);
  }
  assert.equal(defs.write_text.properties.text.maxLength, LIMITS.maxLineChars);
  assert.equal(defs.write_formula.properties.latex.maxLength, LIMITS.maxLatexChars);
  assert.equal(defs.clear.properties.targets.maxItems, LIMITS.maxClearTargets);
  assert.deepEqual(defs.speaker.properties.role.enum, [...ROLES, 'none']);
  assert.deepEqual(defs.draw_shape.properties.shape.enum, ['rect', 'ellipse']);
});

test('schema 里没有允许链接、样式或任意字段的口子', () => {
  const text = JSON.stringify(schema);
  assert.ok(!/"format"\s*:\s*"(uri|url|iri)/.test(text));
  assert.ok(!/"additionalProperties"\s*:\s*true/.test(text));
  const walk = (o: unknown): void => {
    if (typeof o !== 'object' || o === null) return;
    const r = o as Record<string, unknown>;
    if (r['type'] === 'object') assert.equal(r['additionalProperties'], false, JSON.stringify(r).slice(0, 80));
    Object.values(r).forEach(walk);
  };
  walk(schema);
});
