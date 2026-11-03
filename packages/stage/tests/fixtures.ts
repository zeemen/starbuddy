// 合成的讲解 DSL 夹具（规则 8：全部是合成内容，不含任何学生数据；文字只为覆盖各类动作，不是讲解台词）。

export const SAMPLE = {
  dsl_version: '0.1.0',
  title: '（合成样例）一元一次方程',
  canvas: { width: 960, height: 440 },
  roles: ['teacher', 'buddy'],
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
  dsl_version: '0.1.0', title: '（合成）最小', canvas: { width: 640, height: 360 }, roles: ['teacher'], steps: [{ id: 's1', actions }],
});
