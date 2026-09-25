#!/usr/bin/env python3
"""PreToolUse 守卫：确定性拦截高风险操作。退出码 2 = 阻止，并把原因回传给 Claude。"""
import json, re, sys

data = json.load(sys.stdin)
tool = data.get("tool_name", "")
inp = data.get("tool_input", {}) or {}

def block(reason: str):
    print(f"[guard] 已阻止：{reason}。如确需执行，请停下并请人确认。", file=sys.stderr)
    sys.exit(2)

# 生产环境标识：按实际填写（Supabase 生产 project ref、生产域名等）
PROD_MARKERS = [r"PROD_PROJECT_REF_PLACEHOLDER", r"PROD_DOMAIN_PLACEHOLDER", r"--prod\b"]

if tool == "Bash":
    cmd = inp.get("command", "")
    dangerous = [
        (r"\brm\s+-rf\s+(/|~|\*|\.\s*$)", "危险的递归删除"),
        (r"\bgit\s+push\b.*\b(main|master)\b", "直接推送主干"),
        (r"\bgit\s+push\b.*--force", "强制推送"),
        (r"\bsupabase\b.*\bdb\s+push\b", "直接推送数据库迁移"),
        (r"\bcurl\b.*\|\s*(sh|bash)", "管道执行远程脚本"),
        (r"\b(cat|less|head|tail|printenv|env)\b.*(\.env|SECRET|TOKEN|API_KEY)", "读取密钥"),
    ]
    for pat, why in dangerous:
        if re.search(pat, cmd, re.I):
            block(why)
    for pat in PROD_MARKERS:
        if re.search(pat, cmd, re.I):
            block("命令涉及生产环境")

if tool == "Read":
    path = inp.get("file_path", "")
    if re.search(r"(^|/)\.env(\.(?!example)|$)|(^|/)secrets?/|\.pem$|\.key$", path):
        block(f"读取密钥类文件 {path}")

if tool in ("Write", "Edit", "MultiEdit"):
    path = inp.get("file_path", "")
    if re.search(r"(^|/)legacy/", path):
        block(f"legacy/ 下的四科旧代码只读 {path}")
    if re.search(r"(^|/)\.env(\.|$)|(^|/)secrets?/|\.pem$|\.key$", path):
        block(f"写入密钥类文件 {path}")
    if re.search(r"(^|/)evals/.+/gold/", path):
        block(f"修改教研金标准数据 {path}")

sys.exit(0)
