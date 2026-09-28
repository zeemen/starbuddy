-- 0011 kge · 知识地图骨架与原子层（specs/kge-schema-v0.9/spec.md）
-- 状态：草稿 v0.9。只写文件，由人执行（CLAUDE.md 必停项：迁移、RLS 与权限、新数据库角色）。
-- 依据：KGE V1.1 第三章（层级、节点类型、认知层次、范围）、第五章（版本与变更）、第七章（ID 命名）、第八章（kge schema 草案）。
-- 按对照文档 #3：已有表留在 platform.*，kge.* 只放新增对象。本 schema 是公共知识层：不含任何学生数据，也没有学生键（规则 13）。
-- 与 platform 内容登记的边界（spec 设计要点 1）：题目的身份与准入仍只在 platform.item，能力视图仍只在 platform.skill_node；
-- kge.item_tag 只存打标与元数据，题干、答案、解析不进库（spec 待人确认 #3，按默认）。
-- 本迁移只改 kge schema（ADR 02 第 2 条）；platform.curriculum_version 的地图版本引用在 0012。
-- 请以单事务执行（psql -1）。只用标准 Postgres 16 能力。

-- 知识服务角色由人在 db/bootstrap/roles.sql 创建；这里只断言它存在且属性安全，并且不与其他应用角色共存于同一登录角色
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'starbuddy_kge') THEN
    RAISE EXCEPTION '角色 starbuddy_kge 不存在：请先执行 db/bootstrap/roles.sql';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'starbuddy_kge'
               AND (rolsuper OR rolbypassrls OR rolcanlogin OR rolcreaterole OR rolcreatedb OR rolreplication)) THEN
    RAISE EXCEPTION 'starbuddy_kge 必须 NOLOGIN，且不能是 SUPERUSER、BYPASSRLS、CREATEROLE、CREATEDB、REPLICATION';
  END IF;
  -- 发布的信任判断（guard_map_version 里的 current_user = 'starbuddy_authz'）依赖 authz 没有任何成员：迁移时复核 0001 的断言
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'starbuddy_authz' AND (rolcanlogin OR rolsuper))
     OR EXISTS (SELECT 1 FROM pg_roles r
                 WHERE NOT r.rolsuper AND r.rolname <> 'starbuddy_authz' AND pg_has_role(r.oid, 'starbuddy_authz', 'MEMBER')) THEN
    RAISE EXCEPTION 'starbuddy_authz 必须 NOLOGIN、非 SUPERUSER，且不能直接或间接授予任何角色';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles r
              WHERE NOT r.rolsuper AND pg_has_role(r.oid, 'starbuddy_kge', 'MEMBER')
                AND (pg_has_role(r.oid, 'starbuddy_bff', 'MEMBER') OR pg_has_role(r.oid, 'starbuddy_learning', 'MEMBER')
                     OR pg_has_role(r.oid, 'starbuddy_review', 'MEMBER') OR pg_has_role(r.oid, 'starbuddy_scheduler', 'MEMBER')
                     OR pg_has_role(r.oid, 'starbuddy_insight', 'MEMBER'))) THEN
    RAISE EXCEPTION '同一个角色不能同时继承 starbuddy_kge 与 bff / learning / review / scheduler / insight';
  END IF;
  IF pg_encoding_to_char((SELECT encoding FROM pg_database WHERE datname = current_database())) <> 'UTF8' THEN
    RAISE EXCEPTION '数据库编码必须是 UTF8（教材原句哈希按 UTF-8 字节计算）';
  END IF;
END $$;

CREATE SCHEMA kge;
COMMENT ON SCHEMA kge IS '知识与成长引擎（KGE）公共知识层：地图版本、骨架、投影、原子与成长治理。唯一写入方是知识服务（starbuddy_kge）；不含学生数据';
REVOKE ALL ON SCHEMA kge FROM PUBLIC;
GRANT USAGE ON SCHEMA kge TO starbuddy_kge, starbuddy_learning, starbuddy_bff, starbuddy_insight, starbuddy_authz;
-- 知识服务要用 platform 的公共类型（subject、cross_subject_error、confidence、skill_local_id、versioned_name）；不授予任何 platform 表权限
GRANT USAGE ON SCHEMA platform TO starbuddy_kge;

-- ---------- 节点 ID（docs/design/kge/id-and-segments.md；正则与该文档由 docs/design/kge/check_node_id.py 比对） ----------
-- 学科.领域[.主题[.节点[.细目]]]：全大写，首段是学科前缀，其余段为字母、数字、下划线。段数决定层级（领域 2、主题 3、节点 4、细目 5）
CREATE DOMAIN kge.node_id AS text
  CHECK (VALUE ~ '^[A-Z][A-Z0-9]{1,9}(\.[A-Z0-9][A-Z0-9_]{0,31}){1,4}$');

-- 学科前缀与 platform.subject 的对应（ENG 搁置；HIST、PHY、CHEM 预留，待 platform.subject 增加取值时补）
CREATE FUNCTION kge.subject_prefix(p_subject platform.subject) RETURNS text
  LANGUAGE sql IMMUTABLE PARALLEL SAFE
  AS $$ SELECT CASE p_subject WHEN 'aya' THEN 'MATH' WHEN 'wenli' THEN 'CHN' WHEN 'zhixing' THEN 'MORAL' WHEN 'echo' THEN 'ENG' END $$;
-- ID 的段数与上一级 ID（领域没有上一级）
CREATE FUNCTION kge.id_depth(p_id text) RETURNS integer
  LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$ SELECT cardinality(string_to_array(p_id, '.')) $$;
CREATE FUNCTION kge.id_parent(p_id text) RETURNS text
  LANGUAGE sql IMMUTABLE PARALLEL SAFE
  AS $$ SELECT CASE WHEN kge.id_depth(p_id) <= 2 THEN NULL
                    ELSE array_to_string((string_to_array(p_id, '.'))[1:kge.id_depth(p_id) - 1], '.') END $$;
-- 原句哈希：UTF-8 字节的 sha256。convert_to 只标为 STABLE（依赖编码），库编码固定为 UTF8（本迁移开头断言），所以这里可声明 IMMUTABLE 供生成列使用
CREATE FUNCTION kge.quote_sha256(p text) RETURNS bytea
  LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$ SELECT sha256(convert_to(p, 'UTF8')) $$;
-- 公共层的 jsonb 不能带个人层的键（规则 13）：任何层级的键名含 student、attempt、evidence_event、account、guardian、household、learner 都拒绝
CREATE FUNCTION kge.has_personal_key(p jsonb) RETURNS boolean
  LANGUAGE sql IMMUTABLE PARALLEL SAFE
  AS $$ SELECT p::text ~* '"[^"]*(student|attempt|evidence_event|account|guardian|household|learner)[^"]*"\s*:' $$;
-- 数组去重后的个数（变更单审核人不能重复）
CREATE FUNCTION kge.distinct_count(p text[]) RETURNS integer
  LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$ SELECT count(DISTINCT x)::integer FROM unnest(p) x $$;
