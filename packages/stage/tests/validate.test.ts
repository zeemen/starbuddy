// 校验器正反例（spec 范围 1、验收“非法 DSL 被拒”）：字段白名单、上限、拒绝 URL / HTML / 外链 / 脚本、坐标在画布内、
// id 唯一、高亮与擦除只指向现有元素、讲解权只给出场角色；合格时重建、冻结、登记，结果确定。全部是合成数据。
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { isValidated, LIMITS, MAX_ERRORS, validateStageDsl, type DslError } from '../src/index.ts';
import { minimal, sample } from './fixtures.ts';

const cp = (n: number) => String.fromCodePoint(n);
const errorsOf = (v: unknown): DslError[] => {
  const r = validateStageDsl(v);
  assert.equal(r.ok, false, `应当被拒：${(() => { try { return String(JSON.stringify(v)).slice(0, 200); } catch { return '（不可序列化）'; } })()}`);
  return r.ok ? [] : r.errors;
};
const rejects = (v: unknown, code: string, path?: string) => {
  const errs = errorsOf(v);
  assert.ok(errs.some((e) => e.code === code && (path === undefined || e.path === path)),
    `期望 ${code}${path ? ` @ ${path}` : ''}，实际 ${JSON.stringify(errs)}`);
};
const text = (t: string, extra: Record<string, unknown> = {}) => minimal([{ type: 'write_text', id: 'a', x: 10, y: 40, text: t, ...extra }]);
const formula = (latex: string, extra: Record<string, unknown> = {}) => minimal([{ type: 'write_formula', id: 'a', x: 10, y: 40, latex, ...extra }]);

test('合格样例：通过，重建出新对象，深冻结并登记；同一输入结果相同', () => {
  const input = sample();
  const r = validateStageDsl(input);
  assert.ok(r.ok, JSON.stringify(!r.ok && r.errors));
  assert.notEqual(r.dsl, input);
  assert.deepEqual(JSON.parse(JSON.stringify(r.dsl)), input);
  assert.ok(Object.isFrozen(r.dsl) && Object.isFrozen(r.dsl.steps[0]) && Object.isFrozen(r.dsl.steps[0]!.actions[0]) && Object.isFrozen(r.dsl.canvas));
  assert.ok(isValidated(r.dsl));
  assert.ok(!isValidated(input));
  assert.ok(!isValidated(JSON.parse(JSON.stringify(r.dsl))), '复制出来的对象不算已校验');
  const r2 = validateStageDsl(sample());
  assert.equal(JSON.stringify(r2.ok && r2.dsl), JSON.stringify(r.dsl));
  // 反例的错误列表也逐字相同
  const bad = sample(); bad['steps'][1].actions[0].latex = '\\unknown'; bad['title'] = '';
  assert.deepEqual(errorsOf(bad), errorsOf(JSON.parse(JSON.stringify(bad))));
});

test('最小合格 DSL 与边界值通过', () => {
  assert.ok(validateStageDsl(minimal()).ok);
  // 单行正好 60 个码点
  assert.ok(validateStageDsl({ ...minimal([{ type: 'write_text', id: 'a', x: 0, y: 24, text: '合'.repeat(LIMITS.maxLineChars), size: 'sm' }]),
    canvas: { width: 1440, height: 360 } }).ok);
  // 文字右边正好贴着画布右边：640 - 0.6*32*10 = 448
  assert.ok(validateStageDsl(minimal([{ type: 'write_text', id: 'a', x: 448, y: 32, text: 'abcdefghij' }])).ok);
  // 线段端点正好在画布角上；形状正好铺满画布
  assert.ok(validateStageDsl(minimal([{ type: 'draw_line', id: 'a', x1: 0, y1: 0, x2: 640, y2: 360 }])).ok);
  assert.ok(validateStageDsl(minimal([{ type: 'draw_shape', id: 'a', shape: 'rect', x: 0, y: 0, w: 640, h: 360 }])).ok);
  // 100 步、每步 5 个动作（合计 500）
  const many = minimal();
  many['steps'] = Array.from({ length: LIMITS.maxSteps }, (_, i) => ({ id: `s${i}`, actions: Array.from({ length: 5 }, (_, j) =>
    ({ type: 'draw_line', id: `l${i}_${j}`, x1: 0, y1: 0, x2: 1, y2: 1 })) }));
  assert.ok(validateStageDsl(many).ok);
  // 20 个动作的步
  assert.ok(validateStageDsl(minimal(Array.from({ length: LIMITS.maxActionsPerStep }, () => ({ type: 'speaker', role: 'teacher' })))).ok);
});

