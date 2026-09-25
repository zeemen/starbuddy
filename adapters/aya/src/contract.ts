// 按契约 packages/contracts/openapi/plugin-api.v0.9.yaml 校验请求与响应（唯一硬接口，CLAUDE.md 规则 1）。
// 直接加载契约原文，不手抄结构：契约一改，这里的校验随之改变。
import { readFileSync } from "node:fs";
import { Ajv2020, type ValidateFunction } from "ajv/dist/2020.js";
import addFormatsModule from "ajv-formats";
import { parse } from "yaml";

export const CONTRACT_VERSION = "0.9.0";
export const DEFAULT_CONTRACT_PATH = new URL("../../../packages/contracts/openapi/plugin-api.v0.9.yaml", import.meta.url);

export type SchemaName =
  | "Capabilities"
  | "GradeRequest"
  | "GradeResponse"
  | "DiagnoseRequest"
  | "DiagnoseResponse"
  | "Problem";

export interface ContractValidator {
  version: string;
  validate(schema: SchemaName, value: unknown): { ok: true } | { ok: false; errors: string[] };
}

// ajv-formats 是 CJS 包，默认导出在 NodeNext 下带一层 default
const addFormats = addFormatsModule as unknown as typeof addFormatsModule.default;

export function loadContract(path: string | URL = DEFAULT_CONTRACT_PATH): ContractValidator {
  const doc = parse(readFileSync(path, "utf8")) as { info: { version: string }; components: unknown };
  if (doc.info.version !== CONTRACT_VERSION) throw new Error(`契约版本 ${doc.info.version} 与适配器实现的 ${CONTRACT_VERSION} 不一致`);
  // OpenAPI 3.1 的 schema 即 JSON Schema 2020-12。strict 关闭：契约里有 examples、description 之类的注解关键字。
  const ajv = new Ajv2020({ allErrors: true, strict: false });
  addFormats(ajv);
  ajv.addSchema({ $id: "contract", components: doc.components });
  const cache = new Map<SchemaName, ValidateFunction>();
  const get = (name: SchemaName) => {
    let fn = cache.get(name);
    if (!fn) {
      fn = ajv.compile({ $ref: `contract#/components/schemas/${name}` });
      cache.set(name, fn);
    }
    return fn;
  };
  return {
    version: doc.info.version,
    validate(name, value) {
      const fn = get(name);
      if (fn(value)) return { ok: true };
      // 只报位置与关键字，不带实例数据，避免把学生原文写进错误详情（契约 Problem.detail）
      return { ok: false, errors: (fn.errors ?? []).map((e) => `${e.instancePath || "/"} ${e.keyword}`) };
    },
  };
}
