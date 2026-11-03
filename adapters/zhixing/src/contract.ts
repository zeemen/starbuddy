// 按契约 packages/contracts/openapi/plugin-api.v*.yaml 校验请求与响应（唯一硬接口，CLAUDE.md 规则 1）。
// 直接加载契约原文，不手抄结构：契约一改，这里的校验随之改变。
// 来源：adapters/aya/src/contract.ts（同一套版本开关，spec 设计要点 1；待人确认 #6 默认两版都支持），只加了本科用到的 schema 名。
//
// 迁移期两版并存（specs/plugin-api-v1.0-step2/spec.md 第 3 条）：0.9.0 与 1.0.0（2026-10-09 签字）同时加载，
// 按请求的 context.contract_version 选版本。v1.0 用相对路径 $ref 引用两份服务契约（它们又引用 v0.9），
// 所以 openapi 目录下相关的四份契约都按“文件 URL”作 $id 登记到同一个 ajv 实例，跨文件引用按 URI 解析。
import { readFileSync } from "node:fs";
import { Ajv2020, type ValidateFunction } from "ajv/dist/2020.js";
import addFormatsModule from "ajv-formats";
import { parse } from "yaml";

// 1.1.0 候选版（PLT-12，specs/present-item-contract）：以 1.0.0 为底，只多一个只读方法 present_item
export const SUPPORTED_CONTRACT_VERSIONS = ["0.9.0", "1.0.0", "1.1.0"] as const;
export type ContractVersion = (typeof SUPPORTED_CONTRACT_VERSIONS)[number];
/** 能力声明默认报的版本：v1.0 已于 2026-10-09 签字，改报 1.0.0（specs/contract-v1.0-signoff）；0.9.0 迁移期仍可显式指定 */
export const DEFAULT_CONTRACT_VERSION: ContractVersion = "1.0.0";
/** 候选版：签字前只在测试与演练环境使用（能力声明默认版本要改成它时需显式打开开关）。1.0.0 已签字，不再是候选版 */
export const CANDIDATE_CONTRACT_VERSIONS: readonly ContractVersion[] = ["1.1.0"];
/** 兼容旧引用：等于默认版本 */
export const CONTRACT_VERSION = DEFAULT_CONTRACT_VERSION;

export const OPENAPI_DIR = new URL("../../../packages/contracts/openapi/", import.meta.url);
export const PLUGIN_CONTRACT_FILES: Record<ContractVersion, string> = {
  "0.9.0": "plugin-api.v0.9.yaml",
  "1.0.0": "plugin-api.v1.0.yaml",
  "1.1.0": "plugin-api.v1.1.yaml",
};
/** 插件契约跨文件引用到的服务契约（v1.0 → 两份服务契约 → v0.9） */
const RELATED_CONTRACT_FILES = ["knowledge-service.v0.9.yaml", "insight-service.v0.9.yaml"];

export const contractPath = (version: ContractVersion, dir: URL = OPENAPI_DIR) => new URL(PLUGIN_CONTRACT_FILES[version], dir);
export const DEFAULT_CONTRACT_PATH = contractPath(DEFAULT_CONTRACT_VERSION);

export type SchemaName =
  | "Capabilities"
  | "GradeRequest"
  | "GradeResponse"
  | "DiagnoseRequest"
  | "DiagnoseResponse"
  | "GenerateVariationRequest"
  | "GenerateVariationResponse"
  | "PlanRequest"
  | "PlanResponse"
  | "ReportRequest"
  | "ReportResponse"
  | "PresentItemRequest"
  | "PresentItemResponse"
  | "Problem";

export type ValidationResult = { ok: true } | { ok: false; errors: string[] };

