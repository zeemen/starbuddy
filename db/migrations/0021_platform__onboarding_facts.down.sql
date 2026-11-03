-- 0021 回滚：删除各科教材版本表与写入函数
DROP FUNCTION platform.set_subject_textbook(uuid, platform.subject, text);
DROP TABLE platform.student_subject_textbook;
