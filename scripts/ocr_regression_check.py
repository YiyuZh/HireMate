from __future__ import annotations

import argparse
from io import BytesIO
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.resume_loader import check_ocr_capabilities, load_resume_file


SUPPORTED_EXTENSIONS = {".txt", ".docx", ".pdf", ".png", ".jpg", ".jpeg"}
OCR_LIKE_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}


def _clip(text: str, width: int) -> str:
    raw = str(text or "")
    if len(raw) <= width:
        return raw
    return raw[: max(0, width - 1)] + "…"


def _flag(value: bool | None) -> str:
    if value is None:
        return "-"
    return "Y" if bool(value) else "N"


def _iter_sample_files(path: Path, *, recursive: bool) -> list[Path]:
    if path.is_file():
        return [path] if path.suffix.lower() in SUPPORTED_EXTENSIONS else []
    pattern = "**/*" if recursive else "*"
    return sorted(
        [
            item
            for item in path.glob(pattern)
            if item.is_file() and item.suffix.lower() in SUPPORTED_EXTENSIONS
        ],
        key=lambda item: (item.suffix.lower(), item.name.lower()),
    )


def _fake_upload(path: Path) -> BytesIO:
    handle = BytesIO(path.read_bytes())
    handle.name = path.name
    return handle


def _print_capability_snapshot() -> None:
    capability = check_ocr_capabilities()
    missing_deps = ",".join(capability.get("missing_deps") or []) or "-"
    missing_runtime = ",".join(capability.get("missing_runtime") or []) or "-"
    print(
        "OCR capability:",
        f"image={_flag(capability.get('image_ocr_available'))}",
        f"pdf={_flag(capability.get('pdf_ocr_available'))}",
        f"missing_deps={missing_deps}",
        f"missing_runtime={missing_runtime}",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run OCR / resume parsing regression checks on a sample directory.",
    )
    parser.add_argument(
        "sample_path",
        help="Directory or single file containing txt/docx/pdf/png/jpg/jpeg resume samples.",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Recursively scan subdirectories under sample_path.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print parser message for every processed file.",
    )
    args = parser.parse_args()

    sample_path = Path(args.sample_path).expanduser().resolve()
    if not sample_path.exists():
        print(f"[ERROR] sample path not found: {sample_path}")
        return 1

    files = _iter_sample_files(sample_path, recursive=bool(args.recursive))
    if not files:
        print(f"[ERROR] no supported resume samples found under: {sample_path}")
        return 1

    _print_capability_snapshot()
    print(f"Samples: {len(files)} | root={sample_path}")
    print(
        f"{'file':<34} {'method':<8} {'quality':<7} {'len':>6} "
        f"{'edu':<4} {'exp':<4} {'skill':<5} {'time':<5} {'ocr':<4} {'fb_ok':<5}"
    )
    print("-" * 96)

    ok_count = 0
    weak_count = 0
    fail_count = 0
    processed_count = 0
    ocr_used_count = 0

    for file_path in files:
        relative_name = str(file_path.relative_to(sample_path)) if sample_path.is_dir() else file_path.name
        try:
            result = load_resume_file(_fake_upload(file_path))
        except Exception as exc:  # noqa: BLE001
            fail_count += 1
            print(
                f"{_clip(relative_name, 34):<34} {'error':<8} {'fail':<7} {0:>6} "
                f"{'-':<4} {'-':<4} {'-':<5} {'-':<5} {'-':<4} {'-':<5}"
            )
            print(f"  note: {exc}")
            continue

        processed_count += 1
        quality = str(result.get("quality") or "weak").lower()
        if quality == "ok":
            ok_count += 1
        else:
            weak_count += 1

        analysis = result.get("quality_analysis") if isinstance(result.get("quality_analysis"), dict) else {}
        source_type = str(result.get("source_type") or file_path.suffix.lower().lstrip("."))
        is_ocr_like = file_path.suffix.lower() in OCR_LIKE_EXTENSIONS or source_type in {"pdf", "image"}
        used_ocr = bool(result.get("used_ocr"))
        if used_ocr:
            ocr_used_count += 1

        fallback_success: bool | None = None
        if file_path.suffix.lower() == ".pdf" or source_type == "pdf":
            fallback_success = bool(result.get("ocr_fallback_succeeded"))

        print(
            f"{_clip(relative_name, 34):<34} "
            f"{_clip(str(result.get('method') or '-'), 8):<8} "
            f"{_clip(quality, 7):<7} "
            f"{int(analysis.get('length') or 0):>6} "
            f"{_flag(bool(analysis.get('education_keyword_hit'))):<4} "
            f"{_flag(bool(analysis.get('experience_keyword_hit'))):<4} "
            f"{_flag(bool(analysis.get('skill_keyword_hit'))):<5} "
            f"{_flag(bool(analysis.get('has_time_pattern'))):<5} "
            f"{_flag(used_ocr if is_ocr_like else None):<4} "
            f"{_flag(fallback_success):<5}"
        )

        if args.verbose or quality != "ok" or is_ocr_like:
            print(f"  note: {result.get('message') or '-'}")

    ocr_ratio = (ocr_used_count / processed_count * 100.0) if processed_count else 0.0
    print("-" * 96)
    print(
        "Summary:",
        f"ok={ok_count}",
        f"weak={weak_count}",
        f"failed={fail_count}",
        f"ocr_used={ocr_used_count}/{processed_count} ({ocr_ratio:.1f}%)",
    )
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
