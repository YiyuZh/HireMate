from __future__ import annotations

import argparse
import hmac
import os
from pathlib import Path


REQUIRED_CADDY_ENV_KEYS = (
    "CADDY_ACME_EMAIL",
    "PRIMARY_DOMAIN",
    "HIREMATE_DOMAIN",
    "INTERVIEW_DOMAIN",
    "ADMIN_DOMAIN",
    "PORTAL_GATEWAY_TOKEN",
)

SECRET_ENV_KEYS = {"PORTAL_GATEWAY_TOKEN"}

PORTAL_GATEWAY_HEADER_SET = "header_up X-Portal-Gateway-Token {$PORTAL_GATEWAY_TOKEN}"
PORTAL_GATEWAY_HEADER_DELETE = "header_up -X-Portal-Gateway-Token"


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


def _is_unsafe_gateway_token(value: str | None) -> bool:
    if _is_placeholder(value):
        return True
    stripped = (value or "").strip()
    lowered = stripped.lower()
    return len(stripped) < 32 or len(set(stripped)) < 16 or any(
        marker in lowered
        for marker in ("replace-with", "change-me", "changeme", "example", "your-token")
    )


def _portal_gateway_header_error(caddyfile_text: str) -> str | None:
    if PORTAL_GATEWAY_HEADER_DELETE in caddyfile_text:
        return (
            "Caddyfile deletes X-Portal-Gateway-Token after setting it upstream. "
            "Remove the header_up delete line; the set operation already overwrites client input."
        )
    if PORTAL_GATEWAY_HEADER_SET not in caddyfile_text:
        return "Caddyfile does not inject PORTAL_GATEWAY_TOKEN into the Portal upstream request."
    return None


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate the HireMate Caddy mount and runtime environment.")
    parser.add_argument(
        "--portal-env",
        type=Path,
        help="Optional deployed gateway-portal/portal .env; validates that both projects use the same token.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    project_root = Path(__file__).resolve().parents[1]
    caddyfile_path = project_root / "Caddyfile"
    compose_path = project_root / "docker-compose.yml"
    dotenv_path = project_root / ".env"
    dotenv_values = _parse_dotenv(dotenv_path)
    portal_env_path = args.portal_env.resolve() if args.portal_env else None

    print("HireMate Caddy mount check")
    print(f"project_root: {project_root}")
    print(f"compose_path: {compose_path}")
    print(f"caddyfile_path: {caddyfile_path}")
    print(f"dotenv_path: {dotenv_path}")
    if portal_env_path:
        print(f"portal_env_path: {portal_env_path}")
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

    caddyfile_text = caddyfile_path.read_text(encoding="utf-8")
    gateway_header_error = _portal_gateway_header_error(caddyfile_text)
    if gateway_header_error:
        print(f"ERROR: {gateway_header_error}")
        return 1
    print("OK: Portal gateway header is overwritten with the trusted Caddy token and is not deleted.")
    print()
    print("Caddy runtime env check:")

    env_errors = 0
    for key in REQUIRED_CADDY_ENV_KEYS:
        value = os.getenv(key) or dotenv_values.get(key)
        invalid = _is_unsafe_gateway_token(value) if key == "PORTAL_GATEWAY_TOKEN" else _is_placeholder(value)
        if invalid:
            print(f"  ERROR: {key} is missing, still a placeholder, or does not meet the security requirement.")
            env_errors += 1
            continue
        if key in SECRET_ENV_KEYS:
            print(f"  OK: {key}=<redacted> ({len(value or '')} characters)")
        else:
            print(f"  OK: {key}={value}")

    if portal_env_path:
        portal_values = _parse_dotenv(portal_env_path)
        hiremate_token = os.getenv("PORTAL_GATEWAY_TOKEN") or dotenv_values.get("PORTAL_GATEWAY_TOKEN")
        portal_token = portal_values.get("PORTAL_GATEWAY_TOKEN")
        if not portal_env_path.is_file():
            print(f"  ERROR: Portal env file does not exist: {portal_env_path}")
            env_errors += 1
        elif _is_unsafe_gateway_token(portal_token):
            print("  ERROR: Portal PORTAL_GATEWAY_TOKEN is missing, a placeholder, or unsafe.")
            env_errors += 1
        elif not hiremate_token or not hmac.compare_digest(hiremate_token, portal_token or ""):
            print("  ERROR: HireMate and Portal PORTAL_GATEWAY_TOKEN values do not match.")
            env_errors += 1
        else:
            print("  OK: HireMate and Portal gateway tokens match (values redacted).")

    if env_errors:
        print()
        print("Fix on server:")
        print("  1. cd /opt/apps/hiremate")
        print("  2. nano .env")
        print("  3. Set real values for:")
        for key in REQUIRED_CADDY_ENV_KEYS:
            print(f"     - {key}")
        print("  4. PORTAL_GATEWAY_TOKEN must exactly match gateway-portal/portal/.env")
        print("  5. Re-run this script with --portal-env /actual/portal/.env")
        print("  6. docker compose config --quiet")
        print("  7. docker compose up -d --no-deps --force-recreate caddy")
        return 1

    print("Recommended next commands:")
    print("  docker compose config")
    print("  docker compose up -d --build")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
