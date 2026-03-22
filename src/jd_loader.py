"""JD file loader with text extraction and optional PDF OCR fallback."""

from __future__ import annotations

from .resume_loader import (
    FALLBACK_TEXT,
    _extract_pdf_with_ocr,
    _is_text_quality_weak,
    _load_docx,
    _load_pdf_text,
    _load_txt,
)


def _safe_jd_result(text: str, method: str, quality: str, message: str) -> dict:
    clean_text = (text or "").strip() or FALLBACK_TEXT
    final_quality = "ok" if (quality or "").lower() == "ok" else "weak"
    return {
        "text": clean_text,
        "method": method or "text",
        "quality": final_quality,
        "message": " ".join((message or "").split()),
    }


def _load_text_result(text: str, *, file_label: str) -> dict:
    quality = "weak" if _is_text_quality_weak(text) else "ok"
    if (text or "").strip():
        message = f"{file_label} 文本提取成功。" if quality == "ok" else f"{file_label} 文本已提取，但质量较弱，建议人工校对后再保存。"
        return _safe_jd_result(text, "text", quality, message)
    return _safe_jd_result("", "text", "weak", f"{file_label} 未提取到稳定 JD 文本，建议人工补充后再保存。")


def _load_pdf_result(file_obj) -> dict:
    pdf_text = ""
    pdf_text_error = ""
    try:
        pdf_text = _load_pdf_text(file_obj)
    except Exception as exc:  # noqa: BLE001
        pdf_text_error = str(exc)

    if pdf_text and not _is_text_quality_weak(pdf_text):
        return _safe_jd_result(pdf_text, "text", "ok", "PDF JD 文本提取成功。")

    prefix = f"PDF 文本提取失败（{pdf_text_error}）。" if pdf_text_error else "PDF 文本提取质量较弱。"
    try:
        file_obj.seek(0)
        ocr_text = _extract_pdf_with_ocr(file_obj)
    except Exception as exc:  # noqa: BLE001
        if (pdf_text or "").strip():
            return _safe_jd_result(
                pdf_text,
                "text",
                "weak",
                f"{prefix} OCR fallback 不可用（{exc}），已保留可提取文本，请人工校对后再保存。",
            )
        return _safe_jd_result(
            "",
            "ocr",
            "weak",
            f"{prefix} OCR fallback 不可用（{exc}），当前未提取到稳定 JD 文本，请人工补充后再保存。",
        )

    quality = "weak" if _is_text_quality_weak(ocr_text) else "ok"
    message = "已使用 PDF OCR fallback 提取 JD。" if quality == "ok" else "已使用 PDF OCR fallback，但 JD 文本质量较弱，建议人工校对后再保存。"
    return _safe_jd_result(ocr_text, "ocr", quality, message)


def load_jd_file(file_obj) -> dict:
    file_name = str(getattr(file_obj, "name", "") or "").lower()

    if file_name.endswith(".txt"):
        return _load_text_result(_load_txt(file_obj), file_label="TXT JD")

    if file_name.endswith(".docx"):
        return _load_text_result(_load_docx(file_obj), file_label="DOCX JD")

    if file_name.endswith(".pdf"):
        return _load_pdf_result(file_obj)

    raise ValueError("暂不支持该 JD 文件类型，请上传 txt / pdf / docx。")
