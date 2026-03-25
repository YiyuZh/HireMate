# HireMate RAG 向量库操作手册

## 1. 目标边界

- RAG 现在是增强层，不替代规则评分器。
- RAG 不直接产出“通过 / 待复核 / 淘汰”最终结论。
- 当前主要服务三条链路：
- JD 对齐 / 同义词扩展
- evidence grounding
- AI reviewer grounding

## 2. 相关文件

- RAG 核心目录：
- [src/rag/indexer.py](d:/HireMate/src/rag/indexer.py)
- [src/rag/retriever.py](d:/HireMate/src/rag/retriever.py)
- [src/rag/chunker.py](d:/HireMate/src/rag/chunker.py)
- [src/rag/corpus.py](d:/HireMate/src/rag/corpus.py)
- [src/rag/store.py](d:/HireMate/src/rag/store.py)
- 业务接入点：
- [src/jd_parser.py](d:/HireMate/src/jd_parser.py)
- [src/screener.py](d:/HireMate/src/screener.py)
- [src/ai_reviewer.py](d:/HireMate/src/ai_reviewer.py)
- 运营/回归脚本：
- [scripts/rag_build_sample_index.py](d:/HireMate/scripts/rag_build_sample_index.py)
- [scripts/rag_index_historical_corpus.py](d:/HireMate/scripts/rag_index_historical_corpus.py)
- [scripts/rag_incremental_index.py](d:/HireMate/scripts/rag_incremental_index.py)
- [scripts/rag_smoke_test.py](d:/HireMate/scripts/rag_smoke_test.py)
- [scripts/rag_benchmark.py](d:/HireMate/scripts/rag_benchmark.py)
- benchmark 样本：
- [data/rag_benchmark_samples.jsonl](d:/HireMate/data/rag_benchmark_samples.jsonl)

## 3. 向量库存储位置

- 逻辑默认目录：`/app/data/vector_store`
- 当前本机实际路径通常会映射到：`D:\app\data\vector_store`

目录结构：

```text
/app/data/vector_store/
  manifest.json
  collections/
    default/
      chunks.jsonl
      embeddings.jsonl
      stats.json
```

说明：
- `chunks.jsonl`：chunk 文本和 metadata
- `embeddings.jsonl`：向量数据
- `stats.json`：当前 collection 统计和 embedding 配置摘要
- `manifest.json`：全局 store 信息

## 4. 当前支持的 embedding provider

- `mock`
- `openai`
- `openai_compatible`

当前实现说明：
- `mock` 适合本地开发和 smoke
- `openai` 走 OpenAI `/embeddings`
- `openai_compatible` 走兼容的 `/embeddings`

## 5. 常用环境变量

### 5.1 总开关

```bash
set HIREMATE_RAG_ENABLE=1
set HIREMATE_RAG_ENABLE_JD_ALIGNMENT=1
set HIREMATE_RAG_ENABLE_EVIDENCE_GROUNDING=1
set HIREMATE_RAG_ENABLE_AI_REVIEWER_GROUNDING=1
```

### 5.2 embedding provider

OpenAI：

```bash
set HIREMATE_RAG_EMBEDDING_PROVIDER=openai
set HIREMATE_RAG_EMBEDDING_MODEL=text-embedding-3-small
set HIREMATE_RAG_EMBEDDING_API_KEY_MODE=env_name
set HIREMATE_RAG_EMBEDDING_API_KEY_ENV_NAME=OPENAI_API_KEY
```

OpenAI-compatible：

```bash
set HIREMATE_RAG_EMBEDDING_PROVIDER=openai_compatible
set HIREMATE_RAG_EMBEDDING_MODEL=text-embedding-3-small
set HIREMATE_RAG_EMBEDDING_API_BASE=https://your-endpoint/v1
set HIREMATE_RAG_EMBEDDING_API_KEY_MODE=env_name
set HIREMATE_RAG_EMBEDDING_API_KEY_ENV_NAME=OPENAI_API_KEY
```

直填 key 调试：

```bash
set HIREMATE_RAG_EMBEDDING_API_KEY_MODE=direct_input
set HIREMATE_RAG_EMBEDDING_API_KEY=sk-xxxx
```

### 5.3 rerank / 自动增量索引

```bash
set HIREMATE_RAG_AUTO_INDEX_RUNTIME_CONTEXT=1
set HIREMATE_RAG_RERANK_ENABLE=1
set HIREMATE_RAG_TOP_K_JD_ALIGNMENT=4
set HIREMATE_RAG_TOP_K_EVIDENCE_GROUNDING=4
set HIREMATE_RAG_TOP_K_AI_REVIEWER_GROUNDING=4
```

## 6. 常用操作命令

### 6.1 建样本索引

```bash
uv run python scripts/rag_build_sample_index.py
```

适用场景：
- 刚拉代码
- 先验证 RAG 链路是否正常
- benchmark 前先准备基础样本库

### 6.2 跑最小 smoke

```bash
uv run python scripts/rag_smoke_test.py
```

输出会包含：
- chunk 总数
- source_type 分布
- top-k 检索结果
- metadata 完整性

