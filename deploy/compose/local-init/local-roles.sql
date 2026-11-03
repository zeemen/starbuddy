-- 本机合成全栈（deploy/compose/local-test.yaml）的登录角色：每个服务一个，只继承对应的应用角色（与各服务启动时的角色断言一致）。
-- 只在本机一次性容器里（trust 认证、只在共享网络命名空间的回环上监听），名字是合成的，没有口令；不用于任何共享或正式环境。
CREATE ROLE local_bff LOGIN IN ROLE starbuddy_bff;
CREATE ROLE local_learning LOGIN IN ROLE starbuddy_learning;
CREATE ROLE local_insight LOGIN IN ROLE starbuddy_insight;
CREATE ROLE local_kge_read LOGIN IN ROLE starbuddy_kge_read;
CREATE ROLE local_kge LOGIN IN ROLE starbuddy_kge;
CREATE ROLE local_review LOGIN IN ROLE starbuddy_review;
CREATE ROLE local_scheduler LOGIN IN ROLE starbuddy_scheduler;
