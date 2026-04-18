# Zenithy Caddy Basic Auth fail2ban

这个目录提供轻量的后台 Basic Auth 防爆破方案。保护点放在网关/服务器层，而不是 portal 静态页面里。

## 保护范围

- `https://blog.zenithy.art/admin`
- `https://blog.zenithy.art/admin/*`
- `/api/admin/messages*`

Caddy 会把访问日志写入：

```bash
/opt/apps/hiremate/logs/caddy/access.log
```

fail2ban 只匹配 `401` 响应，不影响正常登录后的后台访问。

## 启用步骤

在服务器执行：

```bash
cd /opt/apps/hiremate
git pull
mkdir -p logs/caddy
sudo chown -R 1000:1000 logs/caddy || true

docker compose up -d --force-recreate caddy
docker exec hiremate-caddy caddy validate --config /etc/caddy/Caddyfile

sudo apt update
sudo apt install -y fail2ban
sudo sh security/fail2ban/install.sh
```

## 验证

查看 Caddy 是否正在写日志：

```bash
cd /opt/apps/hiremate
tail -f logs/caddy/access.log
```

测试 fail2ban 正则：

```bash
sudo fail2ban-regex \
  /opt/apps/hiremate/logs/caddy/access.log \
  /etc/fail2ban/filter.d/zenithy-caddy-admin-auth.conf
```

查看封禁状态：

```bash
sudo fail2ban-client status zenithy-caddy-admin-auth
```

手动解封：

```bash
sudo fail2ban-client set zenithy-caddy-admin-auth unbanip <IP>
```

## 默认策略

- `10m` 内失败 `6` 次触发封禁。
- 默认封禁 `1h`。
- 只封禁 `http,https`。
- 本机地址默认忽略。

这个策略是“轻量预防”，用于挡住密码箱/脚本爆破，不替代云安全组、SSH 安全策略和系统更新。
