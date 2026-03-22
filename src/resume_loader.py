"""Resume file loading and OCR fallback helpers."""

from __future__ import annotations

import shutil
from pathlib import Path

FALLBACK_TEXT = "[未提取到稳定文本，请人工核对后再评估]"


def _tesseract_available() -> bool:
    try:
        import pytesseract
    except ModuleNotFoundError:
        return False

    if shutil.which("tesseract") is None:
        return False

    try:
        pytesseract.get_tesseract_version()
    except Exception:
        return False
    return True


def _poppler_available() -> bool:
    return shutil.which("pdfinfo") is not None and shutil.which("pdftoppm") is not None


def _message_indicates_ocr_missing(message: str) -> bool:
    msg = (message or "").lower()
    keywords = [
        "ocr 不可用",
        "ocr unavailable",
        "tesseract",
        "poppler",
        "pdfinfo",
        "pdftoppm",
        "pytesseract",
        "pdf2image",
        "pillow",
    ]
    return any(keyword in msg for keyword in keywords)


def _derive_parse_status(quality: str, message: str, *, can_evaluate: bool) -> str:
    if _message_indicates_ocr_missing(message):
        return "OCR能力缺失"
    if not can_evaluate:
        return "弱质量识别"
    if (quality or "").lower() == "ok":
        return "正常识别"
    return "弱质量识别"


