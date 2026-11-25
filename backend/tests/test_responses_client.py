import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import BaseModel, ConfigDict, Field

from app.llm.responses import StructuredResponsesClient


class DemoPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1)


def completed_response(text: str) -> dict:
    return {
        "id": "resp_test",
        "model": "returned-model",
        "status": "completed",
        "error": None,
        "incomplete_details": None,
        "output": [
            {
                "type": "message",
                "status": "completed",
                "content": [{"type": "output_text", "text": text}],
            }
        ],
        "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
    }


async def test_structured_client_posts_strict_responses_schema() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(
            200,
            json=completed_response(json.dumps({"value": "ok"})),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await StructuredResponsesClient(http=http).generate(
            url="https://provider.test/v1/responses",
            api_key="secret",
            model="test-model",
            instructions="return the schema",
            input_text="context",
            schema_name="demo_v1",
            response_model=DemoPayload,
            metadata={"operation": "demo"},
        )

    body = json.loads(captured["request"].content)
    assert body["text"]["format"] == {
        "type": "json_schema",
        "name": "demo_v1",
        "strict": True,
        "schema": DemoPayload.model_json_schema(),
    }
    assert body["store"] is False
    assert body["stream"] is False
    assert "tools" not in body
    assert "reasoning" not in body
    assert result.payload.value == "ok"


async def test_incomplete_response_retry_doubles_output_limit() -> None:
    request_limits = []
    responses = [
        {
            **completed_response(json.dumps({"value": "ignored"})),
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
        },
        completed_response(json.dumps({"value": "ok"})),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        request_limits.append(json.loads(request.content)["max_output_tokens"])
        return httpx.Response(200, json=responses.pop(0))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await StructuredResponsesClient(
            http=http,
            sleep=lambda _seconds: asyncio.sleep(0),
        ).generate(
            url="https://provider.test/v1/responses",
            api_key="secret",
            model="test-model",
            instructions="return the schema",
            input_text="context",
            schema_name="demo_v1",
            response_model=DemoPayload,
            metadata={"operation": "demo"},
            max_output_tokens=5000,
        )

    assert request_limits == [5000, 10000]
    assert result.payload.value == "ok"


def chat_completion(content: str, finish_reason: str = "stop") -> dict:
    return {
        "id": "chatcmpl_test",
        "model": "deepseek-chat",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
    }


@pytest.fixture
def chat_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.llm.responses.get_settings",
        lambda: SimpleNamespace(llm_api_style="chat"),
    )


async def test_chat_mode_sends_chat_completions_body_and_parses_fenced_json(
    chat_mode: None,
) -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json=chat_completion('```json\n{"value": "ok"}\n```'),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await StructuredResponsesClient(http=http).generate(
            url="https://provider.test/chat/completions",
            api_key="secret",
            model="deepseek-chat",
            instructions="extract value",
            input_text="context",
            schema_name="demo_v1",
            response_model=DemoPayload,
            metadata={"operation": "demo"},
        )

    body = captured["body"]
    assert body["model"] == "deepseek-chat"
    assert body["response_format"] == {"type": "json_object"}
    assert body["max_tokens"] == 5000
    assert body["stream"] is False
    assert body["messages"][1] == {"role": "user", "content": "context"}
    system_prompt = body["messages"][0]["content"]
    assert body["messages"][0]["role"] == "system"
    assert "extract value" in system_prompt
    assert json.dumps(DemoPayload.model_json_schema(), ensure_ascii=False) in (
        system_prompt
    )
    for responses_only_key in ("text", "input", "instructions", "store", "metadata"):
        assert responses_only_key not in body
    assert result.payload.value == "ok"
    assert result.usage == {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}


async def test_chat_mode_length_retry_caps_tokens_at_provider_limit(
    chat_mode: None,
) -> None:
    limits = []
    responses = [
        chat_completion('{"value": "cut', finish_reason="length"),
        chat_completion(json.dumps({"value": "ok"})),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        limits.append(json.loads(request.content)["max_tokens"])
        return httpx.Response(200, json=responses.pop(0))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await StructuredResponsesClient(
            http=http,
            sleep=lambda _seconds: asyncio.sleep(0),
        ).generate(
            url="https://provider.test/chat/completions",
            api_key="secret",
            model="deepseek-chat",
            instructions="extract value",
            input_text="context",
            schema_name="demo_v1",
            response_model=DemoPayload,
            metadata={"operation": "demo"},
            max_output_tokens=5000,
        )

    assert limits == [5000, 8192]
    assert result.payload.value == "ok"


async def test_chat_mode_drops_response_format_when_provider_rejects_it(
    chat_mode: None,
) -> None:
    json_mode_flags = []

    def handler(request: httpx.Request) -> httpx.Response:
        has_json_mode = "response_format" in json.loads(request.content)
        json_mode_flags.append(has_json_mode)
        if has_json_mode:
            return httpx.Response(400, json={"error": {"message": "unsupported"}})
        return httpx.Response(200, json=chat_completion(json.dumps({"value": "ok"})))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await StructuredResponsesClient(http=http).generate(
            url="https://provider.test/chat/completions",
            api_key="secret",
            model="deepseek-chat",
            instructions="extract value",
            input_text="context",
            schema_name="demo_v1",
            response_model=DemoPayload,
            metadata={"operation": "demo"},
        )

    assert json_mode_flags == [True, False]
    assert result.payload.value == "ok"