REVOKE EXECUTE ON FUNCTION kge.subject_prefix(platform.subject), kge.id_depth(text), kge.id_parent(text), kge.distinct_count(text[]),
  kge.quote_sha256(text), kge.has_personal_key(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kge.subject_prefix(platform.subject), kge.id_depth(text), kge.id_parent(text), kge.distinct_count(text[]),
  kge.quote_sha256(text), kge.has_personal_key(jsonb)
  TO starbuddy_kge, starbuddy_learning, starbuddy_authz;

-- ---------- 版本与课标 ----------
CREATE TABLE kge.map_version (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  subject         platform.subject NOT NULL,
  standard_year   smallint NOT NULL CHECK (standard_year BETWEEN 2011 AND 2100),  -- 课标年份
  version         text NOT NULL CHECK (version ~ '^\d{1,3}\.\d{1,3}\.\d{1,4}$'),     -- 主.次.修订
  status          text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published', 'retired')),
  published_at    timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (subject, version),
  UNIQUE (subject, id),  -- platform.curriculum_version 按 (subject, map_version_id) 引用，保证学科一致
  CONSTRAINT map_version_published_at CHECK ((status <> 'published' OR published_at IS NOT NULL)
                                             AND (status <> 'draft' OR published_at IS NULL))
);
-- 同一学科同一时刻只能有一个已发布版本
CREATE UNIQUE INDEX map_version_one_published ON kge.map_version (subject) WHERE status = 'published';
COMMENT ON TABLE kge.map_version IS
  '地图版本（KGE 第八章的 curriculum_version，改名以免与 platform.curriculum_version 的内容版本混淆）。发布只能经 kge.publish_map_version；非草稿版本的骨架不能再改';

CREATE TABLE kge.curriculum_item (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  subject         platform.subject NOT NULL,
  standard_year   smallint NOT NULL CHECK (standard_year BETWEEN 2011 AND 2100),
  code            text NOT NULL CHECK (code ~ '^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$'),
  domain          text NOT NULL CHECK (char_length(domain) BETWEEN 1 AND 64),
  text            text NOT NULL CHECK (char_length(text) BETWEEN 1 AND 1000),
  verb_level      text NOT NULL CHECK (verb_level IN ('know', 'understand', 'apply', 'synthesize')),  -- 课标四级：知道、理解、运用、综合
  source_ref      text NOT NULL CHECK (char_length(source_ref) BETWEEN 1 AND 256),
  UNIQUE (subject, standard_year, code)
);
COMMENT ON TABLE kge.curriculum_item IS '课标条目原子（KGE 第八章）';

-- ---------- 骨架：节点 ----------
CREATE TABLE kge.knowledge_node (
  map_version_id    uuid NOT NULL REFERENCES kge.map_version (id),
  id                kge.node_id NOT NULL,
  level             text NOT NULL CHECK (level IN ('domain', 'topic', 'node', 'detail')),  -- 领域 / 主题 / 知识节点 / 细目
  parent_id         kge.node_id,
  kind              text CHECK (kind IN ('kp', 'ability_kp', 'viewpoint', 'material', 'method', 'pitfall', 'exam_point')),
  name              text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 128),
  includes          text[] NOT NULL DEFAULT '{}' CHECK (cardinality(includes) <= 32),
  excludes          text[] NOT NULL DEFAULT '{}' CHECK (cardinality(excludes) <= 32),
  examples          text[] NOT NULL DEFAULT '{}' CHECK (cardinality(examples) <= 32),
  scope             text NOT NULL DEFAULT 'required' CHECK (scope IN ('required', 'elective', 'edition_ext', 'exam_ext', 'enrichment')),
  cognitive_levels  text[] NOT NULL DEFAULT '{}' CHECK (cognitive_levels <@ ARRAY['know', 'understand', 'apply', 'synthesize']),
  error_type        platform.cross_subject_error,
  status            text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'deprecated')),
  PRIMARY KEY (map_version_id, id),
  FOREIGN KEY (map_version_id, parent_id) REFERENCES kge.knowledge_node (map_version_id, id),
  -- 层级由 ID 的段数决定，上一级就是去掉最后一段的 ID（领域没有上一级）
  CONSTRAINT node_level_depth CHECK (kge.id_depth(id) = CASE level WHEN 'domain' THEN 2 WHEN 'topic' THEN 3 WHEN 'node' THEN 4 ELSE 5 END),
  CONSTRAINT node_parent_is_prefix CHECK (parent_id IS NOT DISTINCT FROM kge.id_parent(id)),
  -- 领域与主题是结构层，不带类型；知识节点与细目必须有类型
  CONSTRAINT node_kind_by_level CHECK ((level IN ('domain', 'topic')) = (kind IS NULL)),
  -- 细目只作证据标签，由易错点或考点承担（spec 待人确认 #7，按默认）
  CONSTRAINT node_detail_kind CHECK (level <> 'detail' OR kind IN ('pitfall', 'exam_point')),
  -- 易错点必须写错因大类，其他类型不写
  CONSTRAINT node_pitfall_error_type CHECK ((kind IS NOT DISTINCT FROM 'pitfall') = (error_type IS NOT NULL)),
  -- 只有参与掌握度计算的类型能带认知层次（KGE 第三章表）
  CONSTRAINT node_cognitive_levels_kind CHECK (cardinality(cognitive_levels) = 0 OR kind IN ('kp', 'ability_kp', 'viewpoint'))
);
COMMENT ON TABLE kge.knowledge_node IS '知识节点（星 = knowledge_node）。ID 跨版本稳定，主键带地图版本；撤下走 status = deprecated，不删除';

CREATE TABLE kge.knowledge_edge (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  map_version_id      uuid NOT NULL,
  from_id             kge.node_id NOT NULL,
  to_map_version_id   uuid NOT NULL,
  to_id               kge.node_id NOT NULL,
  type                text NOT NULL CHECK (type IN ('prerequisite', 'part_of', 'related', 'confusable', 'uses_method', 'has_pitfall', 'aligned_to')),
  strength            text NOT NULL DEFAULT 'strong' CHECK (strength IN ('strong', 'weak')),
  evidence_ref        text CHECK (char_length(evidence_ref) BETWEEN 1 AND 256),
  status              text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'deprecated')),
  FOREIGN KEY (map_version_id, from_id) REFERENCES kge.knowledge_node (map_version_id, id),
  FOREIGN KEY (to_map_version_id, to_id) REFERENCES kge.knowledge_node (map_version_id, id),
  UNIQUE (map_version_id, from_id, to_map_version_id, to_id, type),
  CONSTRAINT edge_no_self_loop CHECK (NOT (map_version_id = to_map_version_id AND from_id = to_id)),
  -- 前置边与从属边两端必须在同一地图版本（一个版本只属于一个学科，所以也同学科）；关联、对齐等边可以跨学科
  CONSTRAINT edge_same_version CHECK (type NOT IN ('prerequisite', 'part_of') OR map_version_id = to_map_version_id)
);
CREATE INDEX knowledge_edge_to_idx ON kge.knowledge_edge (to_map_version_id, to_id);
COMMENT ON TABLE kge.knowledge_edge IS '节点之间的边。只连直接前置，传递关系由图计算（发布前校验报告传递边）';

