from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        env_ignore_empty=True,
    )

    app_env: Literal["local", "test", "internal"]
    app_base_url: AnyHttpUrl
    database_url: str
    redis_url: str
    neo4j_uri: str
    neo4j_username: str
    neo4j_password: SecretStr
    file_storage_root: Path
    session_secret: SecretStr = Field(min_length=32)
    session_ttl_seconds: int = Field(default=28_800, ge=300)
    max_import_file_bytes: int = Field(default=20 * 1024 * 1024, ge=1)
    max_import_rows: int = Field(default=100_000, ge=1)
    cors_origins: list[str]
    algorithm_service_url: AnyHttpUrl
    lgf_match_url: AnyHttpUrl | None = None
    lgf_api_key: SecretStr | None = None
    lgf_enabled: bool = False
    lgf_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    llm_responses_url: AnyHttpUrl | None = None
    llm_api_key: SecretStr | None = None
    llm_model: str | None = Field(default=None, min_length=1, max_length=200)
    # "responses" = OpenAI Responses API（默认，保持原行为）
    # "chat"      = OpenAI 兼容的 chat/completions（DeepSeek/通义/Kimi 等），
    #               此时 llm_responses_url 填 chat/completions 的完整地址，
    #               JSON Schema 由客户端写入提示词，靠提示词与校验重试保证结构。
    llm_api_style: Literal["responses", "chat"] = "responses"

    # 学生自助注册：留空即关闭注册（安全默认）
    registration_invite_code: SecretStr | None = None

    # 游客模式：开启后前端首次访问自动签发一个游客会话（role=applicant），
    # 无须账号密码即可体验完整学生流程；后台管理仍须管理员登录。
    guest_login_enabled: bool = True

    @property
    def secure_cookie(self) -> bool:
        return self.app_env == "internal"

    @property
    def registration_enabled(self) -> bool:
        code = self.registration_invite_code
        return code is not None and bool(code.get_secret_value().strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
