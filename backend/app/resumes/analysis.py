import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.config import get_settings
from app.core.errors import APIError
from app.resumes.llm import (
    LLMParseResult,
    ResponsesClient,
    ResumeLLMError,
    create_responses_http_client,
)
from app.resumes.parsing import (
    ExtractedDocument,
    ValidatedParse,
    detect_resume_document,
    extract_resume_images,
    extract_resume_text,
    has_sufficient_native_text,
    normalize_extracted_text,
    redact_resume_text,
    validate_parse_evidence,
)

StageCallback = Callable[[str], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ResumeAnalysisResult:
    extracted_text: str
    extraction_method: str
    source_sha256: str
    validated: ValidatedParse
    llm_result: LLMParseResult
    requested_model: str


async def analyze_resume_document(
    path: Path,
    *,
    filename: str,
    media_type: str,
    processing_run_id: UUID,
    responses_client: ResponsesClient | None = None,
    settings: Any | None = None,
    on_stage: StageCallback | None = None,
) -> ResumeAnalysisResult:
    await _stage(on_stage, "extract_text")
    resolved_settings = settings or get_settings()
    content = await asyncio.to_thread(path.read_bytes)
    document_type = detect_resume_document(filename, media_type, content)

    extracted: ExtractedDocument | None = None
    try:
        extracted = await extract_resume_text(path, document_type)
    except APIError as error:
        if error.code != "RESUME_TEXT_EMPTY":
            raise

    if extracted is None or not has_sufficient_native_text(extracted.text):
        ocr_text = await _try_ocr_fallback(
            path,
            document_type=document_type,
            processing_run_id=processing_run_id,
            responses_client=responses_client,
            resolved_settings=resolved_settings,
            has_native_text=extracted is not None,
        )
        if ocr_text.strip():
            combined = (
                f"{extracted.text}\n\n{ocr_text}" if extracted is not None else ocr_text
            )
            extracted = ExtractedDocument(
                text=normalize_extracted_text(combined),
                method="pdf_text" if document_type == "pdf" else "docx",
            )

    if extracted is None:
        raise APIError(422, "RESUME_TEXT_EMPTY", "简历中未提取到可用文本")

    await _stage(on_stage, "redact_text")
    redacted_text = redact_resume_text(extracted.text)
    await _stage(on_stage, "call_llm")
    if not all(
        (
            resolved_settings.llm_responses_url,
            resolved_settings.llm_api_key,
            resolved_settings.llm_model,
        )
    ):
        raise APIError(503, "LLM_NOT_CONFIGURED", "简历解析服务尚未配置")
    request = {
        "url": str(resolved_settings.llm_responses_url),
        "api_key": resolved_settings.llm_api_key.get_secret_value(),
        "model": resolved_settings.llm_model,
        "redacted_text": redacted_text,
        "processing_run_id": processing_run_id,
    }
    if responses_client is None:
        async with create_responses_http_client() as http:
            llm_result = await ResponsesClient(http=http).parse_resume(**request)
    else:
        llm_result = await responses_client.parse_resume(**request)

    await _stage(on_stage, "validate_response")
    await _stage(on_stage, "validate_evidence")
    validated = validate_parse_evidence(
        llm_result.payload,
        redacted_text=redacted_text,
    )
    return ResumeAnalysisResult(
        extracted_text=extracted.text,
        extraction_method=extracted.method,
        source_sha256=hashlib.sha256(extracted.text.encode()).hexdigest(),
        validated=validated,
        llm_result=llm_result,
        requested_model=resolved_settings.llm_model,
    )


async def _try_ocr_fallback(
    path: Path,
    *,
    document_type: str,
    processing_run_id: UUID,
    responses_client: ResponsesClient | None,
    resolved_settings: Any,
    has_native_text: bool,
) -> str:
    if getattr(resolved_settings, "llm_api_style", "responses") != "chat":
        return ""
    if not all(
        (
            resolved_settings.llm_responses_url,
            resolved_settings.llm_api_key,
            resolved_settings.llm_model,
        )
    ):
        return ""
    if responses_client is not None and not hasattr(
        responses_client, "ocr_resume_images"
    ):
        return ""

    images = await asyncio.to_thread(extract_resume_images, path, document_type)
    if not images:
        return ""
    payload_images = [(item.media_type, item.data) for item in images]
    request = {
        "url": str(resolved_settings.llm_responses_url),
        "api_key": resolved_settings.llm_api_key.get_secret_value(),
        "model": resolved_settings.llm_model,
        "images": payload_images,
        "processing_run_id": processing_run_id,
    }
    try:
        if responses_client is None:
            async with create_responses_http_client() as http:
                return await ResponsesClient(http=http).ocr_resume_images(**request)
        return await responses_client.ocr_resume_images(**request)
    except ResumeLLMError as error:
        if error.code in {
            "LLM_REQUEST_REJECTED",
            "LLM_RESPONSE_INVALID",
            "LLM_RESPONSE_REFUSED",
        }:
            return ""
        if not has_native_text:
            raise
        return ""


async def _stage(callback: StageCallback | None, stage: str) -> None:
    if callback is not None:
        await callback(stage)
