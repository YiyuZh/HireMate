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

首次部署前先从 `.env.example` 创建 `.env`，并运行网关预检：

```bash
cp .env.example .env
nano .env
python scripts/check_caddy_mount.py
```

`PORTAL_GATEWAY_TOKEN` 必须与实际 Portal 部署仓库的 `.env` 完全一致。
本地源码事实目录是 `D:/apps/gateway-portal/portal`；服务器可能部署为
`/opt/apps/gateway-portal/portal`，历史机器也可能仍使用
`/opt/apps/portal`，必须先按实际文件确定。`.env` 不受 Git 管理，所以
`git pull` 新增环境变量后必须人工合并，不能把 `.env.example` 中的
示例值用于生产。

Compose 的 `config`、`ps` 和 `down` 在缺少新变量时仍可执行；真正启动
Caddy 时会再次校验全部必需变量并安全失败，避免使用空 token 提供流量。

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
git clone https://github.com/YiyuZh/HireMate.git /opt/apps/hiremate
cd /opt/apps/hiremate
cp .env.example .env
nano .env
python scripts/check_caddy_mount.py
docker compose up -d --build
```

后续：

```bash
cd /opt/apps/hiremate
git pull --ff-only
python scripts/check_caddy_mount.py
docker compose up -d --build
```

从旧版本升级并首次看到 `PORTAL_GATEWAY_TOKEN is missing` 时，不需要先
执行 `docker compose down`。先确认服务器上的实际 Portal 目录：

```bash
if [ -f /opt/apps/gateway-portal/portal/docker-compose.yml ]; then
  PORTAL_DIR=/opt/apps/gateway-portal/portal
elif [ -f /opt/apps/portal/docker-compose.yml ]; then
  PORTAL_DIR=/opt/apps/portal
else
  echo "找不到 Portal docker-compose.yml" >&2
  exit 1
fi
printf 'Portal directory: %s\n' "$PORTAL_DIR"
```

把同一个随机 token 写入 `/opt/apps/hiremate/.env` 和
`$PORTAL_DIR/.env`，然后先重建 Portal API，再重建 Caddy：

```bash
cd "$PORTAL_DIR"
docker compose up -d --no-deps --force-recreate messages-api

cd /opt/apps/hiremate
python scripts/check_caddy_mount.py --portal-env "$PORTAL_DIR/.env"
docker compose config --quiet
docker compose up -d --no-deps --force-recreate caddy
```

## 常用排查

查看应用日志：

```bash
docker compose logs -f hiremate-api
```

查看反代日志：

```bash
docker compose logs -f caddy
```

查看数据库文件：

```bash
docker compose exec hiremate-api ls -lah /app/data
```
