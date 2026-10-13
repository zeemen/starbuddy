// 舞台三套坐标令牌的规则（视觉家族规范 v1.2 第六章）：白板 ≥ 65% 舞台高；老师右下、22% / 18%；点点左下、0.85 倍；
// 角色与对话条不压白板；全部在舞台内；讲话者 1.25 线。
import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  BUDDY_SCALE, LAYOUT_NAMES, lineUnit, MIN_WHITEBOARD_RATIO, SPEAKER_OUTLINE_LINES, OUTLINE_LINES, STAGE_LAYOUTS, TEACHER_HEIGHT_RATIO, type Rect,
} from '../src/index.ts';

const close = (a: number, b: number) => Math.abs(a - b) < 1e-9;
const overlap = (a: Rect, b: Rect) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

test('三套布局都满足舞台规则', () => {
  assert.deepEqual([...LAYOUT_NAMES], ['desktop', 'tablet', 'mobile']);
  for (const name of LAYOUT_NAMES) {
    const L = STAGE_LAYOUTS[name];
    const H = L.stage.height; const W = L.stage.width;
    assert.ok(L.whiteboard.h / H >= MIN_WHITEBOARD_RATIO, `${name} 白板占比 ${L.whiteboard.h / H}`);
    assert.ok(close(L.teacher.h, TEACHER_HEIGHT_RATIO[name] * H), `${name} 老师高度`);
    assert.ok(close(L.buddy.h, BUDDY_SCALE * L.teacher.h), `${name} 点点高度`);
    assert.ok(Math.abs(L.buddy.w - BUDDY_SCALE * L.teacher.w) <= 1, `${name} 点点宽度`);
    // 老师右下、点点左下（同一底线）
    assert.ok(L.teacher.x + L.teacher.w / 2 > W / 2 && L.teacher.y + L.teacher.h / 2 > H / 2, `${name} 老师在右下`);
    assert.ok(L.buddy.x + L.buddy.w / 2 < W / 2 && L.buddy.y + L.buddy.h / 2 > H / 2, `${name} 点点在左下`);
    assert.ok(close(L.teacher.y + L.teacher.h, L.buddy.y + L.buddy.h), `${name} 底线对齐`);
    for (const [part, r] of Object.entries({ whiteboard: L.whiteboard, teacher: L.teacher, buddy: L.buddy, dialogue: L.dialogue })) {
      assert.ok(r.x >= 0 && r.y >= 0 && r.x + r.w <= W && r.y + r.h <= H && r.w > 0 && r.h > 0, `${name} ${part} 在舞台内`);
    }
    for (const [a, b] of [['teacher', 'whiteboard'], ['buddy', 'whiteboard'], ['dialogue', 'whiteboard'], ['teacher', 'buddy'],
      ['teacher', 'dialogue'], ['buddy', 'dialogue']] as const) {
      assert.ok(!overlap(L[a], L[b]), `${name} ${a} 与 ${b} 不重叠`);
    }
  }
  assert.ok(TEACHER_HEIGHT_RATIO.desktop === 0.22 && TEACHER_HEIGHT_RATIO.tablet === 0.22 && TEACHER_HEIGHT_RATIO.mobile === 0.18);
  assert.equal(BUDDY_SCALE, 0.85);
  assert.equal(MIN_WHITEBOARD_RATIO, 0.65);
});

test('布局令牌深冻结：任何一层都改不了', () => {
  for (const name of LAYOUT_NAMES) {
    const L = STAGE_LAYOUTS[name];
    for (const part of [L, L.stage, L.whiteboard, L.teacher, L.buddy, L.dialogue]) assert.ok(Object.isFrozen(part), name);
    assert.throws(() => { (L.whiteboard as { h: number }).h = 1; }, TypeError);
    assert.throws(() => { (L.stage as { height: number }).height = 1; }, TypeError);
  }
  assert.ok(Object.isFrozen(STAGE_LAYOUTS) && Object.isFrozen(TEACHER_HEIGHT_RATIO));
  assert.equal(STAGE_LAYOUTS.desktop.whiteboard.h, 440);
});

test('线宽：1 线 = 角色身高的 1/48；讲话者主轮廓 1.25 线', () => {
  assert.equal(OUTLINE_LINES, 1);
  assert.equal(SPEAKER_OUTLINE_LINES, 1.25);
  assert.equal(lineUnit({ x: 0, y: 0, w: 10, h: 96 }), 2);
});