test('顶层：不是对象、多余字段、缺字段、版本不对', () => {
  for (const v of [null, 1, 'x', [], [minimal()]]) rejects(v, 'not_object', '$');
  rejects({ ...minimal(), style: 'x' }, 'unknown_field', '$.style');
  rejects({ ...minimal(), script: 'alert(1)' }, 'unknown_field', '$.script');
  const { roles: _r, ...noRoles } = minimal();
  rejects(noRoles, 'missing_field', '$.roles');
  for (const v of ['0.2.0', '0.1', '1.0.0', 1]) rejects({ ...minimal(), dsl_version: v }, 'bad_version', '$.dsl_version');
  // __proto__ 作为自有字段（JSON.parse 产生）也算多余字段；非普通对象（类实例、Map）不算对象
  rejects(JSON.parse(`{"__proto__":{"x":1},${JSON.stringify(minimal()).slice(1)}`), 'unknown_field', '$.__proto__');
  class Fake { dsl_version = '0.1.0'; }
  rejects(new Fake(), 'not_object', '$');
  rejects(new Map(Object.entries(minimal())), 'not_object', '$');
});

test('标题、画布、角色', () => {
  rejects({ ...minimal(), title: '' }, 'empty', '$.title');
  rejects({ ...minimal(), title: '合'.repeat(LIMITS.maxTitleChars + 1) }, 'too_long', '$.title');
  rejects({ ...minimal(), title: '<i>x</i>' }, 'html', '$.title');
  rejects({ ...minimal(), canvas: { width: 100, height: 360 } }, 'bad_number', '$.canvas.width');
  rejects({ ...minimal(), canvas: { width: 640, height: 2000 } }, 'bad_number', '$.canvas.height');
  rejects({ ...minimal(), canvas: { width: 640.5, height: 360 } }, 'bad_number', '$.canvas.width');
  rejects({ ...minimal(), canvas: { width: 640, height: 360, background: 'url(x)' } }, 'unknown_field', '$.canvas.background');
  rejects({ ...minimal(), canvas: [640, 360] }, 'not_object', '$.canvas');
  for (const roles of [[], ['buddy'], ['teacher', 'teacher'], ['teacher', 'student'], 'teacher', ['teacher', 'buddy', 'x']]) {
    rejects({ ...minimal(), roles }, 'bad_role', '$.roles');
  }
});

test('步数与动作数上限', () => {
  rejects({ ...minimal(), steps: [] }, 'empty', '$.steps');
  rejects({ ...minimal(), steps: {} }, 'bad_type', '$.steps');
  const tooMany = minimal();
  tooMany['steps'] = Array.from({ length: LIMITS.maxSteps + 1 }, (_, i) => ({ id: `s${i}`, actions: [{ type: 'speaker', role: 'none' }] }));
  rejects(tooMany, 'too_many', '$.steps');
  rejects(minimal([]), 'empty', '$.steps[0].actions');
  rejects(minimal(Array.from({ length: LIMITS.maxActionsPerStep + 1 }, () => ({ type: 'speaker', role: 'none' }))), 'too_many', '$.steps[0].actions');
  const total = minimal();
  total['steps'] = Array.from({ length: 26 }, (_, i) => ({ id: `s${i}`, actions: Array.from({ length: 20 }, () => ({ type: 'speaker', role: 'none' })) }));
  rejects(total, 'too_many', '$.steps');
  rejects({ ...minimal(), steps: [{ id: 's1', actions: [{ type: 'speaker', role: 'none' }], caption: 'x' }] }, 'unknown_field', '$.steps[0].caption');
  rejects({ ...minimal(), steps: [{ id: 's1', actions: [{ type: 'speaker', role: 'none' }] }, { id: 's1', actions: [{ type: 'speaker', role: 'none' }] }] },
    'duplicate_id', '$.steps[1].id');
  rejects({ ...minimal(), steps: [{ id: 'S1', actions: [{ type: 'speaker', role: 'none' }] }] }, 'bad_id', '$.steps[0].id');
});

test('动作：未知类型与多余字段（不允许颜色、样式、链接、图片、脚本）', () => {
  for (const type of ['image', 'link', 'script', 'html', 'eval', 'live', 'WRITE_TEXT', 1]) rejects(minimal([{ type }]), 'bad_enum', '$.steps[0].actions[0].type');
  rejects(minimal(['write_text']), 'not_object', '$.steps[0].actions[0]');
  for (const extra of ['color', 'style', 'href', 'src', 'onclick', 'font', 'class']) {
    rejects(text('（合成）', { [extra]: 'x' }), 'unknown_field', `$.steps[0].actions[0].${extra}`);
  }
  rejects(minimal([{ type: 'write_text', id: 'a', x: 10, y: 40 }]), 'missing_field', '$.steps[0].actions[0].text');
  rejects(minimal([{ type: 'speaker' }]), 'missing_field', '$.steps[0].actions[0].role');
  rejects(text('（合成）', { size: 'xl' }), 'bad_enum', '$.steps[0].actions[0].size');
});

