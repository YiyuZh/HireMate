from __future__ import annotations

import os
from pathlib import Path
import sys


REQUIRED_CADDY_ENV_KEYS = (
    "CADDY_ACME_EMAIL",
    "PRIMARY_DOMAIN",
    "HIREMATE_DOMAIN",
    "INTERVIEW_DOMAIN",
    "ADMIN_DOMAIN",
)


def _parse_dotenv(env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not env_path.exists() or not env_path.is_file():
        return values

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _is_placeholder(value: str | None) -> bool:
    if not value:
        return True
    stripped = value.strip()
    return stripped.startswith("<") and stripped.endswith(">")


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    caddyfile_path = project_root / "Caddyfile"
    compose_path = project_root / "docker-compose.yml"
    dotenv_path = project_root / ".env"
    dotenv_values = _parse_dotenv(dotenv_path)

    print("HireMate Caddy mount check")
    print(f"project_root: {project_root}")
    print(f"compose_path: {compose_path}")
    print(f"caddyfile_path: {caddyfile_path}")
    print(f"dotenv_path: {dotenv_path}")
    print()

    if not compose_path.exists():
        print("ERROR: docker-compose.yml is missing.")
        return 1

    if not caddyfile_path.exists():
        print("ERROR: Caddyfile does not exist.")
        print()
        print("Fix on server:")
        print("  1. cd /opt/apps/hiremate")
        print("  2. git restore Caddyfile  # or: git checkout -- Caddyfile")
        print("  3. ls -l Caddyfile")
        return 1

    if caddyfile_path.is_dir():
        print("ERROR: Caddyfile is a directory, but docker compose expects a regular file.")
        print()
        print("Fix on server:")
        print("  1. cd /opt/apps/hiremate")
        print("  2. rm -rf Caddyfile")
        print("  3. git restore Caddyfile  # or: git checkout -- Caddyfile")
        print("  4. ls -l Caddyfile")
        return 1

    if not caddyfile_path.is_file():
        print("ERROR: Caddyfile exists, but is not a regular file.")
        return 1

    print("OK: Caddyfile exists and is a regular file.")
    print()
    print("Caddy runtime env check:")

    env_errors = 0
    for key in REQUIRED_CADDY_ENV_KEYS:
        value = os.getenv(key) or dotenv_values.get(key)
        if _is_placeholder(value):
            print(f"  ERROR: {key} is missing or still uses an <...> placeholder.")
            env_errors += 1
            continue
        print(f"  OK: {key}={value}")

    if env_errors:
        print()
        print("Fix on server:")
        print("  1. cd /opt/apps/hiremate")
        print("  2. nano .env")
        print("  3. Set real values for:")
        for key in REQUIRED_CADDY_ENV_KEYS:
            print(f"     - {key}")
        print("  4. docker compose up -d --build")
        return 1

    print("Recommended next commands:")
    print("  docker compose config")
    print("  docker compose up -d --build")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
