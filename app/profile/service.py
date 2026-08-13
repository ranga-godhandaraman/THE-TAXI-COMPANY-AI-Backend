"""Profile domain service — validation + persistence."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

from fastapi import HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import AuthError, PublicUser, validate_name
from app.db import profile_repository as repo
from app.profile.storage import (
    AvatarStorageError,
    avatar_belongs_to_user,
    delete_avatar_file,
    resolve_avatar_file,
    save_avatar,
    stored_name_from_url,
)

PREFERRED_VEHICLES = frozenset(
    {
        "SEDAN",
        "SUV",
        "XL",
        "EXECUTIVE",
        "LUXURY_VAN",
        "BLACK_CAB",
    }
)

SPECIAL_REQUIREMENT_OPTIONS = frozenset(
    {
        "wheelchair_accessible",
        "child_seat",
        "extra_luggage",
        "other",
    }
)

_PHONE_RE = re.compile(r"^\+?[0-9\s().-]{7,20}$")
_DOB_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _public_avatar_url(stored_url: str | None) -> str | None:
    """Return the authenticated avatar endpoint when an image exists."""
    if not stored_url:
        return None
    return "/api/profile/avatar"


def profile_to_dict(
    *,
    user: PublicUser,
    profile: Any,
) -> dict[str, Any]:
    reqs = profile.special_requirements
    if reqs is not None and not isinstance(reqs, list):
        reqs = list(reqs) if reqs else []
    return {
        "first_name": user.first_name,
        "last_name": user.last_name,
        "email": user.email,
        "phone_number": profile.phone_number,
        "date_of_birth": profile.date_of_birth,
        "address_line_1": profile.address_line_1,
        "address_line_2": profile.address_line_2,
        "city": profile.city,
        "postcode": profile.postcode,
        "country": profile.country or "United Kingdom",
        "preferred_vehicle_type": profile.preferred_vehicle_type,
        "special_requirements": reqs or [],
        "profile_image_url": _public_avatar_url(profile.profile_image_url),
        "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
    }


def _clean_optional_str(value: str | None, *, max_len: int, field: str) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) > max_len:
        raise HTTPException(
            status_code=400, detail=f"{field} must be at most {max_len} characters."
        )
    return cleaned


def _validate_phone(value: str | None) -> str | None:
    cleaned = _clean_optional_str(value, max_len=40, field="Phone number")
    if cleaned is None:
        return None
    if not _PHONE_RE.match(cleaned):
        raise HTTPException(status_code=400, detail="Please enter a valid phone number.")
    digits = re.sub(r"\D", "", cleaned)
    if len(digits) < 7 or len(digits) > 15:
        raise HTTPException(status_code=400, detail="Please enter a valid phone number.")
    return cleaned


def _validate_dob(value: str | None) -> str | None:
    cleaned = _clean_optional_str(value, max_len=16, field="Date of birth")
    if cleaned is None:
        return None
    if not _DOB_RE.match(cleaned):
        raise HTTPException(
            status_code=400, detail="Date of birth must use YYYY-MM-DD format."
        )
    try:
        dob = date.fromisoformat(cleaned)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid date of birth.") from exc
    today = date.today()
    if dob > today:
        raise HTTPException(
            status_code=400, detail="Date of birth cannot be in the future."
        )
    if dob.year < 1900:
        raise HTTPException(status_code=400, detail="Please enter a valid date of birth.")
    return cleaned


def _validate_vehicle(value: str | None) -> str | None:
    cleaned = _clean_optional_str(value, max_len=64, field="Preferred vehicle")
    if cleaned is None:
        return None
    key = cleaned.strip().upper().replace(" ", "_").replace("-", "_")
    aliases = {
        "STANDARD": "SEDAN",
        "STANDARD_SEDAN": "SEDAN",
        "PREMIUM_SUV": "SUV",
        "7_SEATER": "XL",
        "EXECUTIVE_SEDAN": "EXECUTIVE",
    }
    key = aliases.get(key, key)
    if key not in PREFERRED_VEHICLES:
        raise HTTPException(
            status_code=400, detail="Unsupported preferred vehicle type."
        )
    return key


def _validate_requirements(value: list[str] | None) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise HTTPException(
            status_code=400, detail="Special requirements must be a list."
        )
    out: list[str] = []
    for item in value:
        key = str(item).strip().lower().replace(" ", "_")
        if key not in SPECIAL_REQUIREMENT_OPTIONS:
            raise HTTPException(
                status_code=400, detail=f"Unsupported special requirement: {item}"
            )
        if key not in out:
            out.append(key)
    return out


async def get_profile(db: AsyncSession, user: PublicUser) -> dict[str, Any]:
    profile = await repo.get_or_create_profile(db, user_id=user.id)
    await db.commit()
    return profile_to_dict(user=user, profile=profile)


async def update_profile(
    db: AsyncSession,
    user: PublicUser,
    payload: dict[str, Any],
) -> dict[str, Any]:
    try:
        first_name = validate_name(str(payload.get("first_name", "")), "first name")
        last_name = validate_name(str(payload.get("last_name", "")), "last name")
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None

    fields = {
        "phone_number": _validate_phone(payload.get("phone_number")),
        "date_of_birth": _validate_dob(payload.get("date_of_birth")),
        "address_line_1": _clean_optional_str(
            payload.get("address_line_1"), max_len=200, field="Address line 1"
        ),
        "address_line_2": _clean_optional_str(
            payload.get("address_line_2"), max_len=200, field="Address line 2"
        ),
        "city": _clean_optional_str(payload.get("city"), max_len=100, field="City"),
        "postcode": _clean_optional_str(
            payload.get("postcode"), max_len=32, field="Postcode"
        ),
        "country": _clean_optional_str(
            payload.get("country"), max_len=100, field="Country"
        )
        or "United Kingdom",
        "preferred_vehicle_type": _validate_vehicle(
            payload.get("preferred_vehicle_type")
        ),
        "special_requirements": _validate_requirements(
            payload.get("special_requirements")
        ),
    }

    updated_user = await repo.update_user_names(
        db, user_id=user.id, first_name=first_name, last_name=last_name
    )
    if updated_user is None:
        raise HTTPException(status_code=404, detail="User not found.")

    profile = await repo.get_or_create_profile(db, user_id=user.id)
    await repo.apply_profile_fields(profile, fields)
    await db.commit()
    await db.refresh(profile)

    public = PublicUser(
        id=updated_user.id,
        email=updated_user.email,
        first_name=updated_user.first_name,
        last_name=updated_user.last_name,
    )
    return profile_to_dict(user=public, profile=profile)


async def upload_avatar(
    db: AsyncSession, user: PublicUser, upload: UploadFile
) -> dict[str, Any]:
    profile = await repo.get_or_create_profile(db, user_id=user.id)
    old_url = profile.profile_image_url
    try:
        new_url = await save_avatar(upload, user_id=user.id)
    except AvatarStorageError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None

    profile.profile_image_url = new_url
    profile.updated_at = datetime.now(timezone.utc)
    await db.commit()
    delete_avatar_file(old_url)
    return {"profile_image_url": _public_avatar_url(new_url)}


async def remove_avatar(db: AsyncSession, user: PublicUser) -> dict[str, Any]:
    profile = await repo.get_or_create_profile(db, user_id=user.id)
    old_url = profile.profile_image_url
    profile.profile_image_url = None
    profile.updated_at = datetime.now(timezone.utc)
    await db.commit()
    delete_avatar_file(old_url)
    return {"profile_image_url": None}


async def resolve_owned_avatar_path(db: AsyncSession, user: PublicUser):
    profile = await repo.get_profile_row(db, user_id=user.id)
    if profile is None or not profile.profile_image_url:
        return None
    name = stored_name_from_url(profile.profile_image_url)
    if not name or not avatar_belongs_to_user(name, user.id):
        return None
    return resolve_avatar_file(name)


async def resolve_avatar_file_for_user(
    db: AsyncSession, user: PublicUser, stored_name: str
):
    if not avatar_belongs_to_user(stored_name, user.id):
        raise HTTPException(status_code=404, detail="Avatar not found.")
    profile = await repo.get_profile_row(db, user_id=user.id)
    if profile is None or not profile.profile_image_url:
        raise HTTPException(status_code=404, detail="Avatar not found.")
    expected = stored_name_from_url(profile.profile_image_url)
    if expected != stored_name:
        raise HTTPException(status_code=404, detail="Avatar not found.")
    path = resolve_avatar_file(stored_name)
    if path is None:
        raise HTTPException(status_code=404, detail="Avatar not found.")
    return path