test('id：格式、元素 id 全文唯一（擦掉后也不能复用）', () => {
  for (const id of ['A', '1a', 'a-b', 'a b', '', 'a'.repeat(33), 3]) {
    rejects(minimal([{ type: 'write_text', id, x: 10, y: 40, text: '（合成）' }]), 'bad_id', '$.steps[0].actions[0].id');
  }
  const dup = minimal([{ type: 'write_text', id: 'a', x: 10, y: 40, text: '甲' }, { type: 'draw_line', id: 'a', x1: 0, y1: 0, x2: 9, y2: 9 }]);
  rejects(dup, 'duplicate_id', '$.steps[0].actions[1].id');
  const reuse = minimal([{ type: 'write_text', id: 'a', x: 10, y: 40, text: '甲' }, { type: 'clear' }, { type: 'write_text', id: 'a', x: 10, y: 40, text: '乙' }]);
  rejects(reuse, 'duplicate_id', '$.steps[0].actions[2].id');
});

test('文字：单行上限、空白、不可见字符、HTML、链接与脚本', () => {
  const P = '$.steps[0].actions[0].text';
  rejects(text('合'.repeat(LIMITS.maxLineChars + 1), { size: 'sm' }), 'too_long', P);
  rejects(text('   '), 'empty', P);
  rejects(text(''), 'empty', P);
  rejects(text(5 as unknown as string), 'bad_type', P);
  // 另有落单的代理项（Cs）、私用区（Co）、未分配码位（Cn）
  for (const c of [0x0a, 0x0d, 0x09, 0x00, 0x7f, 0x85, 0x200b, 0x200d, 0x200e, 0x2028, 0x2029, 0x202e, 0x2066, 0xfeff, 0xad,
    0xd800, 0xdfff, 0xe000, 0xf8ff, 0xf0000, 0x0378, 0xfdd0]) {
    rejects(text(`甲${cp(c)}乙`), 'invisible_char', P);
  }
  for (const t of ['<b>甲</b>', '</x>', '<!-- x -->', '<?xml', '<svg onload=1>', '&lt;', '&#60;', '&amp;', '<img src=x>']) rejects(text(t), 'html', P);
  for (const t of ['https://example.invalid', 'HTTP://A', 'ftp://a', '//cdn.example.invalid/a.js', 'www.example', 'javascript:alert(1)',
    'JavaScript :x', 'data:text/html,1', 'vbscript:x', 'file:x', 'mailto:a', 'blob:x', 'a onerror=1', 'onclick =x']) {
    rejects(text(t), 'link_or_script', P);
  }
  // 普通的比较符号与“&”不算 HTML
  assert.ok(validateStageDsl(text('（合成）x < 3 & y > 1')).ok);
});

test('公式：只认可排版的命令、可打印 ASCII、禁用命令、长度', () => {
  const P = '$.steps[0].actions[0].latex';
  assert.ok(validateStageDsl(formula('\\frac{1}{2} + x^2 \\le \\sqrt{3}')).ok);
  for (const l of ['\\href{https://x.invalid}{a}', '\\url{x}', '\\def\\a{1}', '\\input{f}', '\\includegraphics{a.png}', '中文', 'x\u00b2'.replace('\u00b2', cp(0xb2))]) {
    rejects(formula(l), 'bad_latex', P);
  }
  rejects(formula('\\unknown{x}'), 'bad_latex', P);
  rejects(formula('\\toString'), 'bad_latex', P);       // 原型链上的名字不是命令
  rejects(formula('\\constructor'), 'bad_latex', P);
  rejects(formula('x'.repeat(LIMITS.maxLatexChars + 1)), 'too_long', P);
  rejects(formula('x'.repeat(LIMITS.maxLineChars + 1), { size: 'sm' }), 'too_long', P);   // 排版后超过单行上限
  rejects(formula('http://a.invalid'), 'link_or_script', P);
  rejects(formula('x<y'), 'html', P);
  rejects(formula('   '), 'empty', P);
  rejects(formula(7 as unknown as string), 'bad_type', P);
});

