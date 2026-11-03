// 安全扫描（写法同 adapters/zhixing/tests/safety.test.ts）：
// 1. 禁语文案不出现：判分 Prompt、适配器源码与内容包里没有 legacy 人设语气（阅卷教师身份、“鼓励为主”、妙笔口吻）、
//    位次与提分（盘点 adapter-design：诊断与处方里去掉“全市位次区间”“预计 +X 分”）、模型出题、自由文本字段，
//    以及考官型 G 类禁语（docs/personas/考官型基线与角色卡 5.2）与 E 类比较用语。
// 2. 不持有供应商信息：适配器的源码、脚本、内容包、配置、README、一致性夹具里没有任何模型供应商域名或 Key 形态的字符串
//    （ADR 04 第 1 条）。本文件自己列了这些词，扫描时排除本文件。
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import { ANTI_FORMULA_SIGNALS, buildEssayGradeMessages, GRADING_RULES, OUTPUT_FORMAT } from "../src/core/essay-prompt.ts";
import { realPack } from "./helpers.ts";

const ROOT = fileURLToPath(new URL("..", import.meta.url));
const SELF = fileURLToPath(import.meta.url);

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    if (name === "node_modules" || name === "package-lock.json") return [];
    return statSync(p).isDirectory() ? files(p) : [p];
  });
}

/** 适配器发布出去的部分：测试目录（桩与测试数据）不算，但也一起扫供应商信息 */
const SHIPPED = files(ROOT).filter((f) => !relative(ROOT, f).startsWith("tests"));

const BANNED_PHRASES = [
  // legacy 人设与身份设定（build-grade.ts、妙笔）
  "PERSONA_TONE", "persona", "妙笔", "阅卷教师", "鼓励为主", "学长", "学姐", "我当年", "搭子",
  // 自由文本与自报字段（legacy 批改 JSON）
  "overall_comment", "uncertainty_note", "needs_human_review", "polish_suggestion", "upgrade", "升格", "est_score", "score_range", "band",
  // 位次、提分、预测（盘点 adapter-design 第 1 节）
  "位次", "排名", "提分", "预计提", "预计 +", "全市", "forecast",
  // 模型出题（KD-02 不移植）
  "现场出题", "生成题目", "quiz/generate",
  // G 类：情面、鼓励、归因、标准之外的判断
  "差一点", "可惜", "很接近了", "不错", "继续加油", "你审题不清", "我觉得", "按理说", "一般来说", "太慢了",
  // E 类：比较与总括评价
  "别人这时候都", "大部分同学", "你就是粗心",
];

test("判分 Prompt 不含人设语气与禁语；只有判分规则", () => {
  const prompt = [
    GRADING_RULES,
    ANTI_FORMULA_SIGNALS,
    OUTPUT_FORMAT,
    ...realPack.pack.essays.flatMap((e) => buildEssayGradeMessages(e, realPack.pack.rubric, realPack.modelDimensions, "合成作文").map((m) => m.content)),
  ].join("\n");
  for (const p of BANNED_PHRASES) assert.ok(!prompt.toLowerCase().includes(p.toLowerCase()), `Prompt 出现“${p}”`);
  // 不让模型写任何自由文本：输出格式里只有编码与数字字段
  assert.ok(!/comment|suggestion|note|reference|review|reason|score/i.test(OUTPUT_FORMAT));
  // 不让模型估分：Prompt 里没有分值（分值只在内容包量表里，由适配器换算）
  for (const d of realPack.pack.rubric.dimensions) for (const l of d.levels) assert.ok(!prompt.includes(`${l.score} 分`));
});

