// 输入：YAML 子集解析、章节六段映射表（仓库里的真实配置）、KGE-01 地图版本种子解析（仓库里的真实种子）。不读 legacy。
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { MapInputError, parseSeedSql, parseTuple } from "../src/map-version.ts";
import { firstOccurrenceKey, parseSegmentConfig, SegmentConfigError } from "../src/segments.ts";
import { parseYaml, YamlError } from "../src/yaml.ts";
import { SEG_YAML } from "./fixtures.ts";

const REPO = new URL("../../../", import.meta.url);

test("YAML 子集：块状映射、注释、引号、整数；不支持的写法报错", () => {
  assert.deepEqual(parseYaml("a: 1\nb:\n  c: \"x # y\"  # 注释\n  d: 'it''s'\n  e: suke-2024\n"), { a: 1, b: { c: "x # y", d: "it's", e: "suke-2024" } });
  assert.throws(() => parseYaml("a: [1, 2]"), YamlError);
  assert.throws(() => parseYaml("a:\n   b: 1"), YamlError);
  assert.throws(() => parseYaml("a: 1\na: 2"), YamlError);
  assert.throws(() => parseYaml("a:\tb"), YamlError);
  assert.throws(() => parseYaml("- a"), YamlError);
});

test("六段映射表：值域与 platform.curriculum_version 一致，写错就报", () => {
  const c = parseSegmentConfig(SEG_YAML);
  assert.equal(c.chapters.get("fun")!.segments.grade, 9);
  assert.throws(() => parseSegmentConfig(SEG_YAML.replace("grade: 9", "grade: 10")), SegmentConfigError);
  assert.throws(() => parseSegmentConfig(SEG_YAML.replace("term: lower", "term: 下")), SegmentConfigError);
  assert.throws(() => parseSegmentConfig(SEG_YAML.replace("exam_year: 2027", "exam_year: 2020")), SegmentConfigError);
  assert.throws(() => parseSegmentConfig(SEG_YAML.replace("textbook_version: suke-2024\n  pyt", "textbook_version: 苏科\n  pyt")), SegmentConfigError);
  assert.throws(() => parseSegmentConfig(`${SEG_YAML}    extra: 1\n`), SegmentConfigError);
  assert.ok(firstOccurrenceKey(c.chapters.get("pyt")!) < firstOccurrenceKey(c.chapters.get("fun")!));
});

test("仓库里的映射表：32 个章节，七至九年级，都标了“待教研核对”", () => {
  const text = readFileSync(new URL("config/aya-chapter-segments.yaml", new URL("../", import.meta.url)), "utf8");
  assert.ok(text.includes("待教研核对"));
  const c = parseSegmentConfig(text);
  assert.equal(c.chapters.size, 32);
  assert.deepEqual([...new Set([...c.chapters.values()].map((x) => x.segments.grade))].sort(), [7, 8, 9]);
  assert.equal(c.chapters.get("fun")!.label, "苏科版九下 · 第5章");
});

test("种子解析：元组里的字符串、NULL、数组与类型转换", () => {
  assert.deepEqual(parseTuple("  ('a''b', NULL, 2022, '{}'::text[], ARRAY['x', 'y''z']::text[], 'k'::platform.cross_subject_error),"),
    ["a'b", null, 2022, [], ["x", "y'z"], "k"]);
  assert.throws(() => parseTuple("  ('abc"), MapInputError);
});

test("KGE-01 的二次函数种子：地图版本、24 个知识节点、迁移表都指向存在的节点", () => {
  const m = parseSeedSql(readFileSync(new URL("db/seeds/kge/aya-fun-0.1.0/import.sql", REPO), "utf8"));
  const report = JSON.parse(readFileSync(new URL("db/seeds/kge/aya-fun-0.1.0/import-report.json", REPO), "utf8")) as Record<string, unknown>;
  assert.equal(m.mapVersionId, report.map_version_id);
  assert.equal(m.version, "0.1.0");
  assert.equal(m.nodes.filter((n) => n.level === "node").length, report.imported_nodes);
  assert.equal(m.idMigration.size, report.imported_nodes);
  assert.equal(m.idMigration.get("K-FUN-0901"), "MATH.FUNC.FUN_CON.K_FUN_0901");
  assert.throws(() => parseSeedSql("BEGIN;\nCOMMIT;\n"), MapInputError);
});