test('坐标：整数、在画布内（文字按估算宽度）', () => {
  const A = '$.steps[0].actions[0]';
  for (const x of [1.5, '10', null, Number.NaN, Number.POSITIVE_INFINITY]) rejects(text('甲', { x }), 'bad_number', `${A}.x`);
  rejects(text('甲', { x: -1 }), 'out_of_canvas', A);
  rejects(text('甲', { y: 31 }), 'out_of_canvas', A);              // 基线以上放不下一个字号（md = 32）
  rejects(text('甲', { y: 361 }), 'out_of_canvas', A);
  rejects(text('abcdefghij', { x: 449, y: 32 }), 'out_of_canvas', A);  // 右边越界 1 个单位
  rejects(formula('x', { x: 640 }), 'out_of_canvas', A);
  rejects(minimal([{ type: 'draw_line', id: 'a', x1: 0, y1: 0, x2: 641, y2: 10 }]), 'out_of_canvas', A);
  rejects(minimal([{ type: 'draw_line', id: 'a', x1: 0, y1: -1, x2: 10, y2: 10 }]), 'out_of_canvas', A);
  rejects(minimal([{ type: 'draw_line', id: 'a', x1: 5, y1: 5, x2: 5, y2: 5 }]), 'empty', A);
  rejects(minimal([{ type: 'draw_line', id: 'a', x1: 0, y1: 0, x2: 10, y2: 10, dashed: 'yes' }]), 'bad_type', `${A}.dashed`);
  rejects(minimal([{ type: 'draw_shape', id: 'a', shape: 'rect', x: 1, y: 0, w: 640, h: 10 }]), 'out_of_canvas', A);
  rejects(minimal([{ type: 'draw_shape', id: 'a', shape: 'rect', x: 0, y: 0, w: 10, h: 361 }]), 'out_of_canvas', A);
  rejects(minimal([{ type: 'draw_shape', id: 'a', shape: 'ellipse', x: 0, y: 0, w: 0, h: 10 }]), 'empty', A);
  rejects(minimal([{ type: 'draw_shape', id: 'a', shape: 'path', x: 0, y: 0, w: 10, h: 10 }]), 'bad_enum', `${A}.shape`);
  rejects(minimal([{ type: 'draw_shape', id: 'a', shape: 'rect', x: 0, y: 0, w: 10, h: 1.2 }]), 'bad_number', `${A}.h`);
});

test('高亮与擦除只指向白板上现有的元素；擦掉元素时连同它的高亮', () => {
  const w = { type: 'write_text', id: 'a', x: 10, y: 40, text: '甲' };
  rejects(minimal([{ type: 'highlight', id: 'h', target: 'zz' }]), 'unknown_target', '$.steps[0].actions[0].target');
  rejects(minimal([w, { type: 'clear' }, { type: 'highlight', id: 'h', target: 'a' }]), 'unknown_target', '$.steps[0].actions[2].target');
  rejects(minimal([w, { type: 'highlight', id: 'h', target: 'a' }, { type: 'highlight', id: 'h2', target: 'h' }]), 'unknown_target');
  rejects(minimal([w, { type: 'clear', targets: ['zz'] }]), 'unknown_target', '$.steps[0].actions[1].targets[0]');
  rejects(minimal([w, { type: 'clear', targets: ['a', 'a'] }]), 'unknown_target', '$.steps[0].actions[1].targets[1]');
  rejects(minimal([w, { type: 'clear', targets: [] }]), 'empty', '$.steps[0].actions[1].targets');
  rejects(minimal([w, { type: 'clear', targets: 'a' }]), 'bad_type', '$.steps[0].actions[1].targets');
  rejects(minimal([w, { type: 'clear', targets: Array.from({ length: LIMITS.maxClearTargets + 1 }, () => 'a') }]), 'too_many');
  // 擦掉目标后，指向它的高亮也不在了：再擦高亮就是未知目标
  rejects(minimal([w, { type: 'highlight', id: 'h', target: 'a' }, { type: 'clear', targets: ['a'] }, { type: 'clear', targets: ['h'] }]),
    'unknown_target', '$.steps[0].actions[3].targets[0]');
  // 跨步也成立；单独擦高亮可以
  assert.ok(validateStageDsl({ ...minimal(), steps: [{ id: 's1', actions: [w, { type: 'highlight', id: 'h', target: 'a' }] },
    { id: 's2', actions: [{ type: 'clear', targets: ['h'] }, { type: 'highlight', id: 'h2', target: 'a' }] }] }).ok);
});

test('讲解权只给本节出场的角色', () => {
  rejects(minimal([{ type: 'speaker', role: 'buddy' }]), 'bad_role', '$.steps[0].actions[0].role');
  for (const role of ['student', 'parent', '', 1]) rejects(minimal([{ type: 'speaker', role }]), 'bad_role');
  assert.ok(validateStageDsl({ ...minimal([{ type: 'speaker', role: 'buddy' }]), roles: ['teacher', 'buddy'] }).ok);
  assert.ok(validateStageDsl(minimal([{ type: 'speaker', role: 'none' }])).ok);
});

