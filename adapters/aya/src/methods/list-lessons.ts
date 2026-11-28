// 讲评课目录（契约 v1.1 草稿内追加的只读方法 list_lessons，specs/lesson-catalog PR A）：从已发布的讲评课内容包投影目录条目。
// 只有讲评课引用、节点、题（可选）、标题与步数；不含讲解 DSL、台词与预制答疑（讲解内容只经 teach 的 lesson_ref 按节下发并计量）。
// 标题即讲解 DSL 的标题（packages/stage 已按标题上限与文字安全读入；契约口径更严，不合契约的由适配器的响应自检挡下）。
import type { LoadedLessons } from "../lessons.ts";

export const LESSON_LIST_ALGORITHM = "aya-lesson-list@0.1.0";

export interface LessonCatalogEntry {
  lesson_ref: { lesson_id: string; lesson_version: string };
  skill_node: string;
  item_ref?: string;
  title: string;
  step_count: number;
}

/** 按（编号, 版本）排序；同一内容包两次投影逐字节相同 */
export function listLessons(lessons: LoadedLessons): LessonCatalogEntry[] {
  return [...lessons.byRef.values()]
    .map((l) => ({
      lesson_ref: { lesson_id: l.lesson_id, lesson_version: l.lesson_version },
      skill_node: `aya:${l.skillNode}`,
      ...(l.itemId ? { item_ref: `aya:${l.itemId}` } : {}),
      title: l.dsl.title,
      step_count: l.dsl.steps.length,
    }))
    .sort((a, b) => (a.lesson_ref.lesson_id < b.lesson_ref.lesson_id ? -1 : a.lesson_ref.lesson_id > b.lesson_ref.lesson_id ? 1
      : a.lesson_ref.lesson_version < b.lesson_ref.lesson_version ? -1 : a.lesson_ref.lesson_version > b.lesson_ref.lesson_version ? 1 : 0));
}
