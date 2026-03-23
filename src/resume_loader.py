"""Resume file loading and OCR fallback helpers."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

FALLBACK_TEXT = "[未提取到稳定文本，请人工核对后再评估]"
OCR_LANG = "chi_sim+eng"
PDF_OCR_DPI = 300
OCR_UPSCALE_TRIGGER = 1800
OCR_UPSCALE_TARGET = 2400
OCR_DOWNSCALE_TRIGGER = 3800
OCR_DOWNSCALE_TARGET = 3200

_SPACE_RE = re.compile(r"[ \t\f\v]+")
_MULTI_BLANK_RE = re.compile(r"\n{3,}")
_NOISE_REPEAT_RE = re.compile(r"([^\w\s\u4e00-\u9fff])\1{4,}")
_DATE_RE = re.compile(
    r"(20\d{2}\s*[./-]\s*\d{1,2})|(20\d{2}\s*年\s*\d{1,2}\s*月)|((19|20)\d{2}\s*[./-]\s*(0?[1-9]|1[0-2]))"
)
_EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?:\+?86[-\s]?)?1[3-9]\d{9}")
_RESUME_KEYWORDS = (
    "教育背景",
    "教育经历",
    "工作经历",
    "实习经历",
    "项目经历",
    "项目经验",
    "技能",
    "专业技能",
    "技术栈",
    "校园经历",
    "个人简介",
    "自我评价",
    "联系方式",
    "education",
    "experience",
    "work experience",
    "internship",
    "project",
    "projects",
    "skills",
    "summary",
    "profile",
    "university",
    "college",
    "bachelor",
    "master",
)
_EDUCATION_SIGNAL_KEYWORDS = (
    "教育背景",
    "教育经历",
    "学历",
    "专业",
    "本科",
    "硕士",
    "博士",
    "university",
    "college",
    "bachelor",
    "master",
    "phd",
    "major",
    "degree",
)
_EXPERIENCE_SIGNAL_KEYWORDS = (
    "工作经历",
    "实习经历",
    "项目经历",
    "项目经验",
    "校园经历",
    "经历",
    "experience",
    "work experience",
    "internship",
    "intern",
    "project",
    "projects",
    "employment",
)
_SKILL_SIGNAL_KEYWORDS = (
    "技能",
    "专业技能",
    "技术栈",
    "技能清单",
    "skills",
    "tech stack",
    "tool",
    "tools",
    "python",
    "sql",
    "excel",
)


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


def _is_cjk(char: str) -> bool:
    return "\u4e00" <= char <= "\u9fff"


def _meaningful_char_count(text: str) -> int:
    return sum(1 for ch in text if ch.isalnum() or _is_cjk(ch))


def _looks_like_noise_line(line: str) -> bool:
    stripped = (line or "").strip()
    if not stripped:
        return False
    if re.fullmatch(r"[^\w\u4e00-\u9fff]+", stripped) and len(stripped) >= 4:
        return True
    total = len([ch for ch in stripped if not ch.isspace()])
    if total <= 0:
        return False
    meaningful = _meaningful_char_count(stripped)
    return total >= 10 and meaningful == 0


def _clean_extracted_text(text: str) -> str:
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\u3000", " ").replace("\xa0", " ")
    normalized = _NOISE_REPEAT_RE.sub(lambda match: match.group(1) * 3, normalized)

    cleaned_lines: list[str] = []
    blank_open = False
    for raw_line in normalized.split("\n"):
        line = _SPACE_RE.sub(" ", raw_line).strip()
        if _looks_like_noise_line(line):
            continue
        if not line:
            if cleaned_lines and not blank_open:
                cleaned_lines.append("")
                blank_open = True
            continue
        cleaned_lines.append(line)
        blank_open = False

    cleaned = "\n".join(cleaned_lines).strip()
    return _MULTI_BLANK_RE.sub("\n\n", cleaned)


def _count_resume_keywords(text: str) -> int:
    lowered = (text or "").lower()
    return sum(1 for keyword in _RESUME_KEYWORDS if keyword.lower() in lowered)


def _has_any_keyword(text: str, keywords: tuple[str, ...]) -> bool:
    lowered = (text or "").lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def _count_readable_lines(text: str) -> int:
    readable = 0
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if len(line) < 6:
            continue
        total = len([ch for ch in line if not ch.isspace()])
        if total <= 0:
            continue
        meaningful = _meaningful_char_count(line)
        if meaningful >= 6 and meaningful / total >= 0.45:
            readable += 1
    return readable


def _analyze_text_quality(text: str) -> dict[str, Any]:
    clean = _clean_extracted_text(text)
    non_blank = [ch for ch in clean if not ch.isspace()]
    if not non_blank:
        return {
            "clean_text": "",
            "length": 0,
            "meaningful_ratio": 0.0,
            "keyword_hits": 0,
            "has_time_pattern": False,
            "readable_lines": 0,
            "has_contact_signal": False,
            "score": -3,
            "weak": True,
        }

    meaningful = _meaningful_char_count(clean)
    meaningful_ratio = meaningful / len(non_blank)
    keyword_hits = _count_resume_keywords(clean)
    has_time_pattern = bool(_DATE_RE.search(clean))
    readable_lines = _count_readable_lines(clean)
    has_contact_signal = bool(_EMAIL_RE.search(clean) or _PHONE_RE.search(clean))

    score = 0
    if len(clean) >= 100:
        score += 1
    if len(clean) >= 220:
        score += 1
    if meaningful_ratio >= 0.58:
        score += 1
    if keyword_hits >= 2:
        score += 2
    elif keyword_hits == 1:
        score += 1
    if has_time_pattern:
        score += 1
    if readable_lines >= 3:
        score += 1
    if readable_lines >= 6:
        score += 1
    if has_contact_signal:
        score += 1

    if len(clean) < 80:
        score -= 2
    if meaningful_ratio < 0.45:
        score -= 2
    if keyword_hits == 0 and not has_time_pattern:
        score -= 1
    if readable_lines <= 1:
        score -= 1

    weak = len(clean) < 80 or meaningful_ratio < 0.35 or score < 2
    return {
        "clean_text": clean,
        "length": len(clean),
        "meaningful_ratio": meaningful_ratio,
        "keyword_hits": keyword_hits,
        "has_time_pattern": has_time_pattern,
        "readable_lines": readable_lines,
        "has_contact_signal": has_contact_signal,
        "score": score,
        "weak": weak,
    }


def _compact_quality_analysis(text: str) -> dict[str, Any]:
    analysis = _analyze_text_quality(text)
    clean = str(analysis.get("clean_text") or "")
    return {
        "length": int(analysis.get("length") or 0),
        "keyword_hits": int(analysis.get("keyword_hits") or 0),
        "has_time_pattern": bool(analysis.get("has_time_pattern")),
        "readable_lines": int(analysis.get("readable_lines") or 0),
        "has_contact_signal": bool(analysis.get("has_contact_signal")),
        "score": int(analysis.get("score") or 0),
        "weak": bool(analysis.get("weak", True)),
        "education_keyword_hit": _has_any_keyword(clean, _EDUCATION_SIGNAL_KEYWORDS),
        "experience_keyword_hit": _has_any_keyword(clean, _EXPERIENCE_SIGNAL_KEYWORDS),
        "skill_keyword_hit": _has_any_keyword(clean, _SKILL_SIGNAL_KEYWORDS),
    }


def _is_text_quality_weak(text: str) -> bool:
    return bool(_analyze_text_quality(text).get("weak", True))


def _quality_label(text: str) -> str:
    return "weak" if _is_text_quality_weak(text) else "ok"


def _join_page_texts(page_texts: list[str]) -> str:
    cleaned_pages = [section.strip() for section in page_texts if (section or "").strip()]
    return _clean_extracted_text("\n\n".join(cleaned_pages))


def _load_pdf_text(file_obj) -> str:
    try:
        from pypdf import PdfReader
    except ModuleNotFoundError as exc:
        raise ValueError("当前环境缺少 pypdf，无法读取 PDF。") from exc

    file_obj.seek(0)
    reader = PdfReader(file_obj)
    page_texts: list[str] = []
    total_pages = len(reader.pages)
    for index, page in enumerate(reader.pages, start=1):
        extracted = _clean_extracted_text(page.extract_text() or "")
        if not extracted:
            continue
        if total_pages > 1:
            page_texts.append(f"[第{index}页]\n{extracted}")
        else:
            page_texts.append(extracted)
    return _join_page_texts(page_texts)


def _load_docx(file_obj) -> str:
    try:
        from docx import Document
    except ModuleNotFoundError as exc:
        raise ValueError("当前环境缺少 python-docx，无法读取 DOCX。") from exc

    file_obj.seek(0)
    doc = Document(file_obj)
    lines = [paragraph.text.strip() for paragraph in doc.paragraphs if paragraph.text and paragraph.text.strip()]
    return "\n".join(lines)


def _import_pillow(context: str):
    try:
        from PIL import Image, ImageEnhance, ImageFilter, ImageOps
    except ModuleNotFoundError as exc:
        raise ValueError(f"{context} 需要 pillow，但当前未安装。") from exc
    return Image, ImageEnhance, ImageFilter, ImageOps


def _import_pytesseract(context: str):
    try:
        import pytesseract
    except ModuleNotFoundError as exc:
        raise ValueError(f"{context} 需要 pytesseract，但当前未安装。") from exc
    return pytesseract


def _otsu_threshold(grayscale_image) -> int:
    histogram = grayscale_image.histogram()[:256]
    total = sum(histogram)
    if total <= 0:
        return 180

    sum_total = sum(index * count for index, count in enumerate(histogram))
    sum_background = 0.0
    weight_background = 0.0
    max_variance = -1.0
    threshold = 180

    for index, count in enumerate(histogram):
        weight_background += count
        if weight_background <= 0:
            continue
        weight_foreground = total - weight_background
        if weight_foreground <= 0:
            break

        sum_background += index * count
        mean_background = sum_background / weight_background
        mean_foreground = (sum_total - sum_background) / weight_foreground
        variance = weight_background * weight_foreground * (mean_background - mean_foreground) ** 2
        if variance > max_variance:
            max_variance = variance
            threshold = index

    return max(96, min(208, int(threshold)))


def _build_ocr_variants(image) -> tuple[list[tuple[str, Any]], dict[str, Any]]:
    Image, ImageEnhance, ImageFilter, ImageOps = _import_pillow("OCR 图像预处理")
    working = ImageOps.exif_transpose(image)
    working.load()

    if working.mode not in {"RGB", "L"}:
        working = working.convert("RGB")

    operations: list[str] = []
    original_size = working.size
    max_side = max(original_size)
    target_side: int | None = None
    if max_side < OCR_UPSCALE_TRIGGER:
        target_side = OCR_UPSCALE_TARGET
    elif max_side > OCR_DOWNSCALE_TRIGGER:
        target_side = OCR_DOWNSCALE_TARGET

    if target_side is not None and max_side > 0:
        scale = target_side / max_side
        new_size = (
            max(1, int(round(working.size[0] * scale))),
            max(1, int(round(working.size[1] * scale))),
        )
        if new_size != working.size:
            resampling = getattr(Image, "Resampling", Image)
            working = working.resize(new_size, resampling.LANCZOS)
            operations.append(f"resize({new_size[0]}x{new_size[1]})")

    grayscale = ImageOps.grayscale(working)
    operations.append("grayscale")

    contrasted = ImageOps.autocontrast(grayscale, cutoff=1)
    contrasted = ImageEnhance.Contrast(contrasted).enhance(1.35)
    operations.append("autocontrast")
    operations.append("contrast")

    sharpened = contrasted.filter(ImageFilter.UnsharpMask(radius=1.2, percent=180, threshold=3))
    operations.append("sharpen")

    threshold = _otsu_threshold(sharpened)
    binary = sharpened.point(lambda px, threshold=threshold: 255 if px > threshold else 0, mode="1").convert("L")
    operations.append("binarize")

    variants = [("enhanced", sharpened), ("binary", binary)]
    return variants, {
        "preprocessed": True,
        "operations": operations,
        "used_binarization": True,
        "original_size": original_size,
        "processed_size": sharpened.size,
        "binary_threshold": threshold,
    }


def _select_stronger_text(primary_text: str, candidate_text: str) -> tuple[str, str, dict[str, Any]]:
    primary_analysis = _analyze_text_quality(primary_text)
    candidate_analysis = _analyze_text_quality(candidate_text)
    primary_score = (int(primary_analysis["score"]), int(primary_analysis["length"]))
    candidate_score = (int(candidate_analysis["score"]), int(candidate_analysis["length"]))

    if candidate_score > primary_score:
        return candidate_analysis["clean_text"], "candidate", candidate_analysis
    return primary_analysis["clean_text"], "primary", primary_analysis


def _ocr_image_to_text(image, *, context: str) -> dict[str, Any]:
    pytesseract = _import_pytesseract(context)
    variants, preprocess_meta = _build_ocr_variants(image)

    best_text = ""
    best_variant = variants[0][0]
    best_analysis = _analyze_text_quality("")
    variants_tried = 0

    for index, (variant_name, variant_image) in enumerate(variants):
        if index > 0 and best_text and not best_analysis["weak"]:
            break

        raw_text = pytesseract.image_to_string(variant_image, lang=OCR_LANG) or ""
        cleaned_text = _clean_extracted_text(raw_text)
        analysis = _analyze_text_quality(cleaned_text)
        variants_tried += 1

        if not best_text:
            best_text = cleaned_text
            best_variant = variant_name
            best_analysis = analysis
            continue

        current_score = (int(analysis["score"]), int(analysis["length"]))
        best_score = (int(best_analysis["score"]), int(best_analysis["length"]))
        if current_score > best_score:
            best_text = cleaned_text
            best_variant = variant_name
            best_analysis = analysis

    return {
        "text": best_analysis["clean_text"] or best_text,
        "variant": best_variant,
        "variants_tried": variants_tried,
        "quality_analysis": best_analysis,
        "preprocessed": bool(preprocess_meta["preprocessed"]),
        "operations": preprocess_meta["operations"],
        "used_binarization": bool(preprocess_meta["used_binarization"]) and best_variant == "binary",
        "binary_threshold": preprocess_meta["binary_threshold"],
    }


def _extract_pdf_with_ocr(file_obj) -> dict[str, Any]:
    try:
        from pdf2image import convert_from_bytes
    except ModuleNotFoundError as exc:
        raise ValueError("PDF OCR 需要 pdf2image，但当前未安装。") from exc

    _import_pytesseract("PDF OCR")

    if not _poppler_available():
        raise ValueError("PDF OCR 需要 poppler（缺少 pdfinfo/pdftoppm）。")
    if not _tesseract_available():
        raise ValueError("PDF OCR 需要 tesseract（未安装或未加入 PATH）。")

    raw = file_obj.getvalue()
    try:
        images = convert_from_bytes(raw, dpi=PDF_OCR_DPI)
    except Exception as exc:
        raise ValueError(f"PDF OCR 调用 poppler 失败（{exc}）。") from exc

    page_texts: list[str] = []
    failed_pages = 0
    used_binarization = False
    used_preprocessing = False
    total_pages = len(images)

    for index, image in enumerate(images, start=1):
        try:
            page_result = _ocr_image_to_text(image, context="PDF OCR")
        except Exception:
            failed_pages += 1
            continue

        page_text = str(page_result.get("text") or "").strip()
        used_binarization = used_binarization or bool(page_result.get("used_binarization"))
        used_preprocessing = used_preprocessing or bool(page_result.get("preprocessed"))
        if not page_text:
            continue

        if total_pages > 1:
            page_texts.append(f"[第{index}页]\n{page_text}")
        else:
            page_texts.append(page_text)

    return {
        "text": _join_page_texts(page_texts),
        "page_count": total_pages,
        "pages_with_text": len(page_texts),
        "failed_pages": failed_pages,
        "used_binarization": used_binarization,
        "preprocessed": used_preprocessing,
        "dpi": PDF_OCR_DPI,
    }


def _extract_image_with_ocr(file_obj) -> dict[str, Any]:
    Image, _, _, ImageOps = _import_pillow("图片 OCR")
    _import_pytesseract("图片 OCR")

    if not _tesseract_available():
        raise ValueError("图片 OCR 需要 tesseract（未安装或未加入 PATH）。")

    file_obj.seek(0)
    image = Image.open(file_obj)
    image = ImageOps.exif_transpose(image)
    image.load()
    return _ocr_image_to_text(image, context="图片 OCR")


def _safe_result(
    text: str,
    method: str,
    quality: str,
    message: str,
    *,
    file_type: str = "",
    can_evaluate: bool = True,
    should_skip: bool = False,
    used_ocr: bool = False,
    ocr_fallback_attempted: bool = False,
    ocr_fallback_succeeded: bool = False,
) -> dict:
    clean = (text or "").strip()
    final_quality = "ok" if (quality or "").lower() == "ok" else "weak"
    final_can_evaluate = bool(can_evaluate)
    final_should_skip = bool(should_skip)
    analysis = _compact_quality_analysis(text)

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
        "quality_analysis": analysis,
        "used_ocr": bool(used_ocr),
        "ocr_fallback_attempted": bool(ocr_fallback_attempted),
        "ocr_fallback_succeeded": bool(ocr_fallback_succeeded),
    }


def _text_result(text: str, *, file_type: str, success_message: str, weak_message: str) -> dict:
    has_text = bool((text or "").strip())
    quality = _quality_label(text)
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
        ocr_result = _extract_image_with_ocr(file_obj)
    except ValueError as exc:
        return _safe_result(
            "",
            method="ocr",
            quality="weak",
            message=f"图片 OCR 不可用（{exc}），当前文件不可稳定识别，建议跳过或人工处理。",
            file_type="image",
            can_evaluate=False,
            should_skip=True,
            used_ocr=False,
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
            used_ocr=False,
        )

    text = str(ocr_result.get("text") or "")
    has_text = bool(text.strip())
    quality = _quality_label(text)
    if not has_text:
        return _safe_result(
            "",
            method="ocr",
            quality="weak",
            message="图片 OCR 已执行（含图像预处理），但未提取到稳定文本，建议跳过或人工处理。",
            file_type="image",
            can_evaluate=False,
            should_skip=True,
            used_ocr=True,
        )

    message = "图片 OCR 已完成（含图像预处理），提取成功。"
    if quality == "weak":
        message = "图片 OCR 已完成（含图像预处理），但识别质量较弱，建议人工复核。"
    return _safe_result(
        text,
        method="ocr",
        quality=quality,
        message=message,
        file_type="image",
        can_evaluate=True,
        should_skip=False,
        used_ocr=True,
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
            used_ocr=False,
            ocr_fallback_attempted=False,
            ocr_fallback_succeeded=False,
        )

    prefix = f"PDF 文本提取失败（{pdf_text_error}）。" if pdf_text_error else "PDF 文本提取质量较弱。"
    try:
        file_obj.seek(0)
        ocr_result = _extract_pdf_with_ocr(file_obj)
    except ValueError as exc:
        if (pdf_text or "").strip():
            return _safe_result(
                pdf_text,
                method="text",
                quality="weak",
                message=f"{prefix} OCR 不可用（{exc}），当前按弱质量文本返回，建议人工复核。",
                file_type="pdf",
                can_evaluate=True,
                should_skip=False,
                used_ocr=False,
                ocr_fallback_attempted=True,
                ocr_fallback_succeeded=False,
            )
        return _safe_result(
            "",
            method="ocr",
            quality="weak",
            message=f"{prefix} OCR 不可用（{exc}），当前文件不可稳定识别，建议跳过或人工处理。",
            file_type="pdf",
            can_evaluate=False,
            should_skip=True,
            used_ocr=False,
            ocr_fallback_attempted=True,
            ocr_fallback_succeeded=False,
        )
    except Exception as exc:
        if (pdf_text or "").strip():
            return _safe_result(
                pdf_text,
                method="text",
                quality="weak",
                message=f"{prefix} OCR 执行失败（{exc}），当前按弱质量文本返回，建议人工复核。",
                file_type="pdf",
                can_evaluate=True,
                should_skip=False,
                used_ocr=False,
                ocr_fallback_attempted=True,
                ocr_fallback_succeeded=False,
            )
        return _safe_result(
            "",
            method="ocr",
            quality="weak",
            message=f"{prefix} OCR 执行失败（{exc}），当前文件未进入稳定评估，建议人工处理。",
            file_type="pdf",
            can_evaluate=False,
            should_skip=True,
            used_ocr=False,
            ocr_fallback_attempted=True,
            ocr_fallback_succeeded=False,
        )

    ocr_text = str(ocr_result.get("text") or "")
    if not ocr_text.strip():
        if (pdf_text or "").strip():
            return _safe_result(
                pdf_text,
                method="text",
                quality="weak",
                message="已尝试 PDF OCR fallback（300 DPI + 图像预处理），但仍只拿到弱质量文本，建议人工复核。",
                file_type="pdf",
                can_evaluate=True,
                should_skip=False,
                used_ocr=True,
                ocr_fallback_attempted=True,
                ocr_fallback_succeeded=False,
            )
        return _safe_result(
            "",
            method="ocr",
            quality="weak",
            message="已尝试 PDF OCR fallback（300 DPI + 图像预处理），但未提取到稳定文本，建议跳过或人工处理。",
            file_type="pdf",
            can_evaluate=False,
            should_skip=True,
            used_ocr=True,
            ocr_fallback_attempted=True,
            ocr_fallback_succeeded=False,
        )

    if (pdf_text or "").strip():
        selected_text, selected_source, selected_analysis = _select_stronger_text(pdf_text, ocr_text)
        if selected_source == "primary":
            return _safe_result(
                selected_text,
                method="text",
                quality="weak" if selected_analysis["weak"] else "ok",
                message="已尝试 PDF OCR fallback（300 DPI + 图像预处理），但原始文本提取结果更稳定。"
                if not selected_analysis["weak"]
                else "已尝试 PDF OCR fallback（300 DPI + 图像预处理），但文本整体仍偏弱，建议人工复核。",
                file_type="pdf",
                can_evaluate=True,
                should_skip=False,
                used_ocr=True,
                ocr_fallback_attempted=True,
                ocr_fallback_succeeded=False,
            )

    failed_pages = int(ocr_result.get("failed_pages") or 0)
    ocr_quality = _quality_label(ocr_text)
    message = "已使用 PDF OCR fallback（300 DPI + 图像预处理），提取成功。"
    if ocr_quality == "weak":
        message = "已使用 PDF OCR fallback（300 DPI + 图像预处理），但识别质量较弱，建议人工复核。"
    if failed_pages > 0:
        message += f" 其中 {failed_pages} 页 OCR 失败，已保留可识别页面。"
    return _safe_result(
        ocr_text,
        method="ocr",
        quality=ocr_quality,
        message=message,
        file_type="pdf",
        can_evaluate=True,
        should_skip=False,
        used_ocr=True,
        ocr_fallback_attempted=True,
        ocr_fallback_succeeded=True,
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
    if image_ocr_available or pdf_ocr_available:
        hints.append("当前 OCR 会先做灰度化、对比度增强、锐化和适度缩放，再调用 Tesseract。")
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