test('错误数有上限；畸形输入不拖慢校验', () => {
  const bad = minimal(Array.from({ length: 20 }, () => ({ type: 'write_text', id: 'A', x: 1.5, y: 'y', text: '<b>' })));
  bad['steps'] = Array.from({ length: 20 }, (_, i) => ({ ...bad['steps'][0], id: `s${i}` }));
  assert.equal(errorsOf(bad).length, MAX_ERRORS);
  const t0 = performance.now();
  validateStageDsl({ ...minimal(), steps: Array.from({ length: 1e6 }, () => ({})) });
  // 只防平方级退化（正常约 0.3 秒）；上限留足余量，避免机器负载高时误报
  assert.ok(performance.now() - t0 < 5000);
});

/** 每次读取返回不同值的字段：第一次是合格值，之后是恶意值（检查与重建若各读一次，恶意值就会混进结果） */
function flipping(obj: Record<string, unknown>, key: string, first: unknown, later: unknown): { reads: () => number } {
  let n = 0;
  Object.defineProperty(obj, key, { enumerable: true, configurable: true, get: () => (n++ === 0 ? first : later) });
  return { reads: () => n };
}

test('入口先取纯数据快照：getter 只读一次，检查与重建用同一个值（text、title、x1）', () => {
  const t = minimal();
  const textGetter = flipping(t['steps'][0].actions[0], 'text', '（合成）', '<script>x</script>');
  const titleGetter = flipping(t, 'title', '（合成）标题', '<b>x</b>');
  const r = validateStageDsl(t);
  assert.ok(r.ok, JSON.stringify(!r.ok && r.errors));
  assert.equal((r.dsl.steps[0]!.actions[0] as { text: string }).text, '（合成）');
  assert.equal(r.dsl.title, '（合成）标题');
  assert.equal(textGetter.reads(), 1);
  assert.equal(titleGetter.reads(), 1);

  const l = minimal([{ type: 'draw_line', id: 'a', y1: 0, x2: 10, y2: 10 }]);
  const x1 = flipping(l['steps'][0].actions[0], 'x1', 5, 99999);
  const rl = validateStageDsl(l);
  assert.ok(rl.ok, JSON.stringify(!rl.ok && rl.errors));
  assert.equal((rl.dsl.steps[0]!.actions[0] as { x1: number }).x1, 5);
  assert.equal(x1.reads(), 1);
});

test('入口先取纯数据快照：Proxy 的读取陷阱只在快照时触发，结果与快照一致', () => {
  let reads = 0;
  const action = new Proxy({ type: 'write_text', id: 'a', x: 10, y: 40, text: '（合成）' } as Record<string, unknown>, {
    get(target, key, receiver) {
      if (key === 'text') return reads++ === 0 ? '（合成）' : 'javascript:alert(1)';
      if (key === 'x') return reads > 1 ? 99999 : 10;
      return Reflect.get(target, key, receiver);
    },
  });
  const r = validateStageDsl(minimal([action]));
  assert.ok(r.ok, JSON.stringify(!r.ok && r.errors));
  assert.deepEqual(JSON.parse(JSON.stringify(r.dsl.steps[0]!.actions[0])), { type: 'write_text', id: 'a', x: 10, y: 40, text: '（合成）' });
  assert.equal(reads, 1);
  // 顶层本身是 Proxy 也一样
  let topReads = 0;
  const top = new Proxy(minimal(), { get(t, k, rcv) { if (k === 'title') return topReads++ === 0 ? '（合成）' : '<i>x</i>'; return Reflect.get(t, k, rcv); } });
  const rt = validateStageDsl(top);
  assert.ok(rt.ok && rt.dsl.title === '（合成）' && topReads === 1);
});

test('不可序列化的输入直接拒绝', () => {
  const cyc = minimal();
  cyc['steps'][0].actions[0].self = cyc;
  assert.deepEqual(errorsOf(cyc), [{ path: '$', code: 'not_serializable' }]);
  assert.deepEqual(errorsOf({ ...minimal(), canvas: { width: 10n, height: 360 } }), [{ path: '$', code: 'not_serializable' }]);
  const throwing = minimal();
  Object.defineProperty(throwing, 'title', { enumerable: true, get() { throw new Error('x'); } });
  assert.deepEqual(errorsOf(throwing), [{ path: '$', code: 'not_serializable' }]);
});
