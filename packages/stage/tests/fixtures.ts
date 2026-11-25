// 合成的讲解 DSL 夹具（规则 8：全部是合成内容，不含任何学生数据；文字只为覆盖各类动作，不是讲解台词）。

export const SAMPLE = {
  dsl_version: '0.2.0',
  title: '（合成样例）一元一次方程',
  canvas: { width: 960, height: 440 },
  roles: ['teacher', 'buddy'],
  narrated: false,
  steps: [
    { id: 's1', actions: [
      { type: 'speaker', role: 'teacher' },
      { type: 'write_text', id: 't1', x: 40, y: 60, text: '（合成）解方程', size: 'lg' },
    ] },
    { id: 's2', actions: [
      { type: 'write_formula', id: 'f1', x: 40, y: 140, latex: '2x + 3 = 7' },
      { type: 'draw_line', id: 'l1', x1: 40, y1: 160, x2: 400, y2: 160 },
    ] },
    { id: 's3', actions: [
      { type: 'write_formula', id: 'f2', x: 40, y: 220, latex: '2x = 7 - 3' },
      { type: 'highlight', id: 'h1', target: 'f2' },
    ] },
    { id: 's4', actions: [
      { type: 'speaker', role: 'buddy' },
      { type: 'write_formula', id: 'f3', x: 40, y: 300, latex: 'x = \\frac{4}{2} = 2', size: 'sm' },
      { type: 'draw_shape', id: 'b1', shape: 'rect', x: 30, y: 266, w: 260, h: 48 },
    ] },
    { id: 's5', actions: [
      { type: 'clear', targets: ['f2'] },
      { type: 'draw_shape', id: 'e1', shape: 'ellipse', x: 600, y: 80, w: 200, h: 120 },
      { type: 'draw_line', id: 'l2', x1: 600, y1: 260, x2: 800, y2: 260, dashed: true },
      { type: 'write_text', id: 't2', x: 600, y: 330, text: '（合成）x < 3 & y > 1', size: 'sm' },
      { type: 'speaker', role: 'none' },
    ] },
  ],
} as const;

/** 可随意修改的深拷贝（夹具本身不动） */
export const sample = (): Record<string, any> => JSON.parse(JSON.stringify(SAMPLE));

/** 只有老师、一步、一个动作的最小合格 DSL */
export const minimal = (actions: unknown[] = [{ type: 'write_text', id: 'a', x: 10, y: 40, text: '（合成）' }]): Record<string, any> => ({
  dsl_version: '0.2.0', title: '（合成）最小', canvas: { width: 640, height: 360 }, roles: ['teacher'], narrated: false, steps: [{ id: 's1', actions }],
});

/** v0.1.0 写法的同一份样例（没有 narrated）：用来核对 0.1.0 → 0.2.0 的真实迁移 */
export const SAMPLE_V01 = (() => {
  const { narrated: _n, ...rest } = JSON.parse(JSON.stringify(SAMPLE)) as Record<string, unknown>;
  return { ...rest, dsl_version: '0.1.0' };
})();

/**
 * 有台词的 v0.2 样例（narrated: true）与它的台词稿：说 / 画 / 指对齐，含一个空槽位（题图）与两种指向。
 * 台词是明显的合成占位，不是人设话术（规则 7：真实台词走人设审核）。
 */
export const NARRATED = {
  dsl_version: '0.2.0',
  title: '（合成样例）说画指',
  canvas: { width: 960, height: 440 },
  roles: ['teacher', 'buddy'],
  narrated: true,
  slots: [{ id: 'fig', kind: 'item_figure', x: 600, y: 40, w: 320, h: 200 }],
  steps: [
    { id: 's1', say: 'l1', actions: [
      { type: 'speaker', role: 'teacher' },
      { type: 'point', target: 'fig', mode: 'spotlight' },
    ] },
    { id: 's2', say: 'l2', actions: [{ type: 'write_formula', id: 'f1', x: 40, y: 80, latex: '2x + 3 = 7' }] },
    { id: 's3', say: 'l3', actions: [{ type: 'point', target: 'f1', mode: 'laser' }] },
    { id: 's4', say: 'l4', actions: [{ type: 'speaker', role: 'buddy' }, { type: 'write_formula', id: 'f2', x: 40, y: 160, latex: '2x = 4' }] },
    { id: 's5', actions: [{ type: 'speaker', role: 'none' }] },
  ],
} as const;

export const NARRATED_SCRIPT = {
  dsl_version: '0.2.0',
  lines: [
    { id: 'l1', role: 'teacher', text: '（合成台词）先看题图。' },
    { id: 'l2', role: 'teacher', text: '（合成台词）写下等式。' },
    { id: 'l3', role: 'teacher', text: '（合成台词）注意这一行。' },
    { id: 'l4', role: 'buddy', text: '（合成台词）两边减三。' },
  ],
} as const;

export const narrated = (): Record<string, any> => JSON.parse(JSON.stringify(NARRATED));
export const narratedScript = (): Record<string, any> => JSON.parse(JSON.stringify(NARRATED_SCRIPT));