-- ---------- 骨架：投影、能力视图挂接、旧编码迁移 ----------
CREATE TABLE kge.textbook_projection (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  map_version_id   uuid NOT NULL,
  node_id          kge.node_id NOT NULL,
  edition          text NOT NULL CHECK (edition ~ '^[a-z0-9][a-z0-9\-]{1,31}$'),  -- 与 platform.curriculum_version.textbook_version 同一套编码
  edition_year     smallint NOT NULL CHECK (edition_year BETWEEN 2000 AND 2100),
  printing         text CHECK (char_length(printing) BETWEEN 1 AND 32),
  volume           text NOT NULL CHECK (char_length(volume) BETWEEN 1 AND 32),
  chapter          text NOT NULL CHECK (char_length(chapter) BETWEEN 1 AND 64),
  page_from        smallint NOT NULL CHECK (page_from >= 1),
  page_to          smallint NOT NULL,
  quote_text       text CHECK (char_length(quote_text) BETWEEN 1 AND 500),
  -- 原句由库算哈希锁定（KGE V1.1 修订 3），写入方不能伪造
  quote_hash       bytea GENERATED ALWAYS AS (kge.quote_sha256(quote_text)) STORED,
  verified_by      text CHECK (char_length(verified_by) BETWEEN 1 AND 64),  -- 核对人工号，不是账号 ID
  verified_at      timestamptz,
  mapping_state    text NOT NULL DEFAULT 'unverified' CHECK (mapping_state IN ('unverified', 'verified', 'disputed')),
  FOREIGN KEY (map_version_id, node_id) REFERENCES kge.knowledge_node (map_version_id, id),
  CONSTRAINT projection_page_range CHECK (page_to >= page_from),
  CONSTRAINT projection_verified_by CHECK (mapping_state <> 'verified' OR (verified_by IS NOT NULL AND verified_at IS NOT NULL AND quote_text IS NOT NULL))
);
CREATE INDEX textbook_projection_node_idx ON kge.textbook_projection (map_version_id, node_id);

CREATE TABLE kge.region_projection (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  map_version_id   uuid NOT NULL,
  node_id          kge.node_id NOT NULL,
  region           text NOT NULL CHECK (region ~ '^[a-z][a-z0-9\-]{1,31}$'),
  exam_year        smallint NOT NULL CHECK (exam_year BETWEEN 2015 AND 2100),
  weight           numeric(4, 3) NOT NULL CHECK (weight BETWEEN 0 AND 1),
  typical_slots    text[] NOT NULL DEFAULT '{}' CHECK (cardinality(typical_slots) <= 16),
  style_tags       text[] NOT NULL DEFAULT '{}' CHECK (cardinality(style_tags) <= 16),
  source_ref       text NOT NULL CHECK (char_length(source_ref) BETWEEN 1 AND 256),
  FOREIGN KEY (map_version_id, node_id) REFERENCES kge.knowledge_node (map_version_id, id),
  UNIQUE (map_version_id, node_id, region, exam_year)
);

CREATE TABLE kge.skill_link (
  map_version_id   uuid NOT NULL,
  node_id          kge.node_id NOT NULL,
  subject          platform.subject NOT NULL,
  local_id         platform.skill_local_id NOT NULL,
  weight           numeric(4, 3) NOT NULL CHECK (weight > 0 AND weight <= 1),
  PRIMARY KEY (map_version_id, node_id, subject, local_id),
  FOREIGN KEY (map_version_id, node_id) REFERENCES kge.knowledge_node (map_version_id, id),
  FOREIGN KEY (subject, local_id) REFERENCES platform.skill_node (subject, local_id)
);
COMMENT ON TABLE kge.skill_link IS '知识节点挂到能力视图（platform.skill_node）。能力视图不单独点亮';

CREATE TABLE kge.map_change (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  subject          platform.subject NOT NULL,
  change_type      text NOT NULL CHECK (change_type IN ('node_add', 'node_split', 'node_merge', 'node_retire', 'node_revise',
                                                         'edge_add', 'edge_remove', 'projection_fix', 'other')),
  payload          jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object' AND pg_column_size(payload) <= 16384 AND NOT kge.has_personal_key(payload)),
  -- 证据包只放聚合统计与公共层对象的 ID（题目、节点）；不得放作答、证据或学生的 ID 等任何个人层指针（规则 13；最小人数门槛由知识服务保证）
  evidence_pack    jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(evidence_pack) = 'object' AND pg_column_size(evidence_pack) <= 16384
                                                      AND NOT kge.has_personal_key(evidence_pack)),
  confidence       platform.confidence,
  status           text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'approved', 'rejected', 'applied')),
  reviewers        text[] NOT NULL DEFAULT '{}' CHECK (cardinality(reviewers) <= 8),  -- 审核人工号
  target_version   uuid REFERENCES kge.map_version (id),
  created_at       timestamptz NOT NULL DEFAULT now(),
  -- 结构性变更需两人确认（KGE 第五章）：批准与应用时至少两个不同的审核人
  CONSTRAINT map_change_two_reviewers CHECK (status NOT IN ('approved', 'applied') OR kge.distinct_count(reviewers) >= 2)
);

CREATE TABLE kge.id_migration (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  map_version_id   uuid NOT NULL REFERENCES kge.map_version (id),  -- 迁入的地图版本
  subject          platform.subject NOT NULL,
  old_id           text NOT NULL CHECK (old_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,127}$'),  -- 旧编码，如 K-FUN-0901、m1、xr1
  new_id           kge.node_id,
  rule             text NOT NULL CHECK (rule IN ('rename', 'split', 'merge', 'retire', 'manual')),
  change_id        uuid REFERENCES kge.map_change (id),
  created_at       timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (map_version_id, new_id) REFERENCES kge.knowledge_node (map_version_id, id),
  UNIQUE NULLS NOT DISTINCT (map_version_id, old_id, new_id),
  -- 撤销的旧编码没有新 ID（对应掌握度标为待复测，KGE 第五章）；其余必须指向新节点
  CONSTRAINT id_migration_retire CHECK ((rule = 'retire') = (new_id IS NULL))
);
COMMENT ON TABLE kge.id_migration IS '旧编码 → 新节点 ID。拆分是一对多，合并是多对一';

-- ---------- 原子：题目标签、题目族、采分点、时政 ----------
CREATE TABLE kge.item_family (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  map_version_id   uuid NOT NULL,
  main_node_id     kge.node_id NOT NULL,
  code             text NOT NULL CHECK (code ~ '^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$'),
  FOREIGN KEY (map_version_id, main_node_id) REFERENCES kge.knowledge_node (map_version_id, id),
  UNIQUE (map_version_id, code),
  UNIQUE (map_version_id, id)  -- 题目标签按（版本, 题目族）引用，不能挂到别的地图版本的题目族
);

