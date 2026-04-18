#!/usr/bin/env sh
set -eu

root_dir="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
filter_src="$root_dir/security/fail2ban/filter.d/zenithy-caddy-admin-auth.conf"
jail_src="$root_dir/security/fail2ban/jail.d/zenithy-caddy-admin-auth.local"

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run as root: sudo sh security/fail2ban/install.sh" >&2
  exit 1
fi

mkdir -p /etc/fail2ban/filter.d /etc/fail2ban/jail.d "$root_dir/logs/caddy"
touch "$root_dir/logs/caddy/access.log"
cp "$filter_src" /etc/fail2ban/filter.d/zenithy-caddy-admin-auth.conf
cp "$jail_src" /etc/fail2ban/jail.d/zenithy-caddy-admin-auth.local

# The official Caddy image commonly writes as uid 1000. Keep this non-fatal
# because some deployments run the container as root.
chown -R 1000:1000 "$root_dir/logs/caddy" 2>/dev/null || true

systemctl enable --now fail2ban >/dev/null 2>&1 || true
fail2ban-client reload
fail2ban-client status zenithy-caddy-admin-auth
