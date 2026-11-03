// 合成夹具：使用 schema 且有模型调用
import { callTextJSON } from "@shared/lib/llm";
import { DiagnoseResultSchema } from "@shared/lib/domain/schemas";
export const run = () => callTextJSON(DiagnoseResultSchema);
