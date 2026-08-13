"""User profile API acceptance tests."""

from __future__ import annotations

import io
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.rate_limit import reset_rate_limits
from app.db.session import dispose_engine, require_neon_settings
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
    return f"profile_{uuid.uuid4().hex[:12]}@example.com"


async def _signup(client: AsyncClient) -> None:
    res = await client.post(
        "/api/auth/signup",
        json={
            "first_name": "Ranga",
            "last_name": "Member",
            "email": _email(),
            "password": "secretpass",
        },
    )
    assert res.status_code == 200, res.text


def _tiny_png() -> bytes:
    # 1x1 PNG
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )


@pytest.mark.asyncio
async def test_get_and_patch_profile(client: AsyncClient) -> None:
    await _signup(client)
    got = await client.get("/api/profile")
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["first_name"] == "Ranga"
    assert body["email"]
    assert body["country"] == "United Kingdom"
    assert body["profile_image_url"] is None

    patched = await client.patch(
        "/api/profile",
        json={
            "first_name": "James",
            "last_name": "Bond",
            "phone_number": "+44 7700 900123",
            "date_of_birth": "1990-05-01",
            "address_line_1": "1 Mayfair Place",
            "address_line_2": "",
            "city": "London",
            "postcode": "W1J 8AJ",
            "country": "United Kingdom",
            "preferred_vehicle_type": "EXECUTIVE",
            "special_requirements": ["wheelchair_accessible", "extra_luggage"],
        },
    )
    assert patched.status_code == 200, patched.text
    data = patched.json()
    assert data["first_name"] == "James"
    assert data["phone_number"] == "+44 7700 900123"
    assert data["preferred_vehicle_type"] == "EXECUTIVE"
    assert "wheelchair_accessible" in data["special_requirements"]


@pytest.mark.asyncio
async def test_rejects_future_dob(client: AsyncClient) -> None:
    await _signup(client)
    res = await client.patch(
        "/api/profile",
        json={
            "first_name": "A",
            "last_name": "B",
            "date_of_birth": "2999-01-01",
        },
    )
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_avatar_upload_and_ownership(client: AsyncClient) -> None:
    await _signup(client)
    png = _tiny_png()
    up = await client.post(
        "/api/profile/avatar",
        files={"file": ("avatar.png", io.BytesIO(png), "image/png")},
    )
    assert up.status_code == 200, up.text
    assert up.json()["profile_image_url"] == "/api/profile/avatar"

    mine = await client.get("/api/profile/avatar")
    assert mine.status_code == 200
    assert mine.headers["content-type"].startswith("image/")

    # Other user cannot use first user's opaque file name
    detail = (await client.get("/api/profile")).json()
    assert detail["profile_image_url"]

    client.cookies.clear()
    await _signup(client)
    # Direct file path guessing with foreign prefix should 404
    foreign = await client.get(
        f"/api/profile/avatar/file/not-a-real-user_{uuid.uuid4().hex}.png"
    )
    assert foreign.status_code == 404


@pytest.mark.asyncio
async def test_profile_requires_auth(client: AsyncClient) -> None:
    client.cookies.clear()
    assert (await client.get("/api/profile")).status_code == 401
