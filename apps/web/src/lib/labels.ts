// 界面文案：学科中文名、状态文字、星空摘要。只描述本人状态，不含比较与评价（spec 第 4 节；禁语 E、G 类的精神）。
import type { Display, Sky, Star } from './star-map.ts';

/** 平台学科 → 页面上的学科名（待人确认 #9：阿鸦显示“数学”，知行本冲刺只有“道法”一片） */
export const SUBJECT_NAME: Record<string, string> = {
  aya: '数学',
  zhixing: '道法',
  wenli: '语文',
  echo: '英语',
};
export const subjectName = (subject: string): string => SUBJECT_NAME[subject] ?? subject;

const DISPLAY_TEXT: Record<Display, string> = { unlit: '未亮', half: '半亮', lit: '亮' };

export function statusText(star: Pick<Star, 'display' | 'retained'>): string {
  return star.display === 'lit' && star.retained ? '亮·保持' : DISPLAY_TEXT[star.display];
}

/** 星空的无障碍摘要，例如“数学星空：亮 12 颗，半亮 30 颗，未亮 917 颗” */
export function skySummaryLabel(sky: Pick<Sky, 'subject' | 'summary'>): string {
  const s = sky.summary;
  return `${subjectName(sky.subject)}星空：亮 ${s.lit} 颗，半亮 ${s.half} 颗，未亮 ${s.unlit} 颗`;
}
