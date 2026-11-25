import asyncio
import re
import unicodedata
from collections import Counter, deque
from dataclasses import dataclass
from datetime import date
from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from docx import Document
from lxml import etree
from pypdf import PdfReader

from app.core.errors import APIError
from app.resumes.schemas import ResumeParseResponse

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    fitz = None

PDF_MEDIA_TYPES = {
    "",
    "application/pdf",
    "application/x-pdf",
    "application/octet-stream",
    "binary/octet-stream",
    "application/wps-office.pdf",
}
DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
DOCX_MEDIA_TYPES = {
    "",
    DOCX_MEDIA_TYPE,
    "application/octet-stream",
    "binary/octet-stream",
    "application/zip",
    "application/x-zip-compressed",
    "application/msword",
    "application/wps-office.docx",
}
MAX_RESUME_FILE_BYTES = 20 * 1024 * 1024
MAX_RESUME_PDF_PAGES = 30
MAX_DOCX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_EXTRACTED_TEXT_CHARS = 100_000
MIN_NATIVE_TEXT_CHARS = 20
MAX_OCR_IMAGES = 5
MAX_OCR_IMAGE_BYTES = 4 * 1024 * 1024
MAX_OCR_TOTAL_BYTES = 12 * 1024 * 1024
MIN_DOCX_IMAGE_BYTES = 2048
OCR_RENDER_DPI = 150
OCR_MAX_LONG_EDGE = 1600

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MC_NS = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
DOCX_SKIP_TAGS = {
    f"{W_NS}del",
    f"{W_NS}instrText",
    f"{W_NS}fldChar",
    f"{W_NS}pPr",
    f"{W_NS}rPr",
    f"{W_NS}tblPr",
    f"{W_NS}tblGrid",
    f"{W_NS}tcPr",
    f"{W_NS}sectPr",
}
DOCX_DEFERRED_TAGS = {f"{W_NS}txbxContent", f"{W_NS}tbl"}
CN_PUNCT_MAP = str.maketrans(
    {
        "，": ",",
        "。": ".",
        "：": ":",
        "；": ";",
        "（": "(",
        "）": ")",
        "【": "[",
        "】": "]",
        "、": ",",
        "—": "-",
        "–": "-",
        "－": "-",
    }
)

HORIZONTAL_WHITESPACE = re.compile(r"[^\S\n]+")
PHONE_PATTERN = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
EMAIL_PATTERN = re.compile(
    r"[A-Z0-9._%+-]{1,64}@[A-Z0-9-]{1,63}(?:\.[A-Z0-9-]{1,63})+",
    re.IGNORECASE,
)
IDENTITY_PATTERN = re.compile(
    r"(?<!\d)\d{6}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])"
    r"(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)"
)
WECHAT_PATTERN = re.compile(
    r"(?P<label>(?:微信号?|WeChat)[ \t]*[:：]?[ \t]*)"
    r"(?P<value>[A-Z][A-Z0-9_-]{5,19})",
    re.IGNORECASE,
)

EDUCATION_RANK = {
    "unknown": 0,
    "other": 1,
    "high_school": 2,
    "associate": 3,
    "bachelor": 4,
    "master": 5,
    "doctor": 6,
}
EVIDENCE_RANK = {"mention": 0, "project": 1, "work": 2}


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    text: str
    method: str


@dataclass(frozen=True, slots=True)
class ExtractedImage:
    media_type: str
    data: bytes


@dataclass(frozen=True, slots=True)
class ValidatedParse:
    document_language: str
    summary: str | None
    educations: list[dict]
    experiences: list[dict]
    projects: list[dict]
    skills: list[dict]
    warnings: list[str]


def detect_resume_document(filename: str, media_type: str, content: bytes) -> str:
    extension = Path(filename).suffix.lower()
    declared_type = media_type.split(";", 1)[0].strip().lower()

    if extension == ".pdf":
        if declared_type not in PDF_MEDIA_TYPES or b"%PDF-" not in content[:1024]:
            raise _unsupported_file_type()
        return "pdf"
    if extension == ".docx":
        if declared_type not in DOCX_MEDIA_TYPES:
            raise _unsupported_file_type()
        validate_docx_archive(content)
        return "docx"
    raise _unsupported_file_type()