CREATE TABLE kge.item_tag (
  map_version_id     uuid NOT NULL REFERENCES kge.map_version (id),
  subject            platform.subject NOT NULL,
  item_id            platform.skill_local_id NOT NULL,
  main_node_id       kge.node_id NOT NULL,
  node_ids           kge.node_id[] NOT NULL DEFAULT '{}' CHECK (cardinality(node_ids) <= 16),
  cognitive_level    text CHECK (cognitive_level IN ('know', 'understand', 'apply', 'synthesize')),
  prior_difficulty   numeric(4, 3) CHECK (prior_difficulty BETWEEN 0 AND 1),
  family_id          uuid,
  source_kind        text NOT NULL CHECK (source_kind IN ('textbook', 'exam_paper', 'original', 'adapted', 'third_party')),
  license_state      text NOT NULL DEFAULT 'unverified' CHECK (license_state IN ('unverified', 'licensed', 'original', 'public', 'restricted')),
  quality_grade      text CHECK (quality_grade IN ('A', 'B', 'C')),
  -- 只是打标审核，不是题目准入：准入只在 platform.item.admission
  tag_review_status  text NOT NULL DEFAULT 'pending' CHECK (tag_review_status IN ('pending', 'approved', 'rejected')),
  PRIMARY KEY (map_version_id, subject, item_id),
  FOREIGN KEY (map_version_id, family_id) REFERENCES kge.item_family (map_version_id, id),
  -- 题目的身份与准入只在 platform.item（不做第二份）
  FOREIGN KEY (subject, item_id) REFERENCES platform.item (subject, item_id),
  FOREIGN KEY (map_version_id, main_node_id) REFERENCES kge.knowledge_node (map_version_id, id)
);
CREATE INDEX item_tag_main_node_idx ON kge.item_tag (map_version_id, main_node_id);
COMMENT ON TABLE kge.item_tag IS '题目打标与元数据，与 platform.item 一对一（按地图版本）。题干、答案、解析不进库；六段标识经 platform.curriculum_version 继承';

CREATE TABLE kge.scoring_point (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  map_version_id       uuid NOT NULL REFERENCES kge.map_version (id),
  subject              platform.subject NOT NULL,
  item_id              platform.skill_local_id NOT NULL,
  rubric_version       platform.versioned_name NOT NULL,  -- 判分结果据此绑定版本（KGE 第七章）
  -- 采分点编号：与 platform.grade_dimension.dimension_id 同一套写法，判分维度据此指回采分点
  point_id             text NOT NULL CHECK (point_id ~ '^[a-z0-9_\-]{1,48}$'),
  score                numeric(5, 2) NOT NULL CHECK (score > 0),
  node_ids             kge.node_id[] NOT NULL CHECK (cardinality(node_ids) BETWEEN 1 AND 8),
  subject_dimension    text CHECK (char_length(subject_dimension) BETWEEN 1 AND 64),
  keyword_family       jsonb NOT NULL DEFAULT '{}' CHECK (pg_column_size(keyword_family) <= 4096 AND NOT kge.has_personal_key(keyword_family)),
  required_logic       text CHECK (char_length(required_logic) BETWEEN 1 AND 1000),
  material_linkage     text CHECK (char_length(material_linkage) BETWEEN 1 AND 1000),
  partial_credit_rule  jsonb NOT NULL DEFAULT '{}' CHECK (pg_column_size(partial_credit_rule) <= 4096 AND NOT kge.has_personal_key(partial_credit_rule)),
  -- 常见错误只写去标识的错误形态，不写真实作答原文（规则 13）
  common_errors        jsonb NOT NULL DEFAULT '[]' CHECK (pg_column_size(common_errors) <= 4096 AND NOT kge.has_personal_key(common_errors)),
  FOREIGN KEY (subject, item_id) REFERENCES platform.item (subject, item_id),
  UNIQUE (subject, item_id, rubric_version, point_id)
);
COMMENT ON TABLE kge.scoring_point IS
  '采分点（Rubric 原子）。写入后不能修改：改内容就发新 rubric_version，已有判分才能按原版本复算（规则 3）；一个 rubric_version 只属于一个地图版本';

CREATE TABLE kge.current_affair (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  map_version_id   uuid NOT NULL REFERENCES kge.map_version (id),
  node_ids         kge.node_id[] NOT NULL CHECK (cardinality(node_ids) BETWEEN 1 AND 16),
  source           text NOT NULL CHECK (char_length(source) BETWEEN 1 AND 256),
  valid_from       date NOT NULL,
  valid_until      date NOT NULL,
  review_status    text NOT NULL DEFAULT 'pending' CHECK (review_status IN ('pending', 'published', 'retired')),
  CONSTRAINT current_affair_validity CHECK (valid_until > valid_from)
);
COMMENT ON TABLE kge.current_affair IS '道法时政素材：生效与失效日期必填；过期仍为 published 时发布校验报警告，由体检流程下架（KGE 第七章）';

CREATE TABLE kge.content_review (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  object_type   text NOT NULL CHECK (object_type IN ('map_version', 'knowledge_node', 'knowledge_edge', 'textbook_projection',
                                                     'region_projection', 'item_tag', 'scoring_point', 'current_affair', 'map_change')),
  object_id     text NOT NULL CHECK (char_length(object_id) BETWEEN 1 AND 160),
  risk_level    text NOT NULL CHECK (risk_level IN ('low', 'medium', 'high')),
  diff          jsonb NOT NULL DEFAULT '{}' CHECK (pg_column_size(diff) <= 16384 AND NOT kge.has_personal_key(diff)),
  decision      text NOT NULL DEFAULT 'pending' CHECK (decision IN ('pending', 'approved', 'rejected')),
  reviewer      text CHECK (char_length(reviewer) BETWEEN 1 AND 64),  -- 审核人工号
  decided_at    timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT content_review_decided CHECK ((decision = 'pending') = (reviewer IS NULL AND decided_at IS NULL))
);

-- ---------- 保护触发器（SECURITY INVOKER：按写入方身份读版本） ----------
-- 版本：新建只能是草稿；发布只能经 kge.publish_map_version（属主 authz）；撤下是终态；非草稿版本只能改状态
CREATE FUNCTION kge.guard_map_version() RETURNS trigger LANGUAGE plpgsql
  SET search_path = pg_catalog, kge, platform, pg_temp
  AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.status <> 'draft' OR NEW.published_at IS NOT NULL THEN
      RAISE EXCEPTION '地图版本只能以草稿新建' USING ERRCODE = 'check_violation';
    END IF;
    NEW.created_at := now();
    RETURN NEW;
  END IF;
  IF NEW.id <> OLD.id OR NEW.subject <> OLD.subject OR NEW.created_at <> OLD.created_at THEN
    RAISE EXCEPTION '地图版本的 ID、学科与创建时间不能修改' USING ERRCODE = 'check_violation';
  END IF;
  IF OLD.status <> 'draft' AND (NEW.version <> OLD.version OR NEW.standard_year <> OLD.standard_year) THEN
    RAISE EXCEPTION '非草稿地图版本只能改状态' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.status IS DISTINCT FROM OLD.status THEN
    IF NOT ((OLD.status = 'draft' AND NEW.status IN ('published', 'retired')) OR (OLD.status = 'published' AND NEW.status = 'retired')) THEN
      RAISE EXCEPTION '不允许的地图版本状态变化：% → %', OLD.status, NEW.status USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status = 'published' AND current_user <> 'starbuddy_authz' THEN
      RAISE EXCEPTION '地图版本只能经 kge.publish_map_version 发布' USING ERRCODE = 'insufficient_privilege';
    END IF;
  ELSIF NEW.published_at IS DISTINCT FROM OLD.published_at THEN
    RAISE EXCEPTION '发布时间只能在发布时写入' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER guard_map_version BEFORE INSERT OR UPDATE ON kge.map_version
  FOR EACH ROW EXECUTE FUNCTION kge.guard_map_version();

