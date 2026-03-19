# HireMate（Python + Streamlit）

一个面向大学生作品集的轻量项目脚手架。

## 项目目标
输入：岗位 JD + 单份简历  
输出：初筛结论、多维评分、推荐理由、风险点、面试建议。

## 技术选型
- Python
- Streamlit
- 本地 JSON 文件读写
- 预留大模型 API 接入能力（`.env` + `prompts.py`）

## 目录结构

```text
ai_recruiting_screener/
├─ app.py                  # Streamlit 入口
├─ requirements.txt        # 最小依赖
├─ .env.example            # 环境变量模板（API 预留）
├─ README.md
├─ data/                   # 本地 JSON 结果输出目录
└─ src/
   ├─ __init__.py
   ├─ jd_parser.py         # JD 解析
   ├─ resume_parser.py     # 简历解析
   ├─ screener.py          # 初筛主流程编排
   ├─ scorer.py            # 规则评分
   ├─ risk_analyzer.py     # 风险点分析
   ├─ interviewer.py       # 面试建议生成
   ├─ prompts.py           # LLM 提示词模板
   └─ utils.py             # 通用工具（JSON / env）
```

## 快速开始

```bash
cd ai_recruiting_screener
python -m venv .venv
source .venv/bin/activate  # Windows 用 .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
streamlit run app.py
```

## 当前版本说明（V1）
- 评分与结论为“规则打底”示例实现，便于理解和迭代。
- 模型能力尚未接入，仅预留了 `prompts.py` 与 `.env` 配置位。
- 每次筛选会在 `data/` 下保存一份 JSON 结果，方便演示与调试。

## 后续扩展建议
1. 将 `scoring_rules.md` 配置化，支持动态权重与阈值。
2. 接入 LLM API，让推荐理由/风险点更贴合简历上下文。
3. 增加输入输出样例与简单单元测试，提升可维护性。