def validate_docx_archive(content: bytes) -> None:
    try:
        with ZipFile(BytesIO(content)) as archive:
            infos = archive.infolist()
            names = {info.filename for info in infos}
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise ValueError("required docx entries missing")
            if any(info.flag_bits & 0x1 for info in infos):
                raise ValueError("encrypted docx entry")
            if sum(info.file_size for info in infos) > MAX_DOCX_UNCOMPRESSED_BYTES:
                raise ValueError("docx uncompressed size exceeded")
    except (BadZipFile, OSError, ValueError) as error:
        raise APIError(422, "RESUME_DOCUMENT_INVALID", "简历文档结构无效") from error


async def extract_resume_text(path: Path, document_type: str) -> ExtractedDocument:
    try:
        if document_type == "pdf":
            raw_text = await asyncio.to_thread(_extract_pdf_text, path)
            method = "pdf_text"
        elif document_type == "docx":
            raw_text = await asyncio.to_thread(_extract_docx_text, path)
            method = "docx"
        else:
            raise ValueError("unknown resume document type")
    except APIError:
        raise
    except Exception as error:
        raise APIError(422, "RESUME_DOCUMENT_INVALID", "简历文档结构无效") from error

    return ExtractedDocument(text=normalize_extracted_text(raw_text), method=method)


def normalize_extracted_text(value: str) -> str:
    normalized_newlines = value.replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    previous_blank = False
    for source_line in normalized_newlines.split("\n"):
        line = HORIZONTAL_WHITESPACE.sub(" ", source_line).strip()
        if line:
            lines.append(line)
            previous_blank = False
        elif lines and not previous_blank:
            lines.append("")
            previous_blank = True

    normalized = "\n".join(lines).strip()
    if not normalized:
        raise APIError(422, "RESUME_TEXT_EMPTY", "简历中未提取到可用文本")
    if len(normalized) > MAX_EXTRACTED_TEXT_CHARS:
        raise APIError(422, "RESUME_TEXT_TOO_LONG", "简历正文超过处理上限")
    return normalized


def has_sufficient_native_text(text: str) -> bool:
    return len(re.sub(r"\s+", "", text)) >= MIN_NATIVE_TEXT_CHARS


def extract_resume_images(path: Path, document_type: str) -> list[ExtractedImage]:
    try:
        if document_type == "pdf":
            return _extract_pdf_images(path)
        if document_type == "docx":
            return _extract_docx_images(path)
    except APIError:
        raise
    except Exception:
        return []
    return []


def build_docx_from_plain_text(text: str) -> bytes:
    normalized = normalize_extracted_text(text)
    document = Document()
    for line in normalized.splitlines():
        if line:
            document.add_paragraph(line)
    stream = BytesIO()
    document.save(stream)
    return stream.getvalue()


def redact_resume_text(value: str) -> str:
    redacted = PHONE_PATTERN.sub(_stars, value)
    redacted = EMAIL_PATTERN.sub(_stars, redacted)
    redacted = IDENTITY_PATTERN.sub(_stars, redacted)
    return WECHAT_PATTERN.sub(_redact_wechat, redacted)


def locate_evidence(text: str, quote: str) -> tuple[int, int] | None:
    if not quote:
        return None
    start = text.find(quote)
    if start >= 0:
        return start, start + len(quote)
    stripped = quote.strip(" \t\r\n\"'“”‘’")
    if stripped and stripped != quote:
        start = text.find(stripped)
        if start >= 0:
            return start, start + len(stripped)
    norm_quote, _ = _normalize_with_map(stripped or quote)
    if len(norm_quote) < 2:
        return None
    norm_text, orig_indices = _normalize_with_map(text)
    pos = norm_text.find(norm_quote)
    if pos < 0:
        return None
    return orig_indices[pos], orig_indices[pos + len(norm_quote) - 1] + 1


