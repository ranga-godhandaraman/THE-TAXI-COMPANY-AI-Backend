"""Secure session token generation and hashing."""

from __future__ import annotations

import hashlib
import hmac
import secrets


def generate_session_token() -> str:
    """Cryptographically secure random token (raw — cookie only)."""
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    """One-way hash for DB storage (never store raw token)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tokens_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)
