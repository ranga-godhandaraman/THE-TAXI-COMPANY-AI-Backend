"""Authenticated user profile APIs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_user
from app.auth.service import PublicUser
from app.db.session import get_session as get_db_session
from app.profile import service as profile_service

router = APIRouter(prefix="/api/profile", tags=["profile"])


class ProfileUpdateRequest(BaseModel):
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    phone_number: str | None = None
    date_of_birth: str | None = None
    address_line_1: str | None = None
    address_line_2: str | None = None
    city: str | None = None
    postcode: str | None = None
    country: str | None = None
    preferred_vehicle_type: str | None = None
    special_requirements: list[str] | None = None


class ProfileResponse(BaseModel):
    first_name: str
    last_name: str
    email: str
    phone_number: str | None = None
    date_of_birth: str | None = None
    address_line_1: str | None = None
    address_line_2: str | None = None
    city: str | None = None
    postcode: str | None = None
    country: str | None = None
    preferred_vehicle_type: str | None = None
    special_requirements: list[str] = Field(default_factory=list)
    profile_image_url: str | None = None
    updated_at: str | None = None


class AvatarResponse(BaseModel):
    profile_image_url: str | None = None


@router.get("", response_model=ProfileResponse)
async def get_profile(
    user: PublicUser = Depends(require_user),
    db: AsyncSession = Depends(get_db_session),
) -> ProfileResponse:
    data = await profile_service.get_profile(db, user)
    return ProfileResponse(**data)


@router.patch("", response_model=ProfileResponse)
async def patch_profile(
    body: ProfileUpdateRequest,
    user: PublicUser = Depends(require_user),
    db: AsyncSession = Depends(get_db_session),
) -> ProfileResponse:
    data = await profile_service.update_profile(
        db, user, body.model_dump()
    )
    return ProfileResponse(**data)


@router.put("", response_model=ProfileResponse)
async def put_profile(
    body: ProfileUpdateRequest,
    user: PublicUser = Depends(require_user),
    db: AsyncSession = Depends(get_db_session),
) -> ProfileResponse:
    data = await profile_service.update_profile(
        db, user, body.model_dump()
    )
    return ProfileResponse(**data)


@router.post("/avatar", response_model=AvatarResponse)
async def upload_avatar(
    file: UploadFile = File(...),
    user: PublicUser = Depends(require_user),
    db: AsyncSession = Depends(get_db_session),
) -> AvatarResponse:
    data = await profile_service.upload_avatar(db, user, file)
    return AvatarResponse(**data)


@router.delete("/avatar", response_model=AvatarResponse)
async def delete_avatar(
    user: PublicUser = Depends(require_user),
    db: AsyncSession = Depends(get_db_session),
) -> AvatarResponse:
    data = await profile_service.remove_avatar(db, user)
    return AvatarResponse(**data)


@router.get("/avatar")
async def get_my_avatar(
    user: PublicUser = Depends(require_user),
    db: AsyncSession = Depends(get_db_session),
) -> Any:
    path = await profile_service.resolve_owned_avatar_path(db, user)
    if path is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Avatar not found.")
    media = "image/jpeg"
    suffix = path.suffix.lower()
    if suffix == ".png":
        media = "image/png"
    elif suffix == ".webp":
        media = "image/webp"
    return FileResponse(path, media_type=media, filename=path.name)


@router.get("/avatar/file/{stored_name}")
async def get_avatar_file(
    stored_name: str,
    user: PublicUser = Depends(require_user),
    db: AsyncSession = Depends(get_db_session),
) -> Any:
    path = await profile_service.resolve_avatar_file_for_user(
        db, user, stored_name
    )
    media = "image/jpeg"
    suffix = path.suffix.lower()
    if suffix == ".png":
        media = "image/png"
    elif suffix == ".webp":
        media = "image/webp"
    return FileResponse(path, media_type=media, filename=path.name)