def validate_parse_evidence(
    payload: ResumeParseResponse,
    *,
    redacted_text: str,
) -> ValidatedParse:
    warnings: list[str] = []
    educations = _ground_items(
        payload.educations,
        category="EDUCATION",
        label_field="school_name",
        redacted_text=redacted_text,
        warnings=warnings,
    )
    experiences = _ground_items(
        payload.experiences,
        category="EXPERIENCE",
        label_field="company_name",
        redacted_text=redacted_text,
        warnings=warnings,
    )
    projects = _ground_items(
        payload.projects,
        category="PROJECT",
        label_field="project_name",
        redacted_text=redacted_text,
        warnings=warnings,
    )
    skills = _ground_items(
        payload.skills,
        category="SKILL",
        label_field="name",
        redacted_text=redacted_text,
        warnings=warnings,
    )
    if not any((educations, experiences, projects, skills)):
        raise APIError(422, "RESUME_EVIDENCE_EMPTY", "简历解析结果缺少可追溯证据")
    return ValidatedParse(
        document_language=payload.document_language,
        summary=payload.summary,
        educations=educations,
        experiences=experiences,
        projects=projects,
        skills=skills,
        warnings=warnings,
    )


def skill_rank(skill: dict) -> tuple[int, float, int]:
    return (
        EVIDENCE_RANK[skill["evidence_strength"]],
        float(skill["confidence"]),
        -int(skill["evidence_start"]),
    )


def derive_highest_education(educations: list[dict]) -> str | None:
    if not educations:
        return None
    levels = [
        value if value in EDUCATION_RANK else "unknown"
        for item in educations
        if (value := item.get("education_level")) is not None
    ]
    if not levels:
        return "unknown"
    return max(levels, key=EDUCATION_RANK.__getitem__)


def derive_total_experience_months(
    experiences: list[dict],
    *,
    current_month: date,
) -> tuple[int | None, list[str]]:
    intervals: list[tuple[int, int]] = []
    warnings: list[str] = []
    current_index = current_month.year * 12 + current_month.month - 1

    for item in experiences:
        start_month = item.get("start_month")
        end_month = item.get("end_month")
        is_current = item.get("is_current") is True
        if not start_month or (not is_current and not end_month):
            warnings.append("EXPERIENCE_DATE_INCOMPLETE")
            continue
        try:
            start_index = _month_index(start_month)
            end_index = current_index if is_current else _month_index(end_month)
        except (TypeError, ValueError):
            warnings.append("EXPERIENCE_DATE_INCOMPLETE")
            continue
        if end_index < start_index:
            warnings.append("EXPERIENCE_DATE_INCOMPLETE")
            continue
        intervals.append((start_index, end_index + 1))

    if not intervals:
        return None, warnings

    merged: list[list[int]] = []
    for start, end in sorted(intervals):
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return sum(end - start for start, end in merged), warnings


def _extract_pdf_text(path: Path) -> str:
    if fitz is not None:
        text = _extract_pdf_text_pymupdf(path)
        if text.strip():
            return text
        try:
            fallback = _extract_pdf_text_pypdf(path)
            if fallback.strip():
                return fallback
        except APIError:
            raise
        except Exception:
            pass
        return text
    return _extract_pdf_text_pypdf(path)


def _extract_pdf_text_pymupdf(path: Path) -> str:
    assert fitz is not None
    with fitz.open(path) as doc:
        if doc.is_encrypted and not doc.authenticate(""):
            raise APIError(
                422,
                "RESUME_DOCUMENT_INVALID",
                "简历文档受密码保护，无法解析",
            )
        if doc.page_count > MAX_RESUME_PDF_PAGES:
            raise APIError(
                422,
                "RESUME_PDF_TOO_MANY_PAGES",
                f"简历请控制在 {MAX_RESUME_PDF_PAGES} 页内",
            )
        chunks: list[str] = []
        for page_index in range(doc.page_count):
            page = doc.load_page(page_index)
            try:
                chunks.append(page.get_text("text") or "")
            finally:
                del page
        return "\n".join(chunks)


