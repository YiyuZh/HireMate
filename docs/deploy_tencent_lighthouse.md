# HireMate 云服务器部署说明

适用场景：
- 腾讯云轻量服务器
- 已安装 Docker / Docker Compose
- 已放通 80 端口
- 当前先用公网 IP 访问 HTTP

如果你后续已经有域名，再补 Caddy 的 HTTPS 配置即可。

## 1. 上传项目

把整个 `HireMate` 目录上传到服务器，例如：

```bash
/root/HireMate
```

重点要保留：
- `app.py`
- `src/`
- `Dockerfile`
- `docker-compose.yml`
- `Caddyfile`

如果你本地已经有旧数据，分两种情况：
- 如果已经是 SQLite：优先保留 `data/hiremate.db`
- 如果还是旧 JSON：可选上传下面这些迁移源文件
- `data/jd_store.json`
- `data/candidate_pool_store.json`
- `data/review_history.json`

SQLite 会作为运行时唯一主存储；旧 JSON 只用于第一次启动时自动迁移到 `/app/data/hiremate.db`。

## 2. 进入项目目录

```bash
cd /root/HireMate
```

## 3. 可选：准备环境变量

如果你暂时不用 AI reviewer / AI 评分细则优化，这一步可以跳过。

如果你要预留：

```bash
cp .env.example .env
vi .env
```

说明：
- 当前 `docker-compose.yml` 已经支持“没有 `.env` 也能正常启动”
- 所以 `.env` 不是硬依赖

## 4. 启动服务

```bash
docker compose up -d --build
```

首次构建会做这些事：
- 安装 Python 依赖
- 安装 OCR 相关系统依赖
- 启动 Streamlit
- 启动 Caddy 反代

## 5. 查看运行状态

```bash
docker compose ps
docker compose logs -f hiremate
docker compose logs -f caddy
```

你应该重点确认：
- `hiremate` 容器是 `Up`
- `caddy` 容器是 `Up`
- Streamlit 没有报依赖错误
- 没有数据库权限错误

## 6. 访问地址

当前默认访问：

```text
http://你的公网IP
```

注意：
- 现在仓库内的 `Caddyfile` 是 `:80`
- 所以默认不是 HTTPS
- 如果你还没绑域名，不要期待 `https://公网IP` 正常签证书

## 7. SQLite 数据位置

当前数据会落在：

```text
/app/data/hiremate.db
```

Docker Compose 当前使用 named volume 持久化：

```text
hiremate_data -> /app/data
```

所以容器重建后数据还在，不依赖项目目录下的 `./data` bind mount。

## 8. 首次启动后的检查项

建议你启动后做这几步：

1. 打开岗位配置页，确认历史岗位还在
2. 进入容器确认 `/app/data/hiremate.db` 是否已经生成
3. 新建一个测试岗位
4. 上传 1 份 txt 简历跑一次批量初筛
5. 进入候选人工作台，确认候选池、人工备注、人工决策能保存

## 9. 后续启用 HTTPS

当你有域名后：

1. 把域名 A 记录指向这台服务器
2. 把 `Caddyfile` 改成：

```text
your-domain.com {
    encode gzip
    reverse_proxy hiremate:8501
}
```

3. 重启：

```bash
docker compose up -d --build
```

之后 Caddy 才会自动申请 HTTPS 证书。

## 10. 常见问题

### 1. 容器起不来

先看：

```bash
docker compose logs -f hiremate
```

常见原因：
- 依赖安装失败
- 服务器磁盘空间不足
- `data/` 目录权限异常

### 2. 图片 OCR 报错

当前 Dockerfile 已安装：
- `tesseract-ocr`
- `tesseract-ocr-chi-sim`
- `poppler-utils`

如果仍然有问题，优先检查上传文件本身是否清晰。

### 3. 想备份数据

直接备份 named volume 对应的数据目录，或先进入容器导出 `/app/data` 下的数据库文件。重点是：

```text
/app/data/hiremate.db
/app/data/hiremate.db-shm
/app/data/hiremate.db-wal
```

更稳妥的方式是先停服务再备份：

```bash
docker compose down
tar -czvf hiremate-data-backup.tar.gz data
```