### 6.3 建历史语料索引

```bash
uv run python scripts/rag_index_historical_corpus.py
```

常用参数：

```bash
uv run python scripts/rag_index_historical_corpus.py --reset
uv run python scripts/rag_index_historical_corpus.py --review-limit 200
uv run python scripts/rag_index_historical_corpus.py --batch-limit-per-jd 3 --candidate-limit-per-batch 50
```

说明：
- 会从现有 `reviews` 和 `candidate_batches / candidate_rows` 构建历史语料
- 如果本地库没有历史 review / batch，会友好返回“Nothing indexed”

### 6.4 给当前批次 / 当前候选人做增量索引

整批：

```bash
uv run python scripts/rag_incremental_index.py --batch-id <BATCH_ID>
```

单候选人：

```bash
uv run python scripts/rag_incremental_index.py --batch-id <BATCH_ID> --candidate-id <CANDIDATE_ID>
```

说明：
- 这适合当前批次刚跑完、你想让 grounding 立刻受益
- 运行时 `evidence_grounding / ai_reviewer_grounding` 也会自动做轻量 runtime context 增量索引

### 6.5 跑正式 benchmark

```bash
uv run python scripts/rag_benchmark.py
```

自定义 benchmark case：

```bash
uv run python scripts/rag_benchmark.py --cases data/rag_benchmark_samples.jsonl
```

## 7. 当前 RAG 如何自动生效

### 7.1 JD 对齐

- [src/jd_parser.py](d:/HireMate/src/jd_parser.py) 在 `parse_jd(...)` 末尾接入了 `expand_jd_with_rag(...)`
- 会补：
- `required_skill_aliases_map`
- `bonus_skill_aliases_map`
- `expanded_required_skills`
- `expanded_bonus_skills`
- `rag_alignment`

### 7.2 evidence grounding

- [src/screener.py](d:/HireMate/src/screener.py) 的 `collect_evidence_snippets(...)` 会调用 `build_evidence_grounding(...)`
- grounding 结果会增强：
- JD 命中词
- 方法词
- 结果词
- 并结合 rerank 后的检索结果帮助片段排序

### 7.3 AI reviewer grounding

- [src/ai_reviewer.py](d:/HireMate/src/ai_reviewer.py) 的 `build_ai_reviewer_prompt(...)` 会调用 `build_ai_reviewer_grounding(...)`
- 返回的 `rag_grounding` 只作为 prompt 背景，不改 reviewer 输出协议

## 8. 如何做“微调优化”

这里的“微调优化”优先指 RAG 检索调优，不是直接去做大模型 fine-tuning。

最推荐的优化顺序：

### 8.1 先补 benchmark，不要先拍脑袋改规则

建议流程：
1. 固定一批 benchmark case
2. 跑一次当前结果
3. 只改一个因素
4. 再跑 benchmark 比较

优先观察：
- top-k 里是否出现正确 source_type
- top-1 是否更稳定
- evidence grounding 是否更聚焦方法/结果/JD 命中
- AI reviewer grounding 是否减少无关片段

### 8.2 先调 rerank，再调 provider

当前 rerank 在 [src/rag/retriever.py](d:/HireMate/src/rag/retriever.py)。

优先可调项：
- `semantic_weight`
- `lexical_weight`
- `skill_weight`
- `source_type_weight`
- `dedupe_by_text`

经验建议：
- 如果召回很多，但排序不准：提高 `lexical_weight / skill_weight`
- 如果语义漂移太大：降低 `semantic_weight`
- 如果重复片段太多：保持 `dedupe_by_text=True`
- 如果 evidence 片段不够靠前：适度提高 evidence/resume_fragment 的 source bonus 逻辑

### 8.3 调 top-k，不要一味放大

建议范围：
- `jd_alignment`: 3~6
- `evidence_grounding`: 3~5
- `ai_reviewer_grounding`: 3~6

经验：
- `top_k` 太小容易漏召回
- `top_k` 太大容易把噪声也带进 prompt 和 evidence 选择

### 8.4 调 alias / 同义词，不要一上来改 chunk 粒度

当前同义词聚类在 [src/rag/retriever.py](d:/HireMate/src/rag/retriever.py) 的 `_ALIGNMENT_SYNONYM_GROUPS`。

适合扩充的方向：
- 岗位缩写
- 中英混写
- 工具别名
- 技能同义表达

例子：
- `A/B测试 / AB测试`
- `大模型 / LLM`
- `提示词 / Prompt`
- `需求文档 / PRD`

### 8.5 再调 chunk 规则

当前 chunk 规则在 [src/rag/chunker.py](d:/HireMate/src/rag/chunker.py)。

优先调这些：
- 是否需要把某类 evidence 单独成块
- 是否需要新增更细的 rubric chunk
- 是否需要对项目/实习片段做更细标签

不建议一开始就把 chunk 切得很碎：
- 太碎会让语义丢失
- 太碎也会让向量库噪声变多

### 8.6 最后再考虑 embedding provider / model

