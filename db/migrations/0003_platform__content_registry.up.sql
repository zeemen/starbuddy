-- 0003 platform · 内容登记：课程版本（六段标识）、能力点（学科命名空间）、题目（准入状态）
-- 各科保留自己的坐标系与粒度，平台以 subject + local_id 索引（完整方案 5.5）。

CREATE TABLE platform.curriculum_version (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  region             text NOT NULL CHECK (region ~ '^[a-z][a-z0-9\-]{1,31}$'),
  exam_year          smallint NOT NULL CHECK (exam_year BETWEEN 2026 AND 2100),
  grade              smallint NOT NULL CHECK (grade BETWEEN 7 AND 9),
  textbook_version   text NOT NULL CHECK (textbook_version ~ '^[a-z0-9][a-z0-9\-]{1,31}$'),
  subject            platform.subject NOT NULL,
  content_version    text NOT NULL CHECK (content_version ~ '^\d+\.\d+\.\d+$'),
  status             text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published', 'retired')),
  published_at       timestamptz,
  UNIQUE (region, exam_year, grade, textbook_version, subject, content_version),
  CHECK ((status = 'draft') = (published_at IS NULL))
);

CREATE TABLE platform.skill_node (
  subject                 platform.subject NOT NULL,
  local_id                platform.skill_local_id NOT NULL,
  curriculum_version_id   uuid NOT NULL REFERENCES platform.curriculum_version (id),
  parent_local_id         platform.skill_local_id,
  label_code              text NOT NULL CHECK (label_code ~ '^[A-Za-z0-9_.\-]{1,64}$'),
  PRIMARY KEY (subject, local_id),
  FOREIGN KEY (subject, parent_local_id) REFERENCES platform.skill_node (subject, local_id)
);
COMMENT ON TABLE platform.skill_node IS '能力点登记。显示名称由各科内容包提供，这里只存编码';

CREATE TABLE platform.item (
  subject                 platform.subject NOT NULL,
  item_id                 platform.skill_local_id NOT NULL,
  curriculum_version_id   uuid NOT NULL REFERENCES platform.curriculum_version (id),
  skill_local_id          platform.skill_local_id,
  admission               platform.admission NOT NULL DEFAULT 'pending_review',
  admitted_at             timestamptz,
  PRIMARY KEY (subject, item_id),
  FOREIGN KEY (subject, skill_local_id) REFERENCES platform.skill_node (subject, local_id),
  CHECK ((admission = 'admitted') = (admitted_at IS NOT NULL))
);
COMMENT ON TABLE platform.item IS '题目登记与准入状态。题面与答案在各科内容库，不在平台 schema';

GRANT SELECT ON platform.curriculum_version, platform.skill_node, platform.item TO starbuddy_bff, starbuddy_learning;
-- 内容发布流水线由学习服务执行（发布闸门，完整方案第六章）
GRANT INSERT, UPDATE ON platform.curriculum_version, platform.skill_node, platform.item TO starbuddy_learning;

ALTER TABLE platform.curriculum_version ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.curriculum_version FORCE ROW LEVEL SECURITY;
CREATE POLICY cv_public_read ON platform.curriculum_version FOR SELECT TO starbuddy_bff USING (status = 'published');
CREATE POLICY cv_service ON platform.curriculum_version FOR ALL TO starbuddy_learning USING (true) WITH CHECK (true);

ALTER TABLE platform.skill_node ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.skill_node FORCE ROW LEVEL SECURITY;
CREATE POLICY skill_node_read ON platform.skill_node FOR SELECT TO starbuddy_bff USING (true);
CREATE POLICY skill_node_service ON platform.skill_node FOR ALL TO starbuddy_learning USING (true) WITH CHECK (true);

ALTER TABLE platform.item ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.item FORCE ROW LEVEL SECURITY;
CREATE POLICY item_read ON platform.item FOR SELECT TO starbuddy_bff USING (admission = 'admitted');
CREATE POLICY item_service ON platform.item FOR ALL TO starbuddy_learning USING (true) WITH CHECK (true);
