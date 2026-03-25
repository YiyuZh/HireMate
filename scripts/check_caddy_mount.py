from __future__ import annotations

from pathlib import Path
import sys


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    caddyfile_path = project_root / "Caddyfile"
    compose_path = project_root / "docker-compose.yml"

    print("HireMate Caddy mount check")
    print(f"project_root: {project_root}")
    print(f"compose_path: {compose_path}")
    print(f"caddyfile_path: {caddyfile_path}")
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
    print("Recommended next commands:")
    print("  docker compose config")
    print("  docker compose up -d --build")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