只有在这些情况才优先换模型：
- 同义召回长期不稳定
- rerank 已调过仍然不准
- benchmark 明显卡在 embedding 表达能力上

否则更推荐先把：
- benchmark
- alias
- rerank
- chunk
调顺

## 9. 推荐调优闭环

建议你以后每次优化都按这个闭环做：

1. 先建样本索引  
   `uv run python scripts/rag_build_sample_index.py`
2. 再建历史语料索引  
   `uv run python scripts/rag_index_historical_corpus.py`
3. 跑 benchmark  
   `uv run python scripts/rag_benchmark.py`
4. 只改一项  
   例如 rerank 权重、alias 词表、chunk 规则
5. 再跑 benchmark
6. 记录：
- 哪个 case 提升了
- 哪个 case 退化了
- 是否影响 evidence grounding / AI reviewer grounding

## 10. 当前已知边界

- 历史语料索引依赖本地已有 `reviews / candidate_batches`
- 如果数据库里没有历史数据，脚本会正常返回，但不会生成历史索引
- 当前真实 embedding provider 只实现了 `openai / openai_compatible`
- 这套优化是 RAG 检索调优，不等于直接训练新的模型

## 11. 建议你下一步怎么用

最推荐的顺序：
1. 先运行样本索引和 benchmark，拿到基线
2. 再运行历史语料索引，把真实 review / batch 语料加入向量库
3. 然后用当前真实批次跑一次增量索引
4. 最后开始小步调 rerank 和 alias

第一轮最值得先调的是：
- `src/rag/retriever.py` 里的 rerank 权重
- `src/rag/retriever.py` 里的 `_ALIGNMENT_SYNONYM_GROUPS`
- `src/rag/chunker.py` 里 evidence / rubric 的切块粒度

## 12. 本轮新增：真实历史 benchmark 扩充与 rerank 首轮调参

### 12.1 从历史 review / batch 扩充 benchmark

如果你的库里已经有真实历史数据，先执行：

```bash
uv run python scripts/rag_expand_benchmark_from_history.py --output data/rag_benchmark_historical.jsonl
```

常用限制参数：

```bash
uv run python scripts/rag_expand_benchmark_from_history.py --review-limit 300
uv run python scripts/rag_expand_benchmark_from_history.py --batch-limit-per-jd 5 --candidate-limit-per-batch 50
uv run python scripts/rag_expand_benchmark_from_history.py --append-base-cases --output data/rag_benchmark_full.jsonl
```

说明：
- 这一步会尽量从真实 `reviews` 和 `candidate_batches` 里抽出更贴近业务的 JD / evidence / AI reviewer case
- 对于只有岗位名、没有有效证据的候选样本，脚本会尽量跳过，避免把低信息量 case 混进 benchmark
- 如果当前库没有历史数据，脚本会稳定返回，不会报错

### 12.2 合并 benchmark 跑回归

```bash
uv run python scripts/rag_benchmark.py --cases data/rag_benchmark_samples.jsonl data/rag_benchmark_historical.jsonl
```

建议至少同时看这几个指标：
- `pass_rate`
- `mean_combined_score`
- `mean_source_rank`
- `mean_substring_rank`

如果 `pass_rate` 没变，但 `mean_substring_rank` 提升，通常说明排序更贴近证据语义了。

### 12.3 自动 sweep rerank 权重

```bash
uv run python scripts/rag_tune_rerank.py --cases data/rag_benchmark_samples.jsonl data/rag_benchmark_historical.jsonl --top-n 10
```

如果你想把结果存档：

```bash
uv run python scripts/rag_tune_rerank.py --cases data/rag_benchmark_samples.jsonl data/rag_benchmark_historical.jsonl --report-out data/rag_rerank_tuning_report.json
```

### 12.4 当前首轮推荐默认值

这轮已经把默认 rerank 权重收口为：

```bash
semantic_weight=0.48
lexical_weight=0.14
skill_weight=0.22
source_type_weight=0.16
dedupe_by_text=True
```

这组值的含义是：
- 语义相似度仍然是主干，但不再压得过重
- 技能命中和 source_type 优先级被适度抬高
- 对 evidence grounding / AI reviewer grounding 更友好

### 12.5 现在可以直接用环境变量继续微调

不用改代码，直接在环境里覆盖：

```bash
set HIREMATE_RAG_RERANK_ENABLE=1
set HIREMATE_RAG_RERANK_SEMANTIC_WEIGHT=0.48
set HIREMATE_RAG_RERANK_LEXICAL_WEIGHT=0.14
set HIREMATE_RAG_RERANK_SKILL_WEIGHT=0.22
set HIREMATE_RAG_RERANK_SOURCE_TYPE_WEIGHT=0.16
set HIREMATE_RAG_RERANK_DEDUPE_BY_TEXT=1
```

推荐的服务器调优闭环：
1. 先建历史索引
2. 再扩 benchmark
3. 跑一次 benchmark 拿基线
4. sweep rerank 权重
5. 只保留能同时改善 `pass_rate` 或 `mean_combined_score` 的配置
6. 再把最终配置写进环境变量或部署文件
