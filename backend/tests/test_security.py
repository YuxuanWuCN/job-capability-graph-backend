from fastapi import Response

from app.auth.router import _set_auth_cookies
from app.core.config import get_settings
from app.core.security import (
    constant_time_equal,
    hash_password,
    new_token,
    token_digest,
    verify_password,
)


def test_password_hash_is_argon2_and_verifies_only_correct_password() -> None:
    encoded = hash_password("correct-password")

    assert encoded.startswith("$argon2id$")
    assert verify_password("correct-password", encoded)
    assert not verify_password("wrong-password", encoded)


def test_token_digest_is_stable_and_does_not_store_plaintext() -> None:
    token = new_token()
    digest = token_digest(token, "test-secret-at-least-32-characters")

    assert len(token) >= 32
    assert len(digest) == 64
    assert token not in digest
    assert constant_time_equal(
        digest,
        token_digest(token, "test-secret-at-least-32-characters"),
    )


def test_mobile_http_guest_cookies_honor_7d_ttl(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("SESSION_TTL_SECONDS", "604800")
    monkeypatch.setenv("GUEST_LOGIN_ENABLED", "true")
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.session_ttl_seconds == 604800
        assert settings.guest_login_enabled is True
        assert settings.secure_cookie is False

        response = Response()
        _set_auth_cookies(response, "sess-tok", "csrf-tok")
        cookies = response.headers.getlist("set-cookie")
        by_name = {item.split("=", 1)[0]: item for item in cookies}

        session_cookie = by_name["session"]
        assert "HttpOnly" in session_cookie
        assert "SameSite=lax" in session_cookie
        assert "Max-Age=604800" in session_cookie
        assert "Secure" not in session_cookie

        csrf_cookie = by_name["csrf"]
        assert "HttpOnly" not in csrf_cookie
        assert "SameSite=lax" in csrf_cookie
        assert "Max-Age=604800" in csrf_cookie
        assert "Secure" not in csrf_cookie
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
