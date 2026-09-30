// 契约 Problem 的错误类型（原在 adapter.ts，冲刺 4-D 拆出，供各方法模块共用）。
export type ProblemCode =
  | "INVALID_REQUEST"
  | "MODE_NOT_SUPPORTED" // v1.0 起（评审 N9）；0.9.0 请求仍报 INVALID_REQUEST
  | "CONTRACT_VERSION_UNSUPPORTED"
  | "SUBJECT_MISMATCH"
  | "METHOD_NOT_IMPLEMENTED"
  | "CONTENT_VERSION_NOT_FOUND"
  | "ITEM_NOT_FOUND"
  | "IDEMPOTENCY_CONFLICT"
  | "JOB_NOT_FOUND"
  | "INTERNAL";

export class ProblemError extends Error {
  status: number;
  code: ProblemCode;
  detail: string | undefined;
  constructor(status: number, code: ProblemCode, title: string, detail?: string) {
    super(title);
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}
