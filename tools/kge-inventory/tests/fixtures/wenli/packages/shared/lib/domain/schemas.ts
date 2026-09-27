// 合成夹具：zod schema 只被文本解析，不会被 import
import { z } from "zod";
export const DiagnoseResultSchema = z.object({
  student_label: z.string(), // 分层标签
  rank_range: z.object({ low: z.number(), high: z.number() }),
  attempt_ids: z.array(z.string()),
  data_confidence: z.number(),
});
