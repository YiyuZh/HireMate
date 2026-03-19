# HireMate

HireMate 是一个面向 HR / 招聘经理的 AI 招聘初筛与候选人审核工作台，不是单次简历打分脚本。

当前主流程：
- 岗位配置页
- 批量初筛
- 候选人工作台

当前技术栈：
- Python
- Streamlit
- SQLite
- OCR fallback（txt / pdf / docx / png / jpg / jpeg）
- Docker + Caddy

## 当前目录重点

```text
HireMate/
├─ app.py                    # Streamlit 主入口
├─ src/
│  ├─ jd_store.py            # 岗位存储（SQLite）
│  ├─ candidate_store.py     # 批次与候选池存储（SQLite）
│  ├─ review_store.py        # 审核留痕存储（SQLite）
│  ├─ sqlite_store.py        # SQLite 建库与旧 JSON 迁移
│  ├─ scorer.py              # 规则评分器
│  ├─ role_profiles.py       # 岗位模板配置
│  ├─ resume_loader.py       # 简历读取与 OCR fallback
│  └─ v2_workspace.py        # 候选人工作台辅助函数
├─ data/
│  ├─ hiremate.db            # SQLite 数据库文件
│  ├─ jd_store.json          # 旧数据，首次启动可自动迁移
│  ├─ candidate_pool_store.json
│  └─ review_history.json
├─ Dockerfile
├─ docker-compose.yml
└─ Caddyfile
```

## 数据存储说明

- 当前默认主存储为 `data/hiremate.db`
- 首次访问 store 时会自动建表
- 如果 `data/` 下仍有旧 JSON 文件，会自动做一次迁移
- Docker 运行时会把宿主机的 `./data` 挂载到容器内 `/app/data`，所以数据可持久化

## 本地启动

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
streamlit run app.py
```

说明：
- 不启用 AI reviewer / AI 评分细则优化时，`.env` 可以为空
- 如果本地没有 OCR 系统依赖，图片 / 部分 PDF 会退化为弱质量识别，但页面仍可运行

## Docker 启动

```bash
docker compose up --build -d
```

启动后默认访问：

```text
http://你的服务器IP
```

## 腾讯云轻量服务器部署建议

建议直接看文档：

- `docs/deploy_tencent_lighthouse.md`

## HTTPS 说明

当前仓库内的 `Caddyfile` 默认只监听 `80` 端口：

```text
:80 {
    encode gzip
    reverse_proxy hiremate:8501
}
```

这意味着：
- 用服务器公网 IP 访问时，默认是 `HTTP`
- 如果你要真正启用 `443 / HTTPS`，需要先绑定域名，再把 `Caddyfile` 改成你的域名站点块

## 常见排查

1. 页面打不开
- 先看容器状态：`docker compose ps`
- 再看应用日志：`docker compose logs -f hiremate`
- 再看反代日志：`docker compose logs -f caddy`

2. 图片 / PDF OCR 不稳定
- 容器内已经安装 `tesseract-ocr`、`tesseract-ocr-chi-sim`、`poppler-utils`
- 如果仍有弱质量识别，优先检查原始文件质量，必要时改为上传 txt / docx

3. 数据没了
- 检查服务器项目目录下的 `data/` 是否还在
- 重点确认 `data/hiremate.db` 是否存在
- 不要随意删除 `data/` 目录