-- 骨架冻结：已发布或已撤下版本的节点、边、投影、能力视图挂接、旧编码迁移不能新增或修改（改地图走新版本）
CREATE FUNCTION kge.guard_frozen() RETURNS trigger LANGUAGE plpgsql
  SET search_path = pg_catalog, kge, platform, pg_temp
  AS $$
DECLARE
  v_ids uuid[] := ARRAY[NEW.map_version_id] || CASE WHEN TG_OP = 'UPDATE' THEN ARRAY[OLD.map_version_id] ELSE '{}'::uuid[] END;
  v_status text;
  v_seen integer := 0;
BEGIN
  -- 对版本行加共享锁：与 publish_map_version 的 FOR UPDATE 互斥，发布期间的写入会等发布结束后按最新状态重新判断
  -- （安全审查 B1：否则校验看不到未提交的写入，或写入读到发布前的草稿状态）
  FOR v_status IN SELECT v.status FROM kge.map_version v WHERE v.id = ANY (v_ids) ORDER BY v.id FOR SHARE LOOP
    v_seen := v_seen + 1;
    IF v_status <> 'draft' THEN
      RAISE EXCEPTION '地图版本已发布或已撤下，% 不能再改（改地图走新版本）', TG_TABLE_NAME USING ERRCODE = 'check_violation';
    END IF;
  END LOOP;
  -- 看不到版本行时拒绝，不放行
  IF v_seen = 0 THEN
    RAISE EXCEPTION '% 引用的地图版本不存在或不可见', TG_TABLE_NAME USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;

-- 节点 ID 的首段必须是本版本学科的前缀
CREATE FUNCTION kge.guard_node() RETURNS trigger LANGUAGE plpgsql
  SET search_path = pg_catalog, kge, platform, pg_temp
  AS $$
DECLARE v_prefix text;
BEGIN
  SELECT kge.subject_prefix(v.subject) INTO v_prefix FROM kge.map_version v WHERE v.id = NEW.map_version_id;
  IF split_part(NEW.id, '.', 1) IS DISTINCT FROM v_prefix THEN
    RAISE EXCEPTION '节点 ID % 的学科前缀与地图版本的学科（%）不符', NEW.id, v_prefix USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;

-- 带学科列的行：学科必须与地图版本一致
CREATE FUNCTION kge.guard_subject() RETURNS trigger LANGUAGE plpgsql
  SET search_path = pg_catalog, kge, platform, pg_temp
  AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM kge.map_version v WHERE v.id = NEW.map_version_id AND v.subject = NEW.subject) THEN
    RAISE EXCEPTION '% 的学科与地图版本不符', TG_TABLE_NAME USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;

-- 节点 ID 数组：每个都必须是本版本已有的节点（数组列没有外键）
CREATE FUNCTION kge.guard_node_refs() RETURNS trigger LANGUAGE plpgsql
  SET search_path = pg_catalog, kge, platform, pg_temp
  AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM unnest(NEW.node_ids) x
              WHERE NOT EXISTS (SELECT 1 FROM kge.knowledge_node n WHERE n.map_version_id = NEW.map_version_id AND n.id = x)) THEN
    RAISE EXCEPTION '% 引用了本地图版本里不存在的节点', TG_TABLE_NAME USING ERRCODE = 'foreign_key_violation';
  END IF;
  -- 已发布或已撤下的版本不再跑发布前校验：原子只能挂在有效节点上，否则发布时的结论不再成立（安全审查建议 3）
  IF EXISTS (SELECT 1 FROM kge.map_version v WHERE v.id = NEW.map_version_id AND v.status <> 'draft')
     AND EXISTS (SELECT 1 FROM unnest(NEW.node_ids) x
                  WHERE NOT EXISTS (SELECT 1 FROM kge.knowledge_node n
                                     WHERE n.map_version_id = NEW.map_version_id AND n.id = x AND n.status = 'active')) THEN
    RAISE EXCEPTION '% 在非草稿地图版本上引用了已停用的节点', TG_TABLE_NAME USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
-- 采分点：同一（学科, 题目, Rubric 版本）只属于一个地图版本（按 Rubric 版本取采分点不能有歧义）
CREATE FUNCTION kge.guard_scoring_point() RETURNS trigger LANGUAGE plpgsql
  SET search_path = pg_catalog, kge, platform, pg_temp
  AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM kge.scoring_point s
              WHERE s.subject = NEW.subject AND s.item_id = NEW.item_id AND s.rubric_version = NEW.rubric_version
                AND s.map_version_id <> NEW.map_version_id) THEN
    RAISE EXCEPTION 'Rubric 版本 % 已属于另一个地图版本', NEW.rubric_version USING ERRCODE = 'unique_violation';
  END IF;
  RETURN NEW;
END $$;
REVOKE EXECUTE ON FUNCTION kge.guard_map_version(), kge.guard_frozen(), kge.guard_node(), kge.guard_subject(), kge.guard_node_refs(),
  kge.guard_scoring_point() FROM PUBLIC;

CREATE TRIGGER guard_frozen BEFORE INSERT OR UPDATE ON kge.knowledge_node FOR EACH ROW EXECUTE FUNCTION kge.guard_frozen();
CREATE TRIGGER guard_frozen BEFORE INSERT OR UPDATE ON kge.knowledge_edge FOR EACH ROW EXECUTE FUNCTION kge.guard_frozen();
CREATE TRIGGER guard_frozen BEFORE INSERT OR UPDATE ON kge.textbook_projection FOR EACH ROW EXECUTE FUNCTION kge.guard_frozen();
CREATE TRIGGER guard_frozen BEFORE INSERT OR UPDATE ON kge.region_projection FOR EACH ROW EXECUTE FUNCTION kge.guard_frozen();
CREATE TRIGGER guard_frozen BEFORE INSERT OR UPDATE ON kge.skill_link FOR EACH ROW EXECUTE FUNCTION kge.guard_frozen();
CREATE TRIGGER guard_frozen BEFORE INSERT OR UPDATE ON kge.id_migration FOR EACH ROW EXECUTE FUNCTION kge.guard_frozen();
CREATE TRIGGER guard_node BEFORE INSERT OR UPDATE ON kge.knowledge_node FOR EACH ROW EXECUTE FUNCTION kge.guard_node();
CREATE TRIGGER guard_subject BEFORE INSERT OR UPDATE ON kge.skill_link FOR EACH ROW EXECUTE FUNCTION kge.guard_subject();
CREATE TRIGGER guard_subject BEFORE INSERT OR UPDATE ON kge.id_migration FOR EACH ROW EXECUTE FUNCTION kge.guard_subject();
CREATE TRIGGER guard_subject BEFORE INSERT OR UPDATE ON kge.item_tag FOR EACH ROW EXECUTE FUNCTION kge.guard_subject();
CREATE TRIGGER guard_subject BEFORE INSERT OR UPDATE ON kge.scoring_point FOR EACH ROW EXECUTE FUNCTION kge.guard_subject();
CREATE TRIGGER guard_node_refs BEFORE INSERT OR UPDATE ON kge.item_tag FOR EACH ROW EXECUTE FUNCTION kge.guard_node_refs();
CREATE TRIGGER guard_node_refs BEFORE INSERT OR UPDATE ON kge.scoring_point FOR EACH ROW EXECUTE FUNCTION kge.guard_node_refs();
CREATE TRIGGER guard_scoring_point BEFORE INSERT ON kge.scoring_point FOR EACH ROW EXECUTE FUNCTION kge.guard_scoring_point();
CREATE TRIGGER guard_node_refs BEFORE INSERT OR UPDATE ON kge.current_affair FOR EACH ROW EXECUTE FUNCTION kge.guard_node_refs();