def _extract_pdf_text_pypdf(path: Path) -> str:
    with path.open("rb") as stream:
        reader = PdfReader(stream)
        page_count = len(reader.pages)
        if page_count > MAX_RESUME_PDF_PAGES:
            raise APIError(
                422,
                "RESUME_PDF_TOO_MANY_PAGES",
                f"简历请控制在 {MAX_RESUME_PDF_PAGES} 页内",
            )
        pages = deque(reader.pages)
        reader.flattened_pages = None
        remaining_uses = Counter(id(page) for page in pages)
        chunks: list[str] = []
        while pages:
            page = pages.popleft()
            try:
                chunks.append(page.extract_text() or "")
            finally:
                page_id = id(page)
                remaining_uses[page_id] -= 1
                if remaining_uses[page_id] == 0:
                    page.clear()
                del page
        return "\n".join(chunks)


def _extract_docx_text(path: Path) -> str:
    content = path.read_bytes()
    validate_docx_archive(content)
    parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=True)
    lines: list[str] = []
    with ZipFile(BytesIO(content)) as archive:
        names = set(archive.namelist())
        ordered_parts = [
            *sorted(
                name
                for name in names
                if name.startswith("word/header") and name.endswith(".xml")
            ),
            "word/document.xml",
            *sorted(
                name
                for name in names
                if name.startswith("word/footer") and name.endswith(".xml")
            ),
        ]
        for part_name in ordered_parts:
            if part_name not in names:
                continue
            root = etree.fromstring(archive.read(part_name), parser=parser)
            if root is None:
                if part_name == "word/document.xml":
                    raise ValueError("unable to parse word/document.xml")
                continue
            lines.extend(_extract_ooxml_lines(root))
    return "\n".join(lines)


def _extract_ooxml_lines(element: etree._Element) -> list[str]:
    lines: list[str] = []
    tag = element.tag
    if tag in DOCX_SKIP_TAGS:
        return lines
    if tag == f"{MC_NS}AlternateContent":
        choices = [child for child in element if child.tag == f"{MC_NS}Choice"]
        targets = choices if choices else list(element)
        for child in targets:
            lines.extend(_extract_ooxml_lines(child))
        return lines
    if tag == f"{W_NS}p":
        parts: list[str] = []
        deferred_blocks: list[etree._Element] = []
        _walk_paragraph_children(element, parts, deferred_blocks)
        paragraph_text = "".join(parts)
        if paragraph_text.strip():
            lines.append(paragraph_text)
        for block in deferred_blocks:
            lines.extend(_extract_ooxml_lines(block))
        return lines
    for child in element:
        lines.extend(_extract_ooxml_lines(child))
    return lines


def _walk_paragraph_children(
    element: etree._Element,
    parts: list[str],
    deferred_blocks: list[etree._Element],
) -> None:
    for child in element:
        tag = child.tag
        if tag in DOCX_SKIP_TAGS:
            continue
        if tag == f"{MC_NS}AlternateContent":
            choices = [item for item in child if item.tag == f"{MC_NS}Choice"]
            targets = choices if choices else list(child)
            for item in targets:
                _walk_paragraph_children(item, parts, deferred_blocks)
            continue
        if tag in DOCX_DEFERRED_TAGS:
            deferred_blocks.append(child)
            continue
        if tag == f"{W_NS}t":
            if child.text:
                parts.append(child.text)
            continue
        if tag == f"{W_NS}tab":
            parts.append(" ")
            continue
        if tag in {f"{W_NS}br", f"{W_NS}cr"}:
            parts.append("\n")
            continue
        _walk_paragraph_children(child, parts, deferred_blocks)