test("适配器源码、内容包、脚本、夹具里没有禁语文案", () => {
  for (const f of SHIPPED) {
    if (!/\.(ts|json|yaml|md)$/.test(f) || f.endsWith("README.md")) continue;
    const text = readFileSync(f, "utf8");
    // 注释里的出处说明会提到被删掉的东西（例如“去掉 band”），只扫字符串字面量与 JSON/YAML 值
    const strings = f.endsWith(".ts") ? [...text.matchAll(/(["'`])((?:\\.|(?!\1)[^\\])*)\1/g)].map((m) => m[2]!) : [text.replace(/^\s*#.*$/gm, "")];
    for (const s of strings) for (const p of BANNED_PHRASES) assert.ok(!s.includes(p), `${relative(ROOT, f)} 出现“${p}”`);
  }
});

const VENDOR_PATTERNS: [string, RegExp][] = [
  ["供应商域名", /(dashscope|aliyuncs|openai\.com|api\.openai|anthropic\.com|deepseek\.com|moonshot\.cn|bigmodel\.cn|zhipuai|volces\.com|volcengine|baidubce|qianfan|hunyuan|tencentcloudapi|siliconflow|minimax|googleapis|azure\.com|x\.ai|mistral\.ai|groq\.com)/i],
  ["Key 形态", /\b(sk-[A-Za-z0-9]{16,}|sk-ant-[A-Za-z0-9\-]{10,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z\-_]{30,})/],
  ["Key 变量名", /\b[A-Z_]*(OPENAI|DASHSCOPE|DEEPSEEK|ANTHROPIC|QWEN|ZHIPU|MOONSHOT|ARK)_[A-Z_]*(KEY|TOKEN|SECRET|BASE_URL)\b/],
  ["BaseURL 配置", /\b(BASE_URL|base_url|baseURL)\b/],
];

test("适配器的源码与配置里没有任何供应商域名、Key 或 BaseURL（模型调用只经学习服务内部路由接口）", () => {
  const scanned = files(ROOT).filter((f) => f !== SELF);
  assert.ok(scanned.length > 10);
  for (const f of scanned) {
    const text = readFileSync(f, "utf8");
    for (const [label, re] of VENDOR_PATTERNS) assert.ok(!re.test(text), `${relative(ROOT, f)} 含${label}`);
  }
  // 唯一的出站调用地址来自 WENLI_ROUTE_URL，路径固定为学习服务内部接口
  const client = readFileSync(join(ROOT, "src/route-client.ts"), "utf8");
  assert.match(client, /ROUTE_PATH = "\/internal\/v1\/model\/route"/);
  const fetchCalls = SHIPPED.filter((f) => f.endsWith(".ts")).filter((f) => /\bfetch(Impl)?\(/.test(readFileSync(f, "utf8")));
  assert.deepEqual(fetchCalls.map((f) => relative(ROOT, f)), ["src/route-client.ts"]);
  // diagnose、plan 不调模型：只有 grade 的判定函数调用路由
  const adapter = readFileSync(join(ROOT, "src/adapter.ts"), "utf8");
  assert.equal((adapter.match(/callRoute\(/g) ?? []).length, 1);
});

test("依赖只有 ajv、ajv-formats、yaml，版本与阿鸦、知行完全一致", () => {
  const pkg = JSON.parse(readFileSync(join(ROOT, "package.json"), "utf8"));
  for (const other of ["aya", "zhixing"]) {
    const o = JSON.parse(readFileSync(join(ROOT, `../${other}/package.json`), "utf8"));
    assert.deepEqual(pkg.dependencies, o.dependencies, other);
    assert.deepEqual(pkg.devDependencies, o.devDependencies, other);
  }
  const lock = JSON.parse(readFileSync(join(ROOT, "package-lock.json"), "utf8"));
  const zlock = JSON.parse(readFileSync(join(ROOT, "../zhixing/package-lock.json"), "utf8"));
  const deps = (l: { packages: Record<string, { version?: string }> }) =>
    Object.fromEntries(Object.entries(l.packages).filter(([k]) => k !== "").map(([k, v]) => [k, v.version]));
  assert.deepEqual(deps(lock), deps(zlock));
});
