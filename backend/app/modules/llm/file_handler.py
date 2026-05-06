"""文件上传处理：提取 Word/PDF 文本内容，支持图片 base64 编码。"""

import base64
import io

from fastapi import UploadFile

SUPPORTED_DOC_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
    "application/msword",  # .doc
}
SUPPORTED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB


async def process_upload(file: UploadFile) -> dict:
    """处理上传文件，返回统一结构。

    Returns:
        {
            "type": "document" | "image",
            "filename": str,
            "content_type": str,
            "text": str | None,          # 文档提取的文本
            "image_base64": str | None,   # 图片 base64（含 data URI 前缀）
            "size": int,
        }
    """
    content = await file.read()
    size = len(content)

    if size > MAX_FILE_SIZE:
        raise ValueError(f"文件大小超过限制（最大 {MAX_FILE_SIZE // 1024 // 1024}MB）")

    ct = file.content_type or ""

    if ct in SUPPORTED_IMAGE_TYPES:
        b64 = base64.b64encode(content).decode()
        return {
            "type": "image",
            "filename": file.filename or "image",
            "content_type": ct,
            "text": None,
            "image_base64": f"data:{ct};base64,{b64}",
            "size": size,
        }

    name_lower = (file.filename or "").lower()
    if ct == "application/pdf" or name_lower.endswith(".pdf"):
        text = _extract_pdf(content)
    elif (
        ct == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        or name_lower.endswith(".docx")
    ):
        text = _extract_docx(content)
    elif ct == "application/msword" or name_lower.endswith(".doc"):
        text = _extract_doc(content)
    else:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError(f"不支持的文件类型: {ct}")

    return {
        "type": "document",
        "filename": file.filename or "file",
        "content_type": ct,
        "text": text,
        "image_base64": None,
        "size": size,
    }


def _extract_pdf(content: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(content))
    pages = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text)
    return "\n\n".join(pages)


def _extract_docx(content: bytes) -> str:
    from docx import Document

    doc = Document(io.BytesIO(content))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs)


def _extract_doc(content: bytes) -> str:
    """复用需求模块的 .doc 解析流水线（antiword → catdoc → 误命名 docx 兜底）。"""
    from app.modules.requirements.parser import _extract_doc as _parse

    return _parse(content)