def _extract_pdf_images(path: Path) -> list[ExtractedImage]:
    if fitz is None:
        return []
    images: list[ExtractedImage] = []
    total_bytes = 0
    with fitz.open(path) as doc:
        if doc.is_encrypted and not doc.authenticate(""):
            return []
        if doc.page_count > MAX_RESUME_PDF_PAGES:
            return []
        for page_index in range(min(doc.page_count, MAX_OCR_IMAGES)):
            page = doc.load_page(page_index)
            pix = None
            try:
                if not page.get_images(full=False) and not page.get_drawings():
                    continue
                rect = page.rect
                scale = OCR_RENDER_DPI / 72.0
                longest = max(rect.width, rect.height, 1.0)
                if longest * scale > OCR_MAX_LONG_EDGE:
                    scale = OCR_MAX_LONG_EDGE / longest
                matrix = fitz.Matrix(scale, scale)
                pix = page.get_pixmap(matrix=matrix, alpha=False)
                data = pix.tobytes("jpeg", jpg_quality=80)
                if not data or len(data) > MAX_OCR_IMAGE_BYTES:
                    continue
                if total_bytes + len(data) > MAX_OCR_TOTAL_BYTES:
                    break
                images.append(ExtractedImage(media_type="image/jpeg", data=data))
                total_bytes += len(data)
            finally:
                del pix
                del page
    return images


def _extract_docx_images(path: Path) -> list[ExtractedImage]:
    content = path.read_bytes()
    validate_docx_archive(content)
    images: list[ExtractedImage] = []
    total_bytes = 0
    with ZipFile(BytesIO(content)) as archive:
        media_infos = sorted(
            (
                info
                for info in archive.infolist()
                if info.filename.startswith("word/media/") and not info.is_dir()
            ),
            key=lambda info: info.filename,
        )
        for info in media_infos:
            if len(images) >= MAX_OCR_IMAGES:
                break
            if not (MIN_DOCX_IMAGE_BYTES <= info.file_size <= MAX_OCR_IMAGE_BYTES):
                continue
            data = archive.read(info.filename)
            if not (MIN_DOCX_IMAGE_BYTES <= len(data) <= MAX_OCR_IMAGE_BYTES):
                continue
            media_type = _sniff_image_media_type(data)
            if media_type is None:
                continue
            if total_bytes + len(data) > MAX_OCR_TOTAL_BYTES:
                break
            images.append(ExtractedImage(media_type=media_type, data=data))
            total_bytes += len(data)
    return images


def _sniff_image_media_type(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    return None


def _normalize_with_map(source: str) -> tuple[str, list[int]]:
    chars: list[str] = []
    indices: list[int] = []
    for idx, ch in enumerate(source):
        if ch.isspace():
            continue
        norm = unicodedata.normalize("NFKC", ch).lower().translate(CN_PUNCT_MAP)
        for sub in norm:
            if not sub.isspace():
                chars.append(sub)
                indices.append(idx)
    return "".join(chars), indices


def _month_index(value: str) -> int:
    year_text, month_text = value.split("-", 1)
    year = int(year_text)
    month = int(month_text)
    if len(year_text) != 4 or len(month_text) != 2 or not 1 <= month <= 12:
        raise ValueError("invalid month")
    return year * 12 + month - 1


def _ground_items(
    items: list,
    *,
    category: str,
    label_field: str,
    redacted_text: str,
    warnings: list[str],
) -> list[dict]:
    grounded = []
    for item in items:
        value = item.model_dump(mode="python")
        offsets = locate_evidence(redacted_text, value["evidence_quote"])
        if offsets is None:
            warnings.append(f"{category}_EVIDENCE_NOT_FOUND:{value[label_field]}")
            continue
        value["evidence_start"], value["evidence_end"] = offsets
        grounded.append(value)
    return grounded


def _stars(match: re.Match[str]) -> str:
    return "*" * len(match.group(0))


def _redact_wechat(match: re.Match[str]) -> str:
    return f"{match.group('label')}{'*' * len(match.group('value'))}"


def _unsupported_file_type() -> APIError:
    return APIError(415, "RESUME_FILE_TYPE_UNSUPPORTED", "仅支持 PDF 或 DOCX 简历")
