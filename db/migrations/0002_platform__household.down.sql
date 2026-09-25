-- 0002 回滚：先删表（连同依赖授权函数的策略），再删函数
DROP TABLE IF EXISTS platform.subject_enrollment;
DROP TABLE IF EXISTS platform.consent_record;
DROP TABLE IF EXISTS platform.guardian_invite;
DROP TABLE IF EXISTS platform.guardian_student_link;
DROP TABLE IF EXISTS platform.student_profile;
DROP TABLE IF EXISTS platform.household;
DROP TABLE IF EXISTS platform.account_contact;
DROP TABLE IF EXISTS platform.account;
DROP FUNCTION IF EXISTS platform.redeem_guardian_invite(text);
DROP FUNCTION IF EXISTS platform.create_guardian_invite(uuid, text, interval);
DROP FUNCTION IF EXISTS platform.can_read_household(uuid);
DROP FUNCTION IF EXISTS platform.can_read_student(uuid);
DROP FUNCTION IF EXISTS platform.is_linked_guardian(uuid);
DROP FUNCTION IF EXISTS platform.is_self_student(uuid);
