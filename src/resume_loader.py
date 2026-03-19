"""简历文件读取模块（本地演示版）。

支持：txt / pdf / docx / png / jpg / jpeg
返回：
- text: 提取文本
- method: text / ocr
- quality: ok / weak
- message: 当前提取情况说明
"""

from __future__ import annotations

from pathlib import Path

FALLBACK_TEXT = "【未提取到稳定文本，请手动修正后再评估】"


def _decode_text_bytes(raw: bytes) -> str:
    """优先 UTF-8，失败回退 GBK。"""
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
    lines = [p.text.strip() for p in doc.paragraphs if p.text and p.text.strip()]
    return "\n".join(lines)


def _is_text_quality_weak(text: str) -> bool:
    """弱质量判断：过短、空白多、乱码比例高。"""
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
    """仅 PDF 使用 OCR fallback。依赖不可用时抛友好错误。"""
    try:
        from pdf2image import convert_from_bytes
    except ModuleNotFoundError as exc:
        raise ValueError("PDF OCR 需要 pdf2image（未安装）。") from exc

    try:
        import pytesseract
    except ModuleNotFoundError as exc:
        raise ValueError("PDF OCR 需要 pytesseract（未安装）。") from exc

    raw = file_obj.getvalue()
    images = convert_from_bytes(raw, dpi=200)
    ocr_lines: list[str] = []
    for image in images:
        line = (pytesseract.image_to_string(image, lang="chi_sim+eng") or "").strip()
        if line:
            ocr_lines.append(line)
    return "\n".join(ocr_lines)




def _extract_image_with_ocr(file_obj) -> str:
    """图片文件使用 OCR 识别。依赖不可用时抛友好错误。"""
    try:
        from PIL import Image
    except ModuleNotFoundError as exc:
        raise ValueError("图片 OCR 需要 pillow（未安装）。") from exc

    try:
        import pytesseract
    except ModuleNotFoundError as exc:
        raise ValueError("图片 OCR 需要 pytesseract（未安装）。") from exc

    file_obj.seek(0)
    image = Image.open(file_obj)
    return pytesseract.image_to_string(image, lang="chi_sim+eng") or ""


def _image_result_with_ocr(file_obj) -> dict:
    """图片：直接走 OCR，并保证返回结构稳定。"""
    try:
        text = _extract_image_with_ocr(file_obj)
    except ValueError as exc:
        return _safe_result(
            text="",
            method="ocr",
            quality="weak",
            message=f"当前环境未启用图片 OCR（{exc}），建议手动粘贴文本或在部署环境安装 OCR 依赖。",
        )
    except Exception as exc:  # noqa: BLE001
        return _safe_result(
            text="",
            method="ocr",
            quality="weak",
            message=f"图片 OCR 调用失败（{exc}），建议手动粘贴文本或在部署环境检查 OCR 配置。",
        )

    quality = "weak" if _is_text_quality_weak(text) else "ok"
    message = "图片 OCR 提取成功。" if quality == "ok" else "图片 OCR 已完成，但文本质量较弱，建议手动修正后再评估。"
    return _safe_result(text=text, method="ocr", quality=quality, message=message)

def _safe_result(text: str, method: str, quality: str, message: str) -> dict:
    """统一构建返回结果，确保始终可用。"""
    clean = (text or "").strip()
    if not clean:
        clean = FALLBACK_TEXT
        quality = "weak"
        if "人工" not in message:
            message = f"{message} 建议人工检查并手动修正。"
    return {"text": clean, "method": method, "quality": quality, "message": message}


