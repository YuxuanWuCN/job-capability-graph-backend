"""LLM 连通性自检：部署后先跑这个，确认 DeepSeek（或其它 provider）真的可用。

用法（服务器上、api 容器内）：
    docker compose -f compose.yaml -f deploy/compose.prod.yaml exec api \
        uv run python scripts/llm_smoke_check.py

退出码：0=通过，1=失败，2=未配置
安全：只打印 URL / 模型 / 风格，绝不打印 API Key 内容。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import BaseModel, ConfigDict, Field  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.llm.responses import (  # noqa: E402
    ResponsesAPIError,
    StructuredResponsesClient,
    create_responses_http_client,
)


class SmokePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(min_length=1)
    echo: str = Field(min_length=1)


async def main() -> int:
    settings = get_settings()
    url = settings.llm_responses_url
    api_key = settings.llm_api_key
    model = settings.llm_model
    style = settings.llm_api_style

    print(f"LLM_API_STYLE      = {style}")
    print(f"LLM_RESPONSES_URL  = {url if url else '(未配置)'}")
    print(f"LLM_MODEL          = {model or '(未配置)'}")
    if api_key:
        key_len = len(api_key.get_secret_value())
        print(f"LLM_API_KEY        = 已配置（长度 {key_len}，不显示内容）")
    else:
        print("LLM_API_KEY        = (未配置)")

    missing = [
        name
        for name, value in (
            ("LLM_RESPONSES_URL", url),
            ("LLM_API_KEY", api_key),
            ("LLM_MODEL", model),
        )
        if not value
    ]
    if missing:
        names = ", ".join(missing)
        print(f"\n[跳过] 未配置：{names}")
        print("AI 环节（简历解析 / 成长路径 / JD 解析）会直接失败。")
        return 2

    async with create_responses_http_client() as http:
        client = StructuredResponsesClient(http=http)
        try:
            result = await client.generate(
                url=str(url),
                api_key=api_key.get_secret_value(),
                model=str(model),
                instructions="你是连通性自检程序，必须严格按给定 JSON Schema 输出。",
                input_text="请返回 status=ok, echo=ping",
                schema_name="llm_smoke_check",
                response_model=SmokePayload,
                metadata={"operation": "llm_smoke_check"},
                max_output_tokens=200,
            )
        except ResponsesAPIError as exc:
            print(
                f"\n[失败] {exc.code}（stage={exc.stage}, retryable={exc.retryable}, "
                f"http={exc.http_status}）"
            )
            print("[排查] LLM_REQUEST_REJECTED → 模型名或 /chat/completions 路径不对；")
            print("       LLM_RATE_LIMITED → 余额不足或触发限流；")
            print("       LLM_TIMEOUT → 网络不通（大陆机器访问境外地址常见）。")
            return 1
        except Exception as exc:  # noqa: BLE001
            print(f"\n[失败] {type(exc).__name__}: {exc}")
            return 1

    print(f"\n[通过] returned_model={result.returned_model} status={result.status}")
    print(f"[通过] usage={result.usage}")
    print(f"[通过] payload={result.payload.model_dump()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
