"""Authentication API acceptance tests."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.auth.passwords import verify_password
from app.auth.rate_limit import reset_rate_limits
from app.auth.tokens import hash_session_token
from app.config import get_settings
from app.db.models import AuthSession, User
from app.db.session import dispose_engine, get_session_factory, require_neon_settings
from app.main import create_app


@pytest.fixture(autouse=True)
def _reset_limits() -> None:
    reset_rate_limits()
    yield
    reset_rate_limits()


@pytest.fixture(scope="module")
def neon_ready() -> None:
    require_neon_settings()


@pytest_asyncio.fixture
async def client(neon_ready: None):
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Origin": "http://127.0.0.1:5173"},
    ) as ac:
        yield ac
    await dispose_engine()


def _email() -> str:
    return f"auth_test_{uuid.uuid4().hex[:12]}@example.com"


@pytest.mark.asyncio
async def test_01_signup_success(client: AsyncClient) -> None:
    email = _email()
    res = await client.post(
        "/api/auth/signup",
        json={
            "first_name": "James",
            "last_name": "Bond",
            "email": email.upper(),  # normalize
            "password": "secretpass",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["user"]["email"] == email
    assert body["user"]["first_name"] == "James"
    assert "password" not in body["user"]
    assert "password_hash" not in body["user"]
    assert get_settings().auth_cookie_name in res.cookies

    factory = get_session_factory()
    async with factory() as session:
        user = await session.scalar(select(User).where(User.email == email))
        assert user is not None
        assert user.password_hash != "secretpass"
        assert verify_password(user.password_hash, "secretpass")
        sess = await session.scalar(
            select(AuthSession).where(AuthSession.user_id == user.id)
        )
        assert sess is not None
        raw = res.cookies.get(get_settings().auth_cookie_name)
        assert raw
        assert sess.token_hash == hash_session_token(raw)
        assert sess.token_hash != raw


@pytest.mark.asyncio
async def test_02_duplicate_email(client: AsyncClient) -> None:
    email = _email()
    payload = {
        "first_name": "A",
        "last_name": "B",
        "email": email,
        "password": "secretpass",
    }
    assert (await client.post("/api/auth/signup", json=payload)).status_code == 200
    res = await client.post("/api/auth/signup", json=payload)
    assert res.status_code == 409


@pytest.mark.asyncio
async def test_03_invalid_email(client: AsyncClient) -> None:
    res = await client.post(
        "/api/auth/signup",
        json={
            "first_name": "A",
            "last_name": "B",
            "email": "not-an-email",
            "password": "secretpass",
        },
    )
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_04_password_too_short(client: AsyncClient) -> None:
    res = await client.post(
        "/api/auth/signup",
        json={
            "first_name": "A",
            "last_name": "B",
            "email": _email(),
            "password": "short",
        },
    )
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_05_signin_success(client: AsyncClient) -> None:
    email = _email()
    await client.post(
        "/api/auth/signup",
        json={
            "first_name": "A",
            "last_name": "B",
            "email": email,
            "password": "secretpass",
        },
    )
    client.cookies.clear()
    res = await client.post(
        "/api/auth/signin",
        json={"email": email, "password": "secretpass"},
    )
    assert res.status_code == 200
    assert get_settings().auth_cookie_name in res.cookies


@pytest.mark.asyncio
async def test_06_incorrect_password(client: AsyncClient) -> None:
    email = _email()
    await client.post(
        "/api/auth/signup",
        json={
            "first_name": "A",
            "last_name": "B",
            "email": email,
            "password": "secretpass",
        },
    )
    client.cookies.clear()
    res = await client.post(
        "/api/auth/signin",
        json={"email": email, "password": "wrongpassword"},
    )
    assert res.status_code == 401
    assert res.json()["detail"] == "Invalid email or password."


@pytest.mark.asyncio
async def test_07_unknown_email(client: AsyncClient) -> None:
    res = await client.post(
        "/api/auth/signin",
        json={"email": _email(), "password": "secretpass"},
    )
    assert res.status_code == 401
    assert res.json()["detail"] == "Invalid email or password."


@pytest.mark.asyncio
async def test_08_me_authenticated(client: AsyncClient) -> None:
    email = _email()
    await client.post(
        "/api/auth/signup",
        json={
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": email,
            "password": "secretpass",
        },
    )
    res = await client.get("/api/auth/me")
    assert res.status_code == 200
    body = res.json()
    assert body["authenticated"] is True
    assert body["user"]["email"] == email
    assert "password_hash" not in body["user"]


@pytest.mark.asyncio
async def test_09_me_unauthenticated(client: AsyncClient) -> None:
    client.cookies.clear()
    res = await client.get("/api/auth/me")
    assert res.status_code == 200
    assert res.json() == {"authenticated": False, "user": None}


@pytest.mark.asyncio
async def test_10_signout(client: AsyncClient) -> None:
    email = _email()
    await client.post(
        "/api/auth/signup",
        json={
            "first_name": "A",
            "last_name": "B",
            "email": email,
            "password": "secretpass",
        },
    )
    res = await client.post("/api/auth/signout")
    assert res.status_code == 200
    me = await client.get("/api/auth/me")
    assert me.json()["authenticated"] is False


@pytest.mark.asyncio
async def test_11_revoked_session(client: AsyncClient) -> None:
    email = _email()
    await client.post(
        "/api/auth/signup",
        json={
            "first_name": "A",
            "last_name": "B",
            "email": email,
            "password": "secretpass",
        },
    )
    cookie_name = get_settings().auth_cookie_name
    raw = client.cookies.get(cookie_name)
    assert raw
    factory = get_session_factory()
    async with factory() as session:
        row = await session.scalar(
            select(AuthSession).where(
                AuthSession.token_hash == hash_session_token(raw)
            )
        )
        assert row
        row.revoked_at = datetime.now(timezone.utc)
        await session.commit()
    me = await client.get("/api/auth/me")
    assert me.json()["authenticated"] is False


@pytest.mark.asyncio
async def test_12_expired_session(client: AsyncClient) -> None:
    email = _email()
    await client.post(
        "/api/auth/signup",
        json={
            "first_name": "A",
            "last_name": "B",
            "email": email,
            "password": "secretpass",
        },
    )
    cookie_name = get_settings().auth_cookie_name
    raw = client.cookies.get(cookie_name)
    factory = get_session_factory()
    async with factory() as session:
        row = await session.scalar(
            select(AuthSession).where(
                AuthSession.token_hash == hash_session_token(raw)
            )
        )
        assert row
        row.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        await session.commit()
    me = await client.get("/api/auth/me")
    assert me.json()["authenticated"] is False


@pytest.mark.asyncio
async def test_13_inactive_account(client: AsyncClient) -> None:
    email = _email()
    await client.post(
        "/api/auth/signup",
        json={
            "first_name": "A",
            "last_name": "B",
            "email": email,
            "password": "secretpass",
        },
    )
    factory = get_session_factory()
    async with factory() as session:
        user = await session.scalar(select(User).where(User.email == email))
        assert user
        user.is_active = False
        await session.commit()
    me = await client.get("/api/auth/me")
    assert me.json()["authenticated"] is False
    client.cookies.clear()
    res = await client.post(
        "/api/auth/signin",
        json={"email": email, "password": "secretpass"},
    )
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_14_protected_without_auth(client: AsyncClient) -> None:
    client.cookies.clear()
    res = await client.post("/api/chat", json={"message": "hello"})
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_15_protected_with_auth(client: AsyncClient) -> None:
    email = _email()
    await client.post(
        "/api/auth/signup",
        json={
            "first_name": "A",
            "last_name": "B",
            "email": email,
            "password": "secretpass",
        },
    )
    # Authenticated — may still fail upstream LLM, but must not be 401
    res = await client.post("/api/chat", json={"message": "hello"})
    assert res.status_code != 401


@pytest.mark.asyncio
async def test_cookie_flags_http_only(client: AsyncClient) -> None:
    email = _email()
    res = await client.post(
        "/api/auth/signup",
        json={
            "first_name": "A",
            "last_name": "B",
            "email": email,
            "password": "secretpass",
        },
    )
    # httpx exposes set-cookie header
    set_cookie = res.headers.get("set-cookie", "")
    assert "HttpOnly" in set_cookie or "httponly" in set_cookie.lower()
    assert "SameSite=lax" in set_cookie or "samesite=lax" in set_cookie.lower()