-- ---------- 发布前校验（spec 设计要点 3）：只读，按调用者身份执行 ----------
-- 图的定义与 KGE-00 工具（tools/kge-inventory/src/graph.ts）一致：
--   环 = 前置边上能回到自身的节点；传递边 = a→c 存在，且经 a 的另一个直接后续 b（b ≠ c）也能到达 c。
-- severity：error 阻断发布，warning 只报警。检查项的完整清单见 docs/design/kge/id-and-segments.md 第 4 节。
CREATE FUNCTION kge.validate_map_version(p_version uuid)
  RETURNS TABLE (check_name text, severity text, object_id text, detail text)
  LANGUAGE plpgsql STABLE
  SET search_path = pg_catalog, kge, platform, pg_temp
  AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM kge.map_version v WHERE v.id = p_version) THEN
    RAISE EXCEPTION '地图版本不存在' USING ERRCODE = 'no_data_found';
  END IF;

  -- 1. 环
  RETURN QUERY
    WITH RECURSIVE e AS (
      SELECT k.from_id::text AS f, k.to_id::text AS t FROM kge.knowledge_edge k
       WHERE k.map_version_id = p_version AND k.to_map_version_id = p_version AND k.type = 'prerequisite' AND k.status = 'active'
    ), r(src, dst) AS (
      SELECT f, t FROM e
      UNION
      SELECT r.src, e.t FROM r JOIN e ON e.f = r.dst
    )
    SELECT 'cycle', 'error', r.src, '前置边成环' FROM r WHERE r.src = r.dst ORDER BY 3;

  -- 2. 传递边
  RETURN QUERY
    WITH RECURSIVE e AS (
      SELECT DISTINCT k.from_id::text AS f, k.to_id::text AS t FROM kge.knowledge_edge k
       WHERE k.map_version_id = p_version AND k.to_map_version_id = p_version AND k.type = 'prerequisite' AND k.status = 'active'
    ), r(src, dst) AS (
      SELECT f, t FROM e
      UNION
      SELECT r.src, e.t FROM r JOIN e ON e.f = r.dst
    )
    SELECT 'transitive_edge', 'error', ac.f || ' -> ' || ac.t, '可由其他前置路径推出的直连前置'
      FROM e ac
     WHERE EXISTS (SELECT 1 FROM e ab JOIN r ON r.src = ab.t AND r.dst = ac.t WHERE ab.f = ac.f AND ab.t <> ac.t)
     ORDER BY 3;

  -- 3. 孤点：知识节点层没有任何有效边、没有题目标签、也没有教材投影
  RETURN QUERY
    SELECT 'orphan', 'error', n.id::text, '没有边、题目标签或教材投影'
      FROM kge.knowledge_node n
     WHERE n.map_version_id = p_version AND n.level = 'node' AND n.status = 'active'
       AND NOT EXISTS (SELECT 1 FROM kge.knowledge_edge k WHERE k.status = 'active'
                        AND ((k.map_version_id = p_version AND k.from_id = n.id) OR (k.to_map_version_id = p_version AND k.to_id = n.id)))
       AND NOT EXISTS (SELECT 1 FROM kge.item_tag i WHERE i.map_version_id = p_version AND i.tag_review_status <> 'rejected'
                        AND (i.main_node_id = n.id OR n.id = ANY (i.node_ids)))
       AND NOT EXISTS (SELECT 1 FROM kge.textbook_projection p WHERE p.map_version_id = p_version AND p.node_id = n.id)
     ORDER BY 3;

  -- 4. 边界缺失：参与掌握度计算的知识节点缺包含、不包含、典型题例或认知层次；易错点缺识别特征（includes，“在作答中表现为什么样子”，KGE 第三章）
  RETURN QUERY
    SELECT 'missing_boundary', 'error', n.id::text,
           concat_ws('、', CASE WHEN cardinality(n.includes) = 0 THEN CASE WHEN n.kind = 'pitfall' THEN '缺识别特征' ELSE '缺包含' END END,
                           CASE WHEN n.kind <> 'pitfall' AND cardinality(n.excludes) = 0 THEN '缺不包含' END,
                           CASE WHEN n.kind <> 'pitfall' AND cardinality(n.examples) = 0 THEN '缺典型题例' END,
                           CASE WHEN n.kind <> 'pitfall' AND cardinality(n.cognitive_levels) = 0 THEN '缺认知层次' END)
      FROM kge.knowledge_node n
     WHERE n.map_version_id = p_version AND n.status = 'active'
       AND ((n.level = 'node' AND n.kind IN ('kp', 'ability_kp', 'viewpoint')
             AND (cardinality(n.includes) = 0 OR cardinality(n.excludes) = 0 OR cardinality(n.examples) = 0 OR cardinality(n.cognitive_levels) = 0))
            OR (n.kind = 'pitfall' AND cardinality(n.includes) = 0))
     ORDER BY 3;

  -- 5. 悬空引用：有效的边、投影、挂接、标签、采分点、时政指向已停用或不存在的节点
  RETURN QUERY
    WITH refs(src, ver, node) AS (
      SELECT 'knowledge_edge:' || k.id, k.map_version_id, k.from_id::text FROM kge.knowledge_edge k
       WHERE k.map_version_id = p_version AND k.status = 'active'
      UNION ALL
      SELECT 'knowledge_edge:' || k.id, k.to_map_version_id, k.to_id::text FROM kge.knowledge_edge k
       WHERE k.map_version_id = p_version AND k.status = 'active'
      UNION ALL
      SELECT 'textbook_projection:' || p.id, p.map_version_id, p.node_id::text FROM kge.textbook_projection p WHERE p.map_version_id = p_version
      UNION ALL
      SELECT 'region_projection:' || p.id, p.map_version_id, p.node_id::text FROM kge.region_projection p WHERE p.map_version_id = p_version
      UNION ALL
      SELECT 'skill_link:' || s.subject || ':' || s.local_id, s.map_version_id, s.node_id::text FROM kge.skill_link s WHERE s.map_version_id = p_version
      UNION ALL
      SELECT 'item_tag:' || i.subject || ':' || i.item_id, i.map_version_id, x::text
        FROM kge.item_tag i, unnest(i.node_ids || i.main_node_id) x WHERE i.map_version_id = p_version AND i.tag_review_status <> 'rejected'
      UNION ALL
      SELECT 'scoring_point:' || s.id, s.map_version_id, x::text FROM kge.scoring_point s, unnest(s.node_ids) x WHERE s.map_version_id = p_version
      UNION ALL
      SELECT 'current_affair:' || c.id, c.map_version_id, x::text FROM kge.current_affair c, unnest(c.node_ids) x
       WHERE c.map_version_id = p_version AND c.review_status <> 'retired'
    )
    SELECT DISTINCT 'dangling_ref', 'error', refs.src, '指向已停用或不存在的节点 ' || refs.node
      FROM refs
     WHERE NOT EXISTS (SELECT 1 FROM kge.knowledge_node n WHERE n.map_version_id = refs.ver AND n.id = refs.node AND n.status = 'active')
     ORDER BY 3;

  -- 8. 边类型与端点类型不符：素材不进依赖图；has_pitfall 指向易错点；uses_method 指向方法
  RETURN QUERY
    SELECT DISTINCT 'edge_kind_mismatch', 'error', 'knowledge_edge:' || k.id,
           CASE WHEN k.type = 'prerequisite' THEN '素材节点不进前置依赖图' WHEN k.type = 'has_pitfall' THEN '终点不是易错点' ELSE '终点不是方法' END
      FROM kge.knowledge_edge k
      JOIN kge.knowledge_node a ON a.map_version_id = k.map_version_id AND a.id = k.from_id
      JOIN kge.knowledge_node b ON b.map_version_id = k.to_map_version_id AND b.id = k.to_id
     WHERE k.map_version_id = p_version AND k.status = 'active'
       AND ((k.type = 'prerequisite' AND 'material' IN (a.kind, b.kind))
            OR (k.type = 'has_pitfall' AND b.kind IS DISTINCT FROM 'pitfall')
            OR (k.type = 'uses_method' AND b.kind IS DISTINCT FROM 'method'))
     ORDER BY 3;

  -- 9. 题目标签的认知层次不在主考点节点的层次集合里（诊断单元是“节点 × 层次”）
  RETURN QUERY
    SELECT 'tag_level_mismatch', 'error', 'item_tag:' || i.subject || ':' || i.item_id,
           format('层次 %s 不在主考点 %s 的 %s 里', i.cognitive_level, i.main_node_id, n.cognitive_levels)
      FROM kge.item_tag i JOIN kge.knowledge_node n ON n.map_version_id = i.map_version_id AND n.id = i.main_node_id
     WHERE i.map_version_id = p_version AND i.tag_review_status <> 'rejected' AND i.cognitive_level IS NOT NULL
       AND NOT (i.cognitive_level = ANY (n.cognitive_levels))
     ORDER BY 3;

  -- 10. 跨版本的边指向未发布的版本（对端学科换版后旧边过期；本版本冻结后只能在新版本里改，只报警告）
  RETURN QUERY
    SELECT 'stale_cross_edge', 'warning', 'knowledge_edge:' || k.id, '对端地图版本不是已发布'
      FROM kge.knowledge_edge k JOIN kge.map_version v ON v.id = k.to_map_version_id
     WHERE k.map_version_id = p_version AND k.to_map_version_id <> p_version AND k.status = 'active' AND v.status <> 'published'
     ORDER BY 3;

  -- 6. 主考点少于 3 道题（粒度信号，只报警告）
  RETURN QUERY
    SELECT 'few_items', 'warning', n.id::text,
           format('主考点题目 %s 道', (SELECT count(*) FROM kge.item_tag i WHERE i.map_version_id = p_version AND i.main_node_id = n.id
                                                                         AND i.tag_review_status <> 'rejected'))
      FROM kge.knowledge_node n
     WHERE n.map_version_id = p_version AND n.level = 'node' AND n.status = 'active' AND n.kind IN ('kp', 'ability_kp', 'viewpoint')
       AND (SELECT count(*) FROM kge.item_tag i WHERE i.map_version_id = p_version AND i.main_node_id = n.id AND i.tag_review_status <> 'rejected') < 3
     ORDER BY 3;

  -- 11. 地区投影的（地区, 考试年份）在考情政策库里没有已发布方案（警告；政策库回填前不阻断）。
  --     政策库由迁移 0013 建；这里用动态查询，0013 未执行或已回滚时跳过（specs/policy-schema-v0.9 设计要点 5）
  IF to_regclass('policy.exam_scheme') IS NOT NULL THEN
    RETURN QUERY EXECUTE
      'SELECT DISTINCT ''policy_scheme_missing''::text, ''warning''::text, p.region || '':'' || p.exam_year,
              ''考情政策库没有该城市该届别的已发布方案''::text
         FROM kge.region_projection p
        WHERE p.map_version_id = $1
          AND NOT EXISTS (SELECT 1 FROM policy.exam_scheme e
                           WHERE e.city = p.region AND e.cohort = p.exam_year AND e.status = ''published'')
        ORDER BY 3' USING p_version;
  END IF;

  -- 7. 时政已过期仍为已发布：只报警告，由体检流程下架（KGE 第七章“过期由体检自动下架，不进骨架”）
  RETURN QUERY
    SELECT 'expired_current_affair', 'warning', c.id::text, format('失效日期 %s', c.valid_until)
      FROM kge.current_affair c
     WHERE c.map_version_id = p_version AND c.review_status = 'published' AND c.valid_until < current_date
     ORDER BY 3;