export interface ContractValidator {
  /** 默认版本：能力声明报这个；validate 不指定版本时也用它 */
  version: ContractVersion;
  versions: readonly ContractVersion[];
  supports(version: unknown): version is ContractVersion;
  validate(schema: SchemaName, value: unknown, version?: ContractVersion): ValidationResult;
  /** 该版本 components.schemas 的全部名字（用于“全部 schema 可编译”的检查） */
  schemaNames(version: ContractVersion): string[];
  /** 编译该版本的全部 components.schemas，返回编译失败的 schema 与原因（空数组 = 全部可解析） */
  compileAll(version: ContractVersion): { schema: string; error: string }[];
}

export interface LoadContractOptions {
  /** 契约目录（默认 packages/contracts/openapi/） */
  dir?: URL;
  defaultVersion?: ContractVersion;
}

// ajv-formats 是 CJS 包，默认导出在 NodeNext 下带一层 default
const addFormats = addFormatsModule as unknown as typeof addFormatsModule.default;

interface OpenApiDoc {
  info: { version: string };
  components: { schemas: Record<string, unknown> } & Record<string, unknown>;
}

export const isSupportedVersion = (v: unknown): v is ContractVersion =>
  typeof v === "string" && (SUPPORTED_CONTRACT_VERSIONS as readonly string[]).includes(v);

export function loadContract(opts: LoadContractOptions = {}): ContractValidator {
  const dir = opts.dir ?? OPENAPI_DIR;
  const defaultVersion = opts.defaultVersion ?? DEFAULT_CONTRACT_VERSION;
  if (!isSupportedVersion(defaultVersion)) throw new Error(`不支持的契约版本 ${String(defaultVersion)}`);
  // OpenAPI 3.1 的 schema 即 JSON Schema 2020-12。strict 关闭：契约里有 examples、description 之类的注解关键字。
  const ajv = new Ajv2020({ allErrors: true, strict: false });
  addFormats(ajv);

  const docs = new Map<ContractVersion, OpenApiDoc>();
  const register = (file: string): OpenApiDoc => {
    const url = new URL(file, dir);
    const doc = parse(readFileSync(url, "utf8")) as OpenApiDoc;
    // 只登记 components：$id 取文件 URL，./xxx.yaml#/components/... 的相对引用据此解析
    ajv.addSchema({ $id: url.href, components: doc.components });
    return doc;
  };
  for (const v of SUPPORTED_CONTRACT_VERSIONS) {
    const doc = register(PLUGIN_CONTRACT_FILES[v]);
    if (doc.info.version !== v) throw new Error(`契约 ${PLUGIN_CONTRACT_FILES[v]} 的版本 ${doc.info.version} 与适配器期望的 ${v} 不一致`);
    docs.set(v, doc);
  }
  for (const f of RELATED_CONTRACT_FILES) register(f);

  const cache = new Map<string, ValidateFunction>();
  const get = (version: ContractVersion, name: string) => {
    const key = `${version}\0${name}`;
    let fn = cache.get(key);
    if (!fn) {
      fn = ajv.compile({ $ref: `${contractPath(version, dir).href}#/components/schemas/${name}` });
      cache.set(key, fn);
    }
    return fn;
  };
  return {
    version: defaultVersion,
    versions: SUPPORTED_CONTRACT_VERSIONS,
    supports: isSupportedVersion,
    validate(name, value, version = defaultVersion) {
      const fn = get(version, name);
      if (fn(value)) return { ok: true };
      // 只报位置与关键字，不带实例数据，避免把学生原文写进错误详情（契约 Problem.detail）
      return { ok: false, errors: (fn.errors ?? []).map((e) => `${e.instancePath || "/"} ${e.keyword}`) };
    },
    schemaNames(version) {
      return Object.keys(docs.get(version)!.components.schemas);
    },
    compileAll(version) {
      const failed: { schema: string; error: string }[] = [];
      for (const name of Object.keys(docs.get(version)!.components.schemas)) {
        try {
          get(version, name);
        } catch (e) {
          failed.push({ schema: name, error: e instanceof Error ? e.message : String(e) });
        }
      }
      return failed;
    },
  };
}
