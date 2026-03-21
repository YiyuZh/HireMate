# HireMate

HireMate 是一个面向 HR / 招聘经理的 AI 招聘初筛与候选人审核工作台。

本仓库当前采用：
- Python + Streamlit
- SQLite 单库部署
- Docker + Caddy
- 规则评分器为主评分器

## 当前存储结构

- 统一数据库入口：`src/db.py`
- SQLite 默认路径：`/app/data/hiremate.db`
- 旧 JSON 只保留迁移用途：`src/legacy_json_compat.py`
- 一次性迁移脚本：`scripts/migrate_json_to_sqlite.py`

当前主存储模块：
- `src/jd_store.py`
- `src/candidate_store.py`
- `src/review_store.py`

## 本地启动

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

说明：
- `python-dotenv` 缺失时应用仍可启动
- `.env` 仅用于 AI 相关预留配置，不是启动硬依赖
- 默认不会自动执行 legacy JSON 迁移；如需迁移，请手工运行 `python scripts/migrate_json_to_sqlite.py`
- 如需本地覆盖数据库位置，可手动设置 `HIREMATE_DB_PATH`

## Docker 部署

构建并启动：

```bash
docker compose up -d --build
```

默认访问：

```text
http://你的公网IP
```

## 数据持久化

- 容器内数据库路径固定为：`/app/data/hiremate.db`
- `docker-compose.yml` 使用 named volume 持久化 `/app/data`
- 重建容器后数据不会丢

## 云服务器更新

首次：

```bash
git clone https://github.com/YiyuZh/HireMate.git /opt/hiremate
cd /opt/hiremate
docker compose up -d --build
```

后续：

```bash
cd /opt/hiremate
git pull --ff-only
docker compose up -d --build
```

## 常用排查

查看应用日志：

```bash
docker compose logs -f hiremate
```

查看反代日志：

```bash
docker compose logs -f caddy
```

查看数据库文件：

```bash
docker compose exec hiremate ls -lah /app/data
```
