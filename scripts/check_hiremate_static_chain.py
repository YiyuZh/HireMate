from __future__ import annotations

import argparse
import re
from collections import deque
from dataclasses import dataclass
from html import unescape
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen


STATIC_REF_RE = re.compile(r"/static/[^\"'()<>\s]+")


@dataclass
class FetchResult:
    url: str
    status: int | None
    content_type: str
    cache_control: str
    body: str
    error: str | None = None


def fetch_text(url: str, timeout: float) -> FetchResult:
    request = Request(
        url,
        headers={
            "User-Agent": "hiremate-static-check/1.0",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            body = response.read().decode(charset, errors="replace")
            return FetchResult(
                url=url,
                status=response.status,
                content_type=response.headers.get("Content-Type", ""),
                cache_control=response.headers.get("Cache-Control", ""),
                body=body,
            )
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return FetchResult(
            url=url,
            status=exc.code,
            content_type=exc.headers.get("Content-Type", ""),
            cache_control=exc.headers.get("Cache-Control", ""),
            body=body,
            error=f"HTTP {exc.code}",
        )
    except URLError as exc:
        return FetchResult(
            url=url,
            status=None,
            content_type="",
            cache_control="",
            body="",
            error=str(exc.reason),
        )


def extract_static_urls(text: str, base_url: str) -> list[str]:
    refs = sorted(set(unescape(match) for match in STATIC_REF_RE.findall(text)))
    return [urljoin(base_url, ref) for ref in refs]


def is_text_asset(content_type: str, url: str) -> bool:
    lowered = content_type.lower()
    return (
        "javascript" in lowered
        or "json" in lowered
        or "text/" in lowered
        or url.endswith(".js")
        or url.endswith(".css")
        or url.endswith(".html")
    )


def crawl_static_graph(entry_url: str, timeout: float, limit: int) -> tuple[FetchResult, list[FetchResult]]:
    root = fetch_text(entry_url, timeout)
    queue: deque[str] = deque(extract_static_urls(root.body, entry_url))
    seen: set[str] = set(queue)
    assets: list[FetchResult] = []

    while queue and len(assets) < limit:
        url = queue.popleft()
        result = fetch_text(url, timeout)
        assets.append(result)

        if result.error or not is_text_asset(result.content_type, url):
            continue

        for child_url in extract_static_urls(result.body, url):
            if child_url in seen:
                continue
            seen.add(child_url)
            queue.append(child_url)

    return root, assets


def print_result(label: str, result: FetchResult) -> None:
    status = result.status if result.status is not None else "ERR"
    cache = result.cache_control or "<missing>"
    print(f"{label}: {status}  {result.url}")
    print(f"  content-type: {result.content_type or '<missing>'}")
    print(f"  cache-control: {cache}")
    if result.error:
        print(f"  error: {result.error}")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check HireMate HTML/static asset consistency.")
    parser.add_argument("--url", required=True, help="Base page URL, for example https://hiremate.example.com")
    parser.add_argument("--timeout", type=float, default=10.0, help="HTTP timeout seconds")
    parser.add_argument("--limit", type=int, default=80, help="Maximum number of static assets to crawl")
    args = parser.parse_args(list(argv) if argv is not None else None)

    entry_url = args.url.rstrip("/") + "/"
    parsed = urlparse(entry_url)
    if not parsed.scheme or not parsed.netloc:
        print("ERROR: --url must include scheme and host, for example https://hiremate.example.com")
        return 2

    root, assets = crawl_static_graph(entry_url, args.timeout, args.limit)
    print_result("ENTRY", root)

    static_errors = 0
    static_ok = 0
    immutable_assets = 0
    no_store_entry = "no-store" in (root.cache_control or "").lower()
    service_worker_detected = "/service-worker" in root.body or "navigator.serviceWorker" in root.body

    print()
    print(f"Discovered static asset refs: {len(assets)}")

    for asset in assets:
        print_result("ASSET", asset)
        if asset.error or asset.status != 200:
            static_errors += 1
        else:
            static_ok += 1
            if "immutable" in (asset.cache_control or "").lower():
                immutable_assets += 1

    print()
    print("Summary:")
    print(f"  entry_no_store: {no_store_entry}")
    print(f"  static_ok: {static_ok}")
    print(f"  static_errors: {static_errors}")
    print(f"  immutable_assets: {immutable_assets}")
    print(f"  service_worker_detected: {service_worker_detected}")

    if root.error or root.status != 200:
        return 1
    if static_errors:
        return 1
    if service_worker_detected:
        print("WARNING: service worker markers detected; clear browser SW cache before ruling out client caching.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
