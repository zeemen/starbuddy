// 内容指纹（24 小时复看用）：由 BFF 在调用学习服务之前算，不依赖适配器、不改契约（设计文档场景 6）。
// 格式 <来源>:<摘要>，满足 usage_reservation.content_fingerprint 的格式约束（字母数字与 : _ -，8–128 字符）。
import { createHash } from 'node:crypto';

const ITEM_REF = /^[A-Za-z0-9:_-]{1,120}$/;

export function imageFingerprint(bytes: Uint8Array): string {
  return 'img:' + createHash('sha256').update(bytes).digest('hex');
}

export function itemFingerprint(itemRef: string): string {
  // 题库题直接用 item_ref；超长或含其他字符时取摘要，避免把自由文本写进计量表
  if (ITEM_REF.test(itemRef) && itemRef.length + 5 >= 8) return 'item:' + itemRef;
  return 'item:' + createHash('sha256').update(itemRef, 'utf8').digest('hex');
}