END $$;
REVOKE EXECUTE ON FUNCTION kge.validate_map_version(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kge.validate_map_version(uuid) TO starbuddy_kge, starbuddy_learning, starbuddy_authz;

-- 发布：属主 authz（只有它能把版本改为已发布）；只有知识服务能调用。有阻断问题时拒绝；同学科原已发布版本转为撤下
CREATE FUNCTION kge.publish_map_version(p_version uuid) RETURNS timestamptz
  LANGUAGE plpgsql SECURITY DEFINER
  SET search_path = pg_catalog, kge, platform, pg_temp
  AS $$
DECLARE
  v kge.map_version%ROWTYPE;
  v_errors integer;
  v_now timestamptz := now();
BEGIN
  SELECT * INTO v FROM kge.map_version WHERE id = p_version FOR UPDATE;
  IF NOT FOUND OR v.status <> 'draft' THEN
    RAISE EXCEPTION '只能发布草稿地图版本' USING ERRCODE = 'check_violation';
  END IF;
  -- 同一学科的发布串行执行（事务级咨询锁）：两个草稿并发发布时，后一个等前一个提交，不会静默撤下刚发布的版本（安全审查建议 2）
  PERFORM pg_advisory_xact_lock(hashtext('kge.publish_map_version:' || v.subject::text));
  PERFORM 1 FROM kge.map_version WHERE subject = v.subject AND status = 'published' FOR UPDATE;
  SELECT count(*) INTO v_errors FROM kge.validate_map_version(p_version) c WHERE c.severity = 'error';
  IF v_errors > 0 THEN
    RAISE EXCEPTION '发布校验有 % 个阻断问题，拒绝发布', v_errors USING ERRCODE = 'check_violation';
  END IF;
  UPDATE kge.map_version SET status = 'retired' WHERE subject = v.subject AND status = 'published';
  UPDATE kge.map_version SET status = 'published', published_at = v_now WHERE id = p_version;
  RETURN v_now;
END $$;
ALTER FUNCTION kge.publish_map_version(uuid) OWNER TO starbuddy_authz;
REVOKE EXECUTE ON FUNCTION kge.publish_map_version(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kge.publish_map_version(uuid) TO starbuddy_kge;

-- ---------- 表权限：只有知识服务能写；任何角色都没有 DELETE（撤下走状态） ----------
GRANT SELECT, INSERT ON kge.map_version TO starbuddy_kge;
GRANT UPDATE (version, standard_year, status) ON kge.map_version TO starbuddy_kge;
GRANT SELECT, INSERT, UPDATE ON kge.curriculum_item, kge.knowledge_node, kge.knowledge_edge, kge.textbook_projection,
                                 kge.region_projection, kge.skill_link, kge.map_change, kge.id_migration, kge.item_family,
                                 kge.item_tag, kge.current_affair, kge.content_review
  TO starbuddy_kge;
-- 采分点只能新增，不能修改（改内容发新 rubric_version）
GRANT SELECT, INSERT ON kge.scoring_point TO starbuddy_kge;
-- quote_hash 是生成列：任何角色写它都会被库拒绝（76_kge_map 断言）
-- 学科服务与编排：读全部
GRANT SELECT ON kge.map_version, kge.curriculum_item, kge.knowledge_node, kge.knowledge_edge, kge.textbook_projection,
                kge.region_projection, kge.skill_link, kge.map_change, kge.id_migration, kge.item_family, kge.item_tag,
                kge.scoring_point, kge.current_affair, kge.content_review
  TO starbuddy_learning;
-- BFF（星图渲染）：只读已发布版本的节点、边、投影（spec 待人确认 #4，按默认）；核对人不给前端
GRANT SELECT ON kge.map_version, kge.knowledge_node, kge.knowledge_edge, kge.region_projection TO starbuddy_bff;
GRANT SELECT (id, map_version_id, node_id, edition, edition_year, printing, volume, chapter, page_from, page_to, quote_text,
              quote_hash, mapping_state)
  ON kge.textbook_projection TO starbuddy_bff;
-- 洞察服务：只读版本、节点与边（核对判断的 target_id）
GRANT SELECT ON kge.map_version, kge.knowledge_node, kge.knowledge_edge TO starbuddy_insight;
-- 发布函数的属主：跑校验、改版本状态
GRANT SELECT ON kge.map_version, kge.knowledge_node, kge.knowledge_edge, kge.textbook_projection, kge.region_projection,
                kge.skill_link, kge.item_tag, kge.scoring_point, kge.current_affair
  TO starbuddy_authz;
GRANT UPDATE (status, published_at) ON kge.map_version TO starbuddy_authz;

-- ---------- RLS ----------
ALTER TABLE kge.map_version ENABLE ROW LEVEL SECURITY;
ALTER TABLE kge.map_version FORCE ROW LEVEL SECURITY;
CREATE POLICY map_version_kge ON kge.map_version FOR ALL TO starbuddy_kge USING (true) WITH CHECK (true);
CREATE POLICY map_version_read ON kge.map_version FOR SELECT TO starbuddy_learning, starbuddy_insight USING (true);
CREATE POLICY map_version_front_read ON kge.map_version FOR SELECT TO starbuddy_bff USING (status = 'published');

ALTER TABLE kge.knowledge_node ENABLE ROW LEVEL SECURITY;
ALTER TABLE kge.knowledge_node FORCE ROW LEVEL SECURITY;
CREATE POLICY node_kge ON kge.knowledge_node FOR ALL TO starbuddy_kge USING (true) WITH CHECK (true);
CREATE POLICY node_read ON kge.knowledge_node FOR SELECT TO starbuddy_learning, starbuddy_insight USING (true);
CREATE POLICY node_front_read ON kge.knowledge_node FOR SELECT TO starbuddy_bff
  USING (status = 'active' AND EXISTS (SELECT 1 FROM kge.map_version v WHERE v.id = map_version_id AND v.status = 'published'));

ALTER TABLE kge.knowledge_edge ENABLE ROW LEVEL SECURITY;
ALTER TABLE kge.knowledge_edge FORCE ROW LEVEL SECURITY;
CREATE POLICY edge_kge ON kge.knowledge_edge FOR ALL TO starbuddy_kge USING (true) WITH CHECK (true);
CREATE POLICY edge_read ON kge.knowledge_edge FOR SELECT TO starbuddy_learning, starbuddy_insight USING (true);
CREATE POLICY edge_front_read ON kge.knowledge_edge FOR SELECT TO starbuddy_bff
  USING (status = 'active'
         AND EXISTS (SELECT 1 FROM kge.map_version v WHERE v.id = map_version_id AND v.status = 'published')
         AND EXISTS (SELECT 1 FROM kge.map_version v WHERE v.id = to_map_version_id AND v.status = 'published'));

ALTER TABLE kge.textbook_projection ENABLE ROW LEVEL SECURITY;
ALTER TABLE kge.textbook_projection FORCE ROW LEVEL SECURITY;
CREATE POLICY textbook_kge ON kge.textbook_projection FOR ALL TO starbuddy_kge USING (true) WITH CHECK (true);
CREATE POLICY textbook_read ON kge.textbook_projection FOR SELECT TO starbuddy_learning USING (true);
-- 前端只看已核对的投影（页码错对开卷定位是致命错误），且所在节点有效
CREATE POLICY textbook_front_read ON kge.textbook_projection FOR SELECT TO starbuddy_bff
  USING (mapping_state = 'verified'
         AND EXISTS (SELECT 1 FROM kge.map_version v WHERE v.id = map_version_id AND v.status = 'published')
         AND EXISTS (SELECT 1 FROM kge.knowledge_node n WHERE n.map_version_id = textbook_projection.map_version_id
                       AND n.id = textbook_projection.node_id AND n.status = 'active'));

ALTER TABLE kge.region_projection ENABLE ROW LEVEL SECURITY;
ALTER TABLE kge.region_projection FORCE ROW LEVEL SECURITY;
CREATE POLICY region_kge ON kge.region_projection FOR ALL TO starbuddy_kge USING (true) WITH CHECK (true);
CREATE POLICY region_read ON kge.region_projection FOR SELECT TO starbuddy_learning USING (true);
CREATE POLICY region_front_read ON kge.region_projection FOR SELECT TO starbuddy_bff
  USING (EXISTS (SELECT 1 FROM kge.map_version v WHERE v.id = map_version_id AND v.status = 'published')
         AND EXISTS (SELECT 1 FROM kge.knowledge_node n WHERE n.map_version_id = region_projection.map_version_id
                       AND n.id = region_projection.node_id AND n.status = 'active'));

-- 其余表：知识服务读写（没有 DELETE 授权），学科服务只读；BFF 与洞察服务没有授权
DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['curriculum_item', 'skill_link', 'map_change', 'id_migration', 'item_family', 'item_tag',
                           'scoring_point', 'current_affair', 'content_review'] LOOP
    EXECUTE format('ALTER TABLE kge.%I ENABLE ROW LEVEL SECURITY', r);
    EXECUTE format('ALTER TABLE kge.%I FORCE ROW LEVEL SECURITY', r);
    EXECUTE format('CREATE POLICY %I ON kge.%I FOR ALL TO starbuddy_kge USING (true) WITH CHECK (true)', r || '_kge', r);
    EXECUTE format('CREATE POLICY %I ON kge.%I FOR SELECT TO starbuddy_learning USING (true)', r || '_read', r);
  END LOOP;
END $$;
