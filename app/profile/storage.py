"""Local filesystem storage for profile avatars (POC).

Stores only files on disk; PostgreSQL keeps a public API reference path.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import UploadFile

# backend/data/uploads/avatars
_UPLOAD_ROOT = Path(__file__).resolve().parents[2] / "data" / "uploads" / "avatars"

ALLOWED_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
MAX_BYTES = 2 * 1024 * 1024  # 2 MB


class AvatarStorageError(Exception):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def ensure_upload_dir() -> Path:
    _UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    return _UPLOAD_ROOT


def public_avatar_path(stored_name: str) -> str:
    """Opaque API path — never expose absolute filesystem paths."""
    return f"/api/profile/avatar/file/{stored_name}"


def stored_name_from_url(url: str | None) -> str | None:
    if not url:
        return None
    prefix = "/api/profile/avatar/file/"
    if not url.startswith(prefix):
        return None
    name = url[len(prefix) :].strip()
    if not name or "/" in name or ".." in name:
        return None
    return name


def resolve_avatar_file(stored_name: str) -> Path | None:
    ensure_upload_dir()
    path = (_UPLOAD_ROOT / stored_name).resolve()
    try:
        path.relative_to(_UPLOAD_ROOT.resolve())
    except ValueError:
        return None
    if not path.is_file():
        return None
    return path


async def save_avatar(upload: UploadFile, *, user_id: str) -> str:
    """Validate and store an avatar; return public API URL path."""
    content_type = (upload.content_type or "").split(";")[0].strip().lower()
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise AvatarStorageError("Please upload a JPEG, PNG, or WebP image.")

    filename = (upload.filename or "").lower()
    ext = ALLOWED_CONTENT_TYPES[content_type]
    if filename:
        if not (
            filename.endswith(".jpg")
            or filename.endswith(".jpeg")
            or filename.endswith(".png")
            or filename.endswith(".webp")
        ):
            raise AvatarStorageError("Unsupported file extension.")

    data = await upload.read()
    if not data:
        raise AvatarStorageError("The uploaded file is empty.")
    if len(data) > MAX_BYTES:
        raise AvatarStorageError("Image must be 2 MB or smaller.")

    # Light magic-byte checks
    if content_type == "image/jpeg" and not data.startswith(b"\xff\xd8"):
        raise AvatarStorageError("Invalid JPEG image.")
    if content_type == "image/png" and not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise AvatarStorageError("Invalid PNG image.")
    if content_type == "image/webp" and not (
        len(data) >= 12 and data[0:4] == b"RIFF" and data[8:12] == b"WEBP"
    ):
        raise AvatarStorageError("Invalid WebP image.")

    ensure_upload_dir()
    # Namespace by user id prefix for ownership checks without DB join on disk
    stored = f"{user_id}_{uuid.uuid4().hex}{ext}"
    path = _UPLOAD_ROOT / stored
    path.write_bytes(data)
    return public_avatar_path(stored)


def delete_avatar_file(url: str | None) -> None:
    name = stored_name_from_url(url)
    if not name:
        return
    path = resolve_avatar_file(name)
    if path and path.is_file():
        path.unlink(missing_ok=True)


def avatar_belongs_to_user(stored_name: str, user_id: str) -> bool:
    return stored_name.startswith(f"{user_id}_")
