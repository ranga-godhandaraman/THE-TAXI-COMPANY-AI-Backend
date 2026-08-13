"""Chat session history API — ownership and persistence."""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.auth.rate_limit import reset_rate_limits
from app.db.models import ChatMessage, ChatSession
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
    return f"chat_hist_{uuid.uuid4().hex[:12]}@example.com"


async def _signup(client: AsyncClient, email: str | None = None) -> str:
    email = email or _email()
    res = await client.post(
        "/api/auth/signup",
        json={
            "first_name": "Alex",
            "last_name": "Rider",
            "email": email,
            "password": "secretpass",
        },
    )
    assert res.status_code == 200, res.text
    return email


@pytest.mark.asyncio
async def test_create_list_and_message_session(client: AsyncClient) -> None:
    await _signup(client)

    created = await client.post("/api/chat/sessions")
    assert created.status_code == 200, created.text
    session = created.json()
    assert session["title"] == "New Booking"
    sid = session["id"]

    listed = await client.get("/api/chat/sessions")
    assert listed.status_code == 200
    assert any(s["id"] == sid for s in listed.json())

    # Persist user message + AI reply (pipeline may return clarification)
    msg = await client.post(
        f"/api/chat/sessions/{sid}/messages",
        json={"content": "I need a car from Heathrow to Westminster."},
    )
    assert msg.status_code == 200, msg.text
    body = msg.json()
    assert body["conversation_id"] == sid
    assert body["answer"]

    detail = await client.get(f"/api/chat/sessions/{sid}")
    assert detail.status_code == 200
    data = detail.json()
    assert data["id"] == sid
    assert "Heathrow" in data["title"] and "Westminster" in data["title"]
    assert len(data["messages"]) >= 2
    assert data["messages"][0]["role"] == "user"
    assert data["messages"][1]["role"] == "assistant"

    factory = get_session_factory()
    async with factory() as db:
        rows = (
            await db.scalars(
                select(ChatMessage)
                .where(ChatMessage.session_id == sid)
                .order_by(ChatMessage.created_at.asc())
            )
        ).all()
        assert len(rows) >= 2
        sess = await db.scalar(select(ChatSession).where(ChatSession.id == sid))
        assert sess is not None
        assert sess.updated_at >= sess.created_at


@pytest.mark.asyncio
async def test_second_session_keeps_first(client: AsyncClient) -> None:
    await _signup(client)
    a = (await client.post("/api/chat/sessions")).json()
    await client.post(
        f"/api/chat/sessions/{a['id']}/messages",
        json={"content": "How much from Didsbury to Birmingham for 3 people?"},
    )
    b = (await client.post("/api/chat/sessions")).json()
    assert a["id"] != b["id"]

    listed = (await client.get("/api/chat/sessions")).json()
    ids = {s["id"] for s in listed}
    assert a["id"] in ids and b["id"] in ids
    # Most recently updated first
    assert listed[0]["id"] in {a["id"], b["id"]}


@pytest.mark.asyncio
async def test_continue_same_session(client: AsyncClient) -> None:
    await _signup(client)
    sid = (await client.post("/api/chat/sessions")).json()["id"]
    await client.post(
        f"/api/chat/sessions/{sid}/messages",
        json={"content": "How much from Heathrow to Westminster?"},
    )
    await client.post(
        f"/api/chat/sessions/{sid}/messages",
        json={"content": "3"},
    )
    detail = (await client.get(f"/api/chat/sessions/{sid}")).json()
    assert len(detail["messages"]) >= 4
    assert all(m["role"] in {"user", "assistant"} for m in detail["messages"])


@pytest.mark.asyncio
async def test_cannot_access_other_users_session(client: AsyncClient) -> None:
    await _signup(client, _email())
    sid = (await client.post("/api/chat/sessions")).json()["id"]

    # New user (new cookies overwrite)
    client.cookies.clear()
    await _signup(client, _email())

    res = await client.get(f"/api/chat/sessions/{sid}")
    assert res.status_code == 404

    res2 = await client.post(
        f"/api/chat/sessions/{sid}/messages",
        json={"content": "hello there journey"},
    )
    assert res2.status_code == 404


@pytest.mark.asyncio
async def test_unauthenticated_rejected(client: AsyncClient) -> None:
    client.cookies.clear()
    assert (await client.get("/api/chat/sessions")).status_code == 401
    assert (await client.post("/api/chat/sessions")).status_code == 401