def _pdf_result_with_fallback(file_obj) -> dict:
    """PDF：文本优先，质量弱时 OCR fallback，并保障返回稳定。"""
    pdf_text = _load_pdf_text(file_obj)
    if not _is_text_quality_weak(pdf_text):
        return _safe_result(pdf_text, method="text", quality="ok", message="PDF 普通文本提取成功。")

    # 文本为空/过短/质量差：尝试 OCR
    try:
        file_obj.seek(0)
        ocr_text = _extract_pdf_with_ocr(file_obj)
    except ValueError as exc:
        # OCR 依赖缺失：不崩溃，返回弱质量可用结构
        msg = f"PDF 文本提取质量较弱，OCR 不可用（{exc}），建议人工检查或改用 txt/docx。"
        return _safe_result(pdf_text, method="text", quality="weak", message=msg)
    except Exception as exc:  # noqa: BLE001
        # OCR 运行失败：同样不崩溃
        msg = f"PDF 文本提取质量较弱，OCR 调用失败（{exc}），建议人工检查或改用 txt/docx。"
        return _safe_result(pdf_text, method="text", quality="weak", message=msg)

    # OCR 成功后仍做质量判定
    ocr_quality = "weak" if _is_text_quality_weak(ocr_text) else "ok"
    ocr_msg = "已使用 OCR fallback。"
    if ocr_quality == "weak":
        ocr_msg = "已使用 OCR fallback，但文本质量仍较弱，建议手动修正后再评估。"
    return _safe_result(ocr_text, method="ocr", quality=ocr_quality, message=ocr_msg)


def load_resume_file(file_obj) -> dict:
    """读取上传简历文件并返回结构化提取结果。"""
    file_name = (getattr(file_obj, "name", "") or "").lower()

    if file_name.endswith(".txt"):
        text = _load_txt(file_obj)
        quality = "weak" if _is_text_quality_weak(text) else "ok"
        message = "TXT 文本提取成功。" if quality == "ok" else "TXT 提取完成，但文本质量较弱，建议人工检查。"
        return _safe_result(text, method="text", quality=quality, message=message)

    if file_name.endswith(".docx"):
        text = _load_docx(file_obj)
        quality = "weak" if _is_text_quality_weak(text) else "ok"
        message = "DOCX 文本提取成功。" if quality == "ok" else "DOCX 提取完成，但文本质量较弱，建议人工检查。"
        return _safe_result(text, method="text", quality=quality, message=message)

    if file_name.endswith(".pdf"):
        return _pdf_result_with_fallback(file_obj)

    if file_name.endswith((".png", ".jpg", ".jpeg")):
        return _image_result_with_ocr(file_obj)

    raise ValueError("暂不支持该文件类型，请上传 txt / pdf / docx / png / jpg / jpeg。")




def check_ocr_capabilities() -> dict:
    """检查本地 OCR 依赖可用性（不触发真实 OCR 调用）。"""
    missing: list[str] = []
    try:
        import PIL  # noqa: F401
    except ModuleNotFoundError:
        missing.append("pillow")

    try:
        import pytesseract  # noqa: F401
    except ModuleNotFoundError:
        missing.append("pytesseract")

    try:
        import pdf2image  # noqa: F401
    except ModuleNotFoundError:
        missing.append("pdf2image")

    image_ocr_available = all(dep not in missing for dep in ["pillow", "pytesseract"])
    pdf_ocr_available = all(dep not in missing for dep in ["pdf2image", "pytesseract"])
    return {
        "image_ocr_available": image_ocr_available,
        "pdf_ocr_available": pdf_ocr_available,
        "missing_deps": sorted(set(missing)),
    }

def _demo_read(path: str) -> None:
    """本地测试入口：读取指定文件并打印结构化结果。"""
    from io import BytesIO

    p = Path(path)
    if not p.exists():
        print(f"[ERROR] 文件不存在: {p}")
        return

    raw = p.read_bytes()
    fake_upload = BytesIO(raw)
    fake_upload.name = p.name

    try:
        result = load_resume_file(fake_upload)
        print("=" * 80)
        print(f"文件: {p.name}")
        print(f"method={result['method']} quality={result['quality']}")
        print(f"message={result['message']}")
        print(f"text_preview={result['text'][:120].replace(chr(10), ' | ')}")
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] {p.name}: {exc}")


if __name__ == "__main__":
    # 示例：python -m src.resume_loader sample.txt sample.docx sample.pdf sample.png
    import sys

    if len(sys.argv) <= 1:
        print("用法: python -m src.resume_loader <txt/docx/pdf/png/jpg/jpeg 路径...>")
        print("示例: python -m src.resume_loader ./samples/demo.txt ./samples/demo.docx ./samples/demo.pdf ./samples/demo.png")
    else:
        for arg in sys.argv[1:]:
            _demo_read(arg)
