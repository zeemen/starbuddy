"""页面优先检索 + 全文兜底（KGE-03 PR ③；spec 设计要点 6，待人确认 #5）。

模块：tokenize（jieba 分词）、bm25（进程内 BM25）、chunks（只切公共层：已发布页面快照 + 已发布原子）、
pipeline（查阅路径与上下文包；对照路径 ChunkRetriever）、resolver（node.resolve 的全文检索，经 Resolver 接口）、
signals（补页面信号，不含查询原文与学生标识）、vector（向量检索只留接口）。
"""
