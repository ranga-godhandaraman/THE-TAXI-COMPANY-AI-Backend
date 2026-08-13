"""Pydantic schemas for authentication endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SignupRequest(BaseModel):
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., min_length=3, max_length=320)
    password: str = Field(..., min_length=1, max_length=200)


class SigninRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=320)
    password: str = Field(..., min_length=1, max_length=200)


class UserOut(BaseModel):
    id: str
    first_name: str
    last_name: str
    email: str


class AuthUserResponse(BaseModel):
    user: UserOut


class MeResponse(BaseModel):
    authenticated: bool
    user: UserOut | None = None
