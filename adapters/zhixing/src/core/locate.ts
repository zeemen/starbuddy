// M2 开卷定位的确定性判定（纯函数）。
//
// 移植自 legacy（只读）：legacy/wenli/初中语文_AI_class/wenli/packages/shared/lib/domain/daofa-locate.ts
//   （scoreLocate 的对错判定、locateSignalRate）。
// 没有移植的部分：coaching 文案（学长口吻“我当年……”、快慢评价，属禁语 G 类，ZHIXING-KD-10）、
//   与“学长用时”的快慢比较（faster）、节点名称与页码（来自来源待核的知识图谱，决定 #5）。
// M2 定位题判分不在本任务范围（学科码待映射表签字，spec“不做”），本函数暂未接入任何方法。

export interface LocateItemRef {
  answer_node_id: string;
}

export interface LocateVerdict {
  correct: boolean;
}

export function scoreLocate(item: LocateItemRef, pickedNodeId: string): LocateVerdict {
  return { correct: pickedNodeId === item.answer_node_id };
}

/** 定位力信号：答对 = 1，答错 = 0 */
export function locateSignalRate(correct: boolean): number {
  return correct ? 1 : 0;
}
