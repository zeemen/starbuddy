"""知识服务读工具（KGE-03 PR ②；spec 设计要点 5，待人确认 #3、#4、#7）。

契约：packages/contracts/openapi/knowledge-service.v0.9.yaml。同一层工具实现（tools.py）由两处暴露：
HTTP（http.py，FastAPI，路径 POST /v0/<工具名>）与 MCP（mcp_server.py，工具名 = operationId）。
只读公共层：数据库只用 starbuddy_kge_read 连接（reader.py），页面只读编译器导出的快照目录（pages.py）。
个人层三个工具（evidence.write、mastery.get、mastery.explain）回 501。
"""
