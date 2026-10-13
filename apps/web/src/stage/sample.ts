// 舞台样张页（/dev/stage，只在开发、测试构建里存在）用的合成讲解 DSL（规则 8：全部合成，不含学生数据；文字只为演示各类动作，
// 不是讲解台词，也不是人设话术）。一份合格样例逐步播放，一份非法样例展示校验器的拒绝结果。

export const STAGE_SAMPLE = {
  dsl_version: '0.1.0',
  title: '（合成样例）一元一次方程',
  canvas: { width: 960, height: 440 },
  roles: ['teacher', 'buddy'],
  steps: [
    { id: 's1', actions: [
      { type: 'speaker', role: 'teacher' },
      { type: 'write_text', id: 't1', x: 40, y: 64, text: '（合成）解方程', size: 'lg' },
    ] },
    { id: 's2', actions: [
      { type: 'write_formula', id: 'f1', x: 40, y: 140, latex: '2x + 3 = 7' },
      { type: 'draw_line', id: 'l1', x1: 40, y1: 160, x2: 420, y2: 160 },
    ] },
    { id: 's3', actions: [
      { type: 'write_formula', id: 'f2', x: 40, y: 220, latex: '2x = 7 - 3' },
      { type: 'highlight', id: 'h1', target: 'f2' },
    ] },
    { id: 's4', actions: [
      { type: 'speaker', role: 'buddy' },
      { type: 'write_formula', id: 'f3', x: 40, y: 300, latex: 'x = \\frac{4}{2}' },
      { type: 'draw_shape', id: 'b1', shape: 'rect', x: 28, y: 262, w: 300, h: 52 },
    ] },
    { id: 's5', actions: [
      { type: 'speaker', role: 'teacher' },
      { type: 'clear', targets: ['f2'] },
      { type: 'write_formula', id: 'f4', x: 40, y: 380, latex: 'x = 2', size: 'lg' },
      { type: 'draw_shape', id: 'e1', shape: 'ellipse', x: 560, y: 90, w: 320, h: 150 },
      { type: 'write_text', id: 't2', x: 600, y: 176, text: '（合成）检验：2×2+3=7', size: 'sm' },
    ] },
    { id: 's6', actions: [
      { type: 'speaker', role: 'none' },
      { type: 'draw_line', id: 'l2', x1: 560, y1: 300, x2: 880, y2: 300, dashed: true },
    ] },
  ],
} as const;

/** 非法样例：外链、HTML、多余的样式字段、越界坐标、认不出的公式命令、给未出场角色讲解权 */
export const STAGE_INVALID_SAMPLE = {
  dsl_version: '0.1.0',
  title: '（合成）非法样例',
  canvas: { width: 640, height: 360 },
  roles: ['teacher'],
  steps: [
    { id: 's1', actions: [
      { type: 'write_text', id: 'a', x: 10, y: 40, text: '（合成）//example.invalid/a.js' },
      { type: 'write_text', id: 'b', x: 10, y: 80, text: '<b>（合成）</b>' },
      { type: 'draw_line', id: 'c', x1: 0, y1: 0, x2: 900, y2: 10, color: 'red' },
      { type: 'write_formula', id: 'd', x: 10, y: 120, latex: '\\href{x}{y}' },
      { type: 'speaker', role: 'buddy' },
    ] },
  ],
} as const;
