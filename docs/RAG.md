# 知识库（外挂 RAG）—— 愿景与现状

## 愿景：分层、可插拔的检索能力

用能力来描述架构，而不是用某一代实现来命名它：不说「我们采用 sqlite-vec + BGE 做 RAG」，而说「我们定义了语义检索能力，当前实现是 Embedding + sqlite-vec」。每个能力独立演进，AI coding 改动的爆炸半径小。

Adaptive RAG：用一个 query classifier 按查询复杂度把每个 query 路由到合适的 pipeline：

- **精确匹配层**：FTS5/BM25 精确匹配
- **语义相似层**：sqlite-vec 轻量语义近邻
- **深度推理导航层**：PageIndex——长文档、复杂查询、需要引用溯源

组件地图：

- **Docling**：负责「读懂文档」——文档理解、解析、结构化
- **Query Classifier**：判断 Query 的检索需求，减少不必要的高开销检索，提升端到端延迟和准确率
- **FTS5/BM25**：精确词项、标题、编号、术语、ID 检索
- **Embedding**：把文本映射到语义空间（BGE / Qwen3 / doubao-embedding-vision 等），负责中英文语义；端点可配置化（如 https://ark.cn-beijing.volces.com/api/coding/v3）
- **sqlite-vec**：轻量语义近邻检索
- **PageIndex**：长文档结构导航、复杂问题定位、溯源
- **Context Retrieval**：统一协调不同检索来源，形成上下文
- **Daemon + RPC**：把重量级/独立能力服务化

可扩展性：Reranker、Hybrid search 等。最终 RAG 做成可插拔服务，与任意 agent 对接——解耦合，每个模块自行演进。

## 现状：能力 → 当前实现 → 位置

| 能力 | 当前实现 | 状态 | 位置 |
| --- | --- | --- | --- |
| 精确匹配层 | SQLite FTS5（bm25），中文按双字 bigram 切分 | ✅ | `app/utils.py` 的 `segment_for_index`、`knowledge_chunks_fts` |
| Embedding | 任意 OpenAI 兼容 `/embeddings`，全局检索模型配置（设置 → 检索模型），与 LLM 配置分离 | ✅ 可配置化 | `app/provider.py` 的 `embed`、`app/store.py` 的 `get_retrieval_spec` |
| 语义相似层 | **sqlite-vec**（vec0 虚表按维度建表、cosine 距离 KNN） | ✅ | `app/store.py` 的 `search_knowledge_vec`、`app/database.py` 的 `ensure_vec_table` |
| Reranker | Cohere 兼容 `/rerank`，混合检索取前 20 交给 rerank 模型重排后取前 limit；未配置或调用失败时降级为混合排序 | ✅ 可配置化 | `app/provider.py` 的 `rerank`、`app/knowledge.py` 的 `search` |
| 文档理解 | **v0**：轻量提取器（mammoth / openpyxl / python-pptx / pdfminer.six） | ⚠️ Docling 的替换位 | `app/knowledge.py` 的 `_EXTRACTORS` |
| Context Retrieval | 每轮自动检索，FTS 0.45 + 余弦 0.75 混合，取前 5，注入 `<knowledge>`，引用随 `context.ready` 给前端 | ✅ | `app/context.py` 的 `build()` |
| 检索来源协调 | 单层混合检索 | ⚠️ 多层路由的挂载点 | `KnowledgeService.search` |
| 管理界面 | 知识库页：导入 / 列表 / 删除 / 回收站 / 试检索；设置 → 检索模型：嵌入 / rerank 各一张卡片（地址 / 模型名 / API Key / 测试 / 清除） | ✅ | `frontend/src/pages/KnowledgePage.tsx`、`frontend/src/pages/settings/RetrievalPanel.tsx` |
| Query Classifier / PageIndex / Daemon+RPC | 未建 | 🚧 见路线 | — |

## 支持的格式

| 格式 | 提取方式 | 备注 |
| --- | --- | --- |
| txt / md / 各类源码 | utf-8-sig → utf-16 → gb18030 → charset_normalizer 依次探测 | `ALLOWED_EXTENSIONS` |
| docx | mammoth 转 markdown，内嵌图片剥成纯文字 | 标题/列表/表格结构保留 |
| xlsx | openpyxl（read_only + data_only），每张表 `## 表格：<名>` + 竖线行 | 只取单元格现值，不算公式 |
| pptx | python-pptx，每页 `## 幻灯片 N` + 文本框/表格/备注 | |
| pdf | pdfminer.six，每页 `## 第 N 页` | 仅文字版；扫描版提取为空即跳过 |

