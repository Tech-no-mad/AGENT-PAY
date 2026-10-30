"""
Authentication routes — wraps Supabase Auth for sign-up, login, and user profile.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.models.schemas import AuthResponse, LoginRequest, SignUpRequest
from backend.services.supabase_client import get_supabase, get_user_profile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])
security = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> dict[str, Any]:
    """
    Extract and verify JWT from Authorization: Bearer <token>.
    Returns the Supabase user dictionary.
    """
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    sb = get_supabase()

    try:
        user_res = sb.auth.get_user(token)
        if not user_res or not user_res.user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired authentication token",
            )
        user = user_res.user
        return {
            "id": user.id,
            "email": user.email,
            "user_metadata": user.user_metadata or {},
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Auth verification failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Authentication failed: {str(exc)}",
        )


@router.post("/signup", response_model=AuthResponse)
async def signup(payload: SignUpRequest) -> AuthResponse:
    """Register a new user via Supabase Auth."""
    sb = get_supabase()
    try:
        res = sb.auth.sign_up({
            "email": payload.email,
            "password": payload.password,
            "options": {
                "data": {
                    "full_name": payload.full_name or payload.email.split("@")[0],
                }
            },
        })

        if not res.user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User creation failed",
            )

        access_token = res.session.access_token if res.session else "verification-required"
        refresh_token = res.session.refresh_token if res.session else None

        return AuthResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            user={
                "id": res.user.id,
                "email": res.user.email,
                "full_name": payload.full_name,
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Signup error: %s", exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/login", response_model=AuthResponse)
async def login(payload: LoginRequest) -> AuthResponse:
    """Log in an existing user via Supabase Auth."""
    sb = get_supabase()
    try:
        res = sb.auth.sign_in_with_password({
            "email": payload.email,
            "password": payload.password,
        })

        if not res.session or not res.user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )

        return AuthResponse(
            access_token=res.session.access_token,
            refresh_token=res.session.refresh_token,
            user={
                "id": res.user.id,
                "email": res.user.email,
                "full_name": res.user.user_metadata.get("full_name") if res.user.user_metadata else None,
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Login error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials or authentication error",
        )


@router.post("/logout")
async def logout(current_user: dict[str, Any] = Depends(get_current_user)) -> dict[str, str]:
    """Sign out the current user."""
    return {"message": "Successfully logged out"}


@router.get("/me")
async def get_me(current_user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """Get current user information and profile."""
    profile = get_user_profile(current_user["id"])
    return {
        "user": current_user,
        "profile": profile,
    }
