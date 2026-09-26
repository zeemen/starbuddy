// 计量指纹：规范化请求体去掉 request_id 与 usage_id 后的 SHA-256（设计文档场景 3）。
// 去掉 usage_id：它由 key 派生、是预留的产物，指纹要在预留之前算。
// grade 的 appeal 缺省等于 false，与学习服务的归一规则相同，两种写法得到同一指纹。
import { canonical, sha256Hex } from './canonical.ts';

export type MeteredMethod = 'grade' | 'diagnose';

export function meteringFingerprint(method: MeteredMethod, body: Record<string, unknown>): string {
  // 按实际发出去的 JSON 计算：Date、带 toJSON 的对象先按 JSON.stringify 的规则转换
  const b = JSON.parse(JSON.stringify(body)) as Record<string, unknown>;
  const context = b['context'];
  if (context !== null && typeof context === 'object' && !Array.isArray(context)) {
    delete (context as Record<string, unknown>)['request_id'];
    delete (context as Record<string, unknown>)['usage_id'];
  }
  if (method === 'grade') b['appeal'] = b['appeal'] === true;
  return sha256Hex(canonical({ method, body: b }));
}