旧版二进制格式 .doc/.xls/.ppt 直接拒绝并提示另存。导入的数据流：

```
文件 → 提取文字（按扩展名分派）→ chunk_text（1800 字符目标、220 重叠、空行分段）
    → FTS5 索引 → [可选] 全部块向量 → 原文件字节存数据目录 objects/
```

带嵌入配置的导入同时把向量写进 vec0 虚表（按维度一张表，`knowledge_chunks_vec_{dim}`，cosine 距离、模型与项目作为 metadata 过滤列）；检索时对查询向量做 KNN，余弦距离换算回相似度后沿用原来的 0.75 权重，FTS 0.45 权重不变。配置了 rerank 模型时，混合排序取前 20 条候选（`RERANK_CANDIDATES`）发给 rerank 接口，返回的 `relevance_score` 直接作为结果的 `score`，`source` 标为 `reranked`；rerank 调用失败只是降级回混合排序，检索不会失败。

嵌入与 rerank 是**全局单一配置**：存设置 KV（`retrieval_embedding` / `retrieval_rerank`），API Key 存系统凭据管理器（固定标识 `retrieval-embedding` / `retrieval-rerank`），与模型配置（LLM）完全分离。记忆相关性排序与知识库共用嵌入配置；模型配置表单不再有「嵌入模型」字段（旧库迁移时把默认配置的值搬进检索配置）。

## 兜底上限（backstop，不是 UX）

- 单文件 50MB（`MAX_KNOWLEDGE_BYTES`）
- xlsx 每表 5,000 行、单元格 500 字符
- 单文档提取文字总量 1,000,000 字符——所有块的向量是在导入时一次性发给嵌入接口的，超大规模文档会打爆嵌入接口

## 已知限制（接受的，不是遗漏）

- 同一个嵌入模型名换到不同维度的端点后，旧维度的块不再被新查询语义命中（两张维度表不互通）；重新导入即可恢复
- 一个文档所有块一次发给嵌入接口，块多时受服务商单请求批量上限约束
- 永久删除回收站条目不回收 objects/ 里的文件字节
- 无按 content_hash 去重

## 演进路线（每步独立可停，都是「换实现、不动能力」）

1. ~~sqlite-vec 替换语义近邻~~ ✅ 已完成：vec0 KNN 替换全量余弦；同时修复了回收站恢复知识文档不重建索引的缺陷（恢复时从 `knowledge_chunks` 重建 FTS 与 vec 行）。
2. **Docling 作为可选的文档理解后端**：`_EXTRACTORS` 已经是分派点。Docling 带 torch/布局模型，重，建议按 Daemon + RPC 独立进程部署，主应用零负担；轻量提取器继续作为默认。
3. **Query Classifier**：等真的有多层（至少 PageIndex 就位）可路由时才值得——现在两层混合检索一次调用的延迟低于一次分类调用。
4. **PageIndex 长文档导航层**：长文档结构索引、溯源，独立于现有分块。
5. ~~Reranker~~ ✅ 已完成：Cohere 兼容 `/rerank`，独立检索模型配置，失败降级；剩下的是可插拔服务化——把整个知识库按 MCP 或 HTTP sidecar 暴露给任意 agent。

## 验证

- 后端 `backend/tests/test_knowledge.py`：docx/xlsx/pptx/pdf 提取与检索、中文 CID 字体 PDF、图片剥离、截断上限、旧格式与坏文件跳过、KNN 最近邻正确性、删除清理 vec 行、恢复重建、迁移回填；`test_retrieval.py`：检索模型配置往返、api_key 语义、迁移搬家、rerank 重排与降级、测试端点。
- 前端 `frontend/src/api/knowledge.test.ts`：导入结果合并、accept 列表；`retrieval.test.ts`：卡片载荷与 api_key 语义；`profiles.test.ts`：模型配置字段的往返。
- 端到端：知识库页上传 → 列表 → 试检索 → 对话引用；恢复后检索复原。