def _decode_text_bytes(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return raw.decode("gbk")
        except UnicodeDecodeError:
            return raw.decode("utf-8", errors="ignore")


def _load_txt(file_obj) -> str:
    return _decode_text_bytes(file_obj.getvalue())


def _load_pdf_text(file_obj) -> str:
    try:
        from pypdf import PdfReader
    except ModuleNotFoundError as exc:
        raise ValueError("当前环境缺少 pypdf，无法读取 PDF。") from exc

    file_obj.seek(0)
    reader = PdfReader(file_obj)
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(pages)


def _load_docx(file_obj) -> str:
    try:
        from docx import Document
    except ModuleNotFoundError as exc:
        raise ValueError("当前环境缺少 python-docx，无法读取 DOCX。") from exc

    file_obj.seek(0)
    doc = Document(file_obj)
    lines = [paragraph.text.strip() for paragraph in doc.paragraphs if paragraph.text and paragraph.text.strip()]
    return "\n".join(lines)


def _is_text_quality_weak(text: str) -> bool:
    clean = (text or "").strip()
    if len(clean) < 80:
        return True

    non_blank = [ch for ch in clean if not ch.isspace()]
    if not non_blank:
        return True

    meaningful = [ch for ch in non_blank if ch.isalnum() or "\u4e00" <= ch <= "\u9fff"]
    meaningful_ratio = len(meaningful) / len(non_blank)
    garbled_ratio = 1 - meaningful_ratio
    return garbled_ratio > 0.55


def _extract_pdf_with_ocr(file_obj) -> str:
    try:
        from pdf2image import convert_from_bytes
    except ModuleNotFoundError as exc:
        raise ValueError("PDF OCR 需要 pdf2image，但当前未安装。") from exc

    try:
        import pytesseract
    except ModuleNotFoundError as exc:
        raise ValueError("PDF OCR 需要 pytesseract，但当前未安装。") from exc

    if not _poppler_available():
        raise ValueError("PDF OCR 需要 poppler（缺少 pdfinfo/pdftoppm）。")
    if not _tesseract_available():
        raise ValueError("PDF OCR 需要 tesseract（未安装或未加入 PATH）。")

    raw = file_obj.getvalue()
    try:
        images = convert_from_bytes(raw, dpi=200)
    except Exception as exc:
        raise ValueError(f"PDF OCR 调用 poppler 失败（{exc}）。") from exc

    ocr_lines: list[str] = []
    for image in images:
        line = (pytesseract.image_to_string(image, lang="chi_sim+eng") or "").strip()
        if line:
            ocr_lines.append(line)
    return "\n".join(ocr_lines)


def _extract_image_with_ocr(file_obj) -> str:
    try:
        from PIL import Image
    except ModuleNotFoundError as exc:
        raise ValueError("图片 OCR 需要 pillow，但当前未安装。") from exc

    try:
        import pytesseract
    except ModuleNotFoundError as exc:
        raise ValueError("图片 OCR 需要 pytesseract，但当前未安装。") from exc

    if not _tesseract_available():
        raise ValueError("图片 OCR 需要 tesseract（未安装或未加入 PATH）。")

    file_obj.seek(0)
    image = Image.open(file_obj)
    return pytesseract.image_to_string(image, lang="chi_sim+eng") or ""


def _safe_result(
    text: str,
    method: str,
    quality: str,
    message: str,
    *,
    file_type: str = "",
    can_evaluate: bool = True,
    should_skip: bool = False,
) -> dict:
    clean = (text or "").strip()
    final_quality = "ok" if (quality or "").lower() == "ok" else "weak"
    final_can_evaluate = bool(can_evaluate)
    final_should_skip = bool(should_skip)

    if not clean:
        clean = FALLBACK_TEXT
        final_quality = "weak"

    parse_status = _derive_parse_status(final_quality, message, can_evaluate=final_can_evaluate)
    return {
        "text": clean,
        "method": method or "text",
        "quality": final_quality,
        "message": " ".join((message or "").split()),
        "parse_status": parse_status,
        "can_evaluate": final_can_evaluate,
        "should_skip": final_should_skip,
        "source_type": file_type,
        "ocr_missing": _message_indicates_ocr_missing(message),
    }


def _text_result(text: str, *, file_type: str, success_message: str, weak_message: str) -> dict:
    has_text = bool((text or "").strip())
    quality = "weak" if _is_text_quality_weak(text) else "ok"
    if not has_text:
        return _safe_result(
            "",
            method="text",
            quality="weak",
            message=f"{file_type.upper()} 未提取到稳定文本，当前文件不可稳定识别，建议人工处理。",
            file_type=file_type,
            can_evaluate=False,
            should_skip=True,
        )
    return _safe_result(
        text,
        method="text",
        quality=quality,
        message=success_message if quality == "ok" else weak_message,
        file_type=file_type,
        can_evaluate=True,
        should_skip=False,
    )


def _image_result_with_ocr(file_obj) -> dict:
    try:
        text = _extract_image_with_ocr(file_obj)
    except ValueError as exc:
        return _safe_result(
            "",
            method="ocr",
            quality="weak",
            message=f"图片 OCR 不可用（{exc}），当前文件不可稳定识别，建议跳过或人工处理。",
            file_type="image",
            can_evaluate=False,
            should_skip=True,
        )
    except Exception as exc:
        return _safe_result(
            "",
            method="ocr",
            quality="weak",
            message=f"图片 OCR 执行失败（{exc}），当前文件未进入稳定评估，建议人工处理。",
            file_type="image",
            can_evaluate=False,
            should_skip=True,
        )

    has_text = bool((text or "").strip())
    quality = "weak" if _is_text_quality_weak(text) else "ok"
    if not has_text:
        return _safe_result(
            "",
            method="ocr",
            quality="weak",
            message="图片 OCR 已执行，但未提取到稳定文本，建议跳过或人工处理。",
            file_type="image",
            can_evaluate=False,
            should_skip=True,
        )

    message = "图片 OCR 提取成功。" if quality == "ok" else "图片 OCR 已完成，但文本质量较弱，建议人工复核。"
    return _safe_result(
        text,
        method="ocr",
        quality=quality,
        message=message,
        file_type="image",
        can_evaluate=True,
        should_skip=False,
    )


def _pdf_result_with_fallback(file_obj) -> dict:
    pdf_text = ""
    pdf_text_error = ""
    try:
        pdf_text = _load_pdf_text(file_obj)
    except Exception as exc:
        pdf_text_error = str(exc)

    if pdf_text and not _is_text_quality_weak(pdf_text):
        return _safe_result(
            pdf_text,
            method="text",
            quality="ok",
            message="PDF 文本提取成功。",
            file_type="pdf",
            can_evaluate=True,
            should_skip=False,
        )

    prefix = f"PDF 文本提取失败（{pdf_text_error}）。" if pdf_text_error else "PDF 文本提取质量较弱。"
    try:
        file_obj.seek(0)
        ocr_text = _extract_pdf_with_ocr(file_obj)
    except ValueError as exc:
        if (pdf_text or "").strip():
            return _safe_result(
                pdf_text,
                method="text",
                quality="weak",
                message=f"{prefix} OCR 不可用（{exc}），仍可继续初筛，但建议人工复核。",
                file_type="pdf",
                can_evaluate=True,
                should_skip=False,
            )
        return _safe_result(
            "",
            method="ocr",
            quality="weak",
            message=f"{prefix} OCR 不可用（{exc}），当前文件不可稳定识别，建议跳过或人工处理。",
            file_type="pdf",
            can_evaluate=False,
            should_skip=True,
        )
    except Exception as exc:
        if (pdf_text or "").strip():
            return _safe_result(
                pdf_text,
                method="text",
                quality="weak",
                message=f"{prefix} OCR 执行失败（{exc}），仍可继续初筛，但建议人工复核。",
                file_type="pdf",
                can_evaluate=True,
                should_skip=False,
            )
        return _safe_result(
            "",
            method="ocr",
            quality="weak",
            message=f"{prefix} OCR 执行失败（{exc}），当前文件未进入稳定评估，建议人工处理。",
            file_type="pdf",
            can_evaluate=False,
            should_skip=True,
        )

    has_text = bool((ocr_text or "").strip())
    ocr_quality = "weak" if _is_text_quality_weak(ocr_text) else "ok"
    if not has_text:
        return _safe_result(
            "",
            method="ocr",
            quality="weak",
            message="已执行 PDF OCR fallback，但未提取到稳定文本，建议跳过或人工处理。",
            file_type="pdf",
            can_evaluate=False,
            should_skip=True,
        )

    message = "已使用 PDF OCR fallback，提取成功。" if ocr_quality == "ok" else "已使用 PDF OCR fallback，但文本质量较弱，建议人工复核。"
    return _safe_result(
        ocr_text,
        method="ocr",
        quality=ocr_quality,
        message=message,
        file_type="pdf",
        can_evaluate=True,
        should_skip=False,
    )


def load_resume_file(file_obj) -> dict:
    file_name = (getattr(file_obj, "name", "") or "").lower()

    if file_name.endswith(".txt"):
        return _text_result(
            _load_txt(file_obj),
            file_type="txt",
            success_message="TXT 文本提取成功。",
            weak_message="TXT 提取完成，但文本质量较弱，建议人工复核。",
        )

    if file_name.endswith(".docx"):
        return _text_result(
            _load_docx(file_obj),
            file_type="docx",
            success_message="DOCX 文本提取成功。",
            weak_message="DOCX 提取完成，但文本质量较弱，建议人工复核。",
        )

    if file_name.endswith(".pdf"):
        return _pdf_result_with_fallback(file_obj)

    if file_name.endswith((".png", ".jpg", ".jpeg")):
        return _image_result_with_ocr(file_obj)

    raise ValueError("暂不支持该文件类型，请上传 txt / pdf / docx / png / jpg / jpeg。")


def check_ocr_capabilities() -> dict:
    dependency_status = {
        "pillow": True,
        "pytesseract": True,
        "pdf2image": True,
    }
    missing: list[str] = []
    try:
        import PIL  # noqa: F401
    except ModuleNotFoundError:
        missing.append("pillow")
        dependency_status["pillow"] = False

    try:
        import pytesseract  # noqa: F401
    except ModuleNotFoundError:
        missing.append("pytesseract")
        dependency_status["pytesseract"] = False

    try:
        import pdf2image  # noqa: F401
    except ModuleNotFoundError:
        missing.append("pdf2image")
        dependency_status["pdf2image"] = False

    runtime_missing: list[str] = []
    tesseract_ok = _tesseract_available()
    poppler_ok = _poppler_available()
    runtime_status = {
        "tesseract": tesseract_ok,
        "poppler": poppler_ok,
    }
    if not tesseract_ok:
        runtime_missing.append("tesseract")
    if not poppler_ok:
        runtime_missing.append("poppler")

    image_ocr_available = all(dep not in missing for dep in ["pillow", "pytesseract"]) and tesseract_ok
    pdf_ocr_available = all(dep not in missing for dep in ["pdf2image", "pytesseract"]) and tesseract_ok and poppler_ok
    hints: list[str] = []
    if not dependency_status["pillow"]:
        hints.append("缺少 pillow：图片 OCR 无法处理 PNG/JPG。")
    if not dependency_status["pytesseract"]:
        hints.append("缺少 pytesseract：图片 OCR 和 PDF OCR fallback 都无法调用 Tesseract。")
    if not dependency_status["pdf2image"]:
        hints.append("缺少 pdf2image：扫描版 PDF 无法走 OCR fallback。")
    if not tesseract_ok:
        hints.append("未检测到 tesseract：图片 OCR 与 PDF OCR fallback 都不可用。")
    if not poppler_ok:
        hints.append("未检测到 poppler：扫描版 PDF 无法转图片，因此 PDF OCR fallback 不可用。")
    if image_ocr_available and pdf_ocr_available:
        hints.append("OCR 依赖齐全：图片 OCR 与 PDF OCR fallback 均可用。")

    return {
        "image_ocr_available": image_ocr_available,
        "pdf_ocr_available": pdf_ocr_available,
        "missing_deps": sorted(set(missing)),
        "missing_runtime": sorted(set(runtime_missing)),
        "dependency_status": dependency_status,
        "runtime_status": runtime_status,
        "hints": hints,
    }


def _demo_read(path: str) -> None:
    from io import BytesIO

    file_path = Path(path)
    if not file_path.exists():
        print(f"[ERROR] 文件不存在: {file_path}")
        return

    raw = file_path.read_bytes()
    fake_upload = BytesIO(raw)
    fake_upload.name = file_path.name

    try:
        result = load_resume_file(fake_upload)
        print("=" * 80)
        print(f"文件: {file_path.name}")
        print(
            "method={method} quality={quality} parse_status={parse_status} can_evaluate={can_evaluate}".format(
                method=result["method"],
                quality=result["quality"],
                parse_status=result["parse_status"],
                can_evaluate=result["can_evaluate"],
            )
        )
        print(f"message={result['message']}")
        print(f"text_preview={result['text'][:120].replace(chr(10), ' | ')}")
    except Exception as exc:
        print(f"[ERROR] {file_path.name}: {exc}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) <= 1:
        print("用法: python -m src.resume_loader <txt/docx/pdf/png/jpg/jpeg 路径...>")
        print("示例: python -m src.resume_loader ./samples/demo.txt ./samples/demo.docx ./samples/demo.pdf ./samples/demo.png")
    else:
        for arg in sys.argv[1:]:
            _demo_read(arg)
