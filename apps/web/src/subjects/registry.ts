// 壳内各科路由模块的清单（spec 第 18 节“前端合并壳，不合并页”）：学科码、状态、入口组件所在目录。
// 路由统一为 /s/{studentId}/learn/{subject}；科目页只调 BFF，不调原产品、不读适配器内容包（规则 1），不用 iframe 与跳转（frame-src 'none'）。
//   live：数学（4-H 只显示今日任务与该科星空入口；作答、诊断、批改等消耗类入口在冲刺 5 联调）；
//   coming：道法、语文（适配器与已发布内容版本都到位前是“即将开放”占位页，可进入并返回）；
//   hidden：英语（对照文档决定 #1），不显示入口，路由 404。
// 中文名取 src/shell/copy.ts 的 SUBJECT_LABEL（subject_catalog 没有中文名列）。
export type SubjectStatus = 'live' | 'coming' | 'hidden';

export interface SubjectModule { code: 'math' | 'chinese' | 'politics' | 'english'; status: SubjectStatus }

export const SUBJECT_MODULES: readonly SubjectModule[] = [
  { code: 'math', status: 'live' },
  { code: 'politics', status: 'coming' },
  { code: 'chinese', status: 'coming' },
  { code: 'english', status: 'hidden' },
];

export const visibleSubjects = (): SubjectModule[] => SUBJECT_MODULES.filter((m) => m.status !== 'hidden');
export const subjectModule = (code: string): SubjectModule | null =>
  SUBJECT_MODULES.find((m) => m.code === code && m.status !== 'hidden') ?? null;
