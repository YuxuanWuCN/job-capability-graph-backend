from collections.abc import Callable, Iterator
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.auth.models import User
from app.core.config import get_settings
from app.main import app

INVITE_CODE = "lab-invite-2026"
PASSWORD = "correct-horse-battery"


@pytest.fixture
def set_invite_code(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[[str], None]]:
    def apply(code: str) -> None:
        monkeypatch.setenv("REGISTRATION_INVITE_CODE", code)
        get_settings.cache_clear()

    yield apply
    monkeypatch.undo()
    get_settings.cache_clear()


def registration_payload(username: str, invite_code: str = INVITE_CODE) -> dict:
    return {
        "username": username,
        "password": PASSWORD,
        "display_name": "测试学生",
        "invite_code": invite_code,
    }


def new_username() -> str:
    return f"student{uuid4().hex[:8]}"


async def test_registration_is_closed_without_invite_code(
    set_invite_code: Callable[[str], None],
) -> None:
    set_invite_code("")
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as http:
        response = await http.post(
            "/api/v1/auth/register",
            json=registration_payload(new_username()),
        )

    assert response.status_code == 403
    assert "REGISTRATION_DISABLED" in response.text


async def test_registration_rejects_short_password(
    set_invite_code: Callable[[str], None],
) -> None:
    set_invite_code(INVITE_CODE)
    payload = registration_payload(new_username())
    payload["password"] = "short"
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as http:
        response = await http.post("/api/v1/auth/register", json=payload)

    assert response.status_code == 422


async def test_wrong_invite_code_is_denied(
    client,
    set_invite_code: Callable[[str], None],
) -> None:
    set_invite_code(INVITE_CODE)
    response = await client.post(
        "/api/v1/auth/register",
        json=registration_payload(new_username(), invite_code="wrong-code"),
    )

    assert response.status_code == 403
    assert "INVITE_CODE_INVALID" in response.text


async def test_registration_creates_applicant_session(
    client,
    db_session,
    set_invite_code: Callable[[str], None],
) -> None:
    set_invite_code(INVITE_CODE)
    username = new_username()
    response = await client.post(
        "/api/v1/auth/register",
        json=registration_payload(username),
    )

    assert response.status_code == 201
    data = response.json()["data"]
    assert data["username"] == username
    assert data["role"] == "applicant"
    assert data["csrf_token"]
    assert "session" in response.cookies

    stored = await db_session.scalar(
        select(User).where(User.username_normalized == username)
    )
    assert stored is not None
    assert stored.role == "applicant"
    assert stored.is_active is True

    duplicate = await client.post(
        "/api/v1/auth/register",
        json=registration_payload(username),
    )
    assert duplicate.status_code == 409
    assert "USERNAME_ALREADY_EXISTS" in duplicate.text
