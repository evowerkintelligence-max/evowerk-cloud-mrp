from __future__ import annotations

import uuid
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi_users import exceptions
from httpx_oauth.clients.google import GoogleOAuth2
from httpx_oauth.clients.microsoft import MicrosoftGraphOAuth2
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.auth import TokenResponse, _issue_tokens, _mfa_verify_rate_limit
from app.core.config import get_settings
from app.core.security import create_mfa_pending_token, decode_token, decrypt_secret, verify_totp
from app.core.users import UserManager, get_user_manager
from app.db.session import get_db
from app.middleware.audit import log_event

router = APIRouter(prefix="/auth", tags=["oauth"])
settings = get_settings()

google_client = GoogleOAuth2(settings.google_oauth_client_id, settings.google_oauth_client_secret)
microsoft_client = MicrosoftGraphOAuth2(settings.microsoft_oauth_client_id, settings.microsoft_oauth_client_secret)


async def _google_email_verified(access_token: str) -> bool:
    """Google's standard OIDC userinfo endpoint reports email_verified - this
    is the actual per-token verification signal the auto-link decision relies
    on (httpx-oauth's GoogleOAuth2.get_id_email() drops this field, so it's
    fetched separately here)."""
    async with httpx.AsyncClient() as http:
        resp = await http.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return bool(resp.json().get("email_verified", False))


async def _microsoft_email_verified(access_token: str) -> bool:
    """Microsoft Graph's /me endpoint has no per-request email_verified claim
    the way Google's userinfo endpoint does. Treating Microsoft OAuth logins
    as provider-verified by default (both work/school tenant provisioning and
    MSA signup already verify the email at account-creation time) - flagged
    here as a real asymmetry with the Google check above, not an oversight.
    Revisit if Microsoft's identity platform exposes a stronger signal."""
    return True


def _redirect_with_tokens(tokens: TokenResponse) -> RedirectResponse:
    query = urlencode({"access_token": tokens.access_token, "refresh_token": tokens.refresh_token})
    return RedirectResponse(url=f"{settings.frontend_base_url}/oauth/callback#{query}")


async def _handle_callback(
    *,
    oauth_name: str,
    code: str,
    client: GoogleOAuth2 | MicrosoftGraphOAuth2,
    email_verified_check,
    request: Request,
    db: AsyncSession,
    user_manager: UserManager,
) -> RedirectResponse:
    redirect_uri = str(request.url_for(f"{oauth_name}_callback"))
    token_data = await client.get_access_token(code, redirect_uri)
    access_token = token_data["access_token"]
    account_id, account_email = await client.get_id_email(access_token)

    # No self-service OAuth signup: accounts are provisioned by an org admin
    # (via /register or a future invite flow), not created on first OAuth
    # login. This also sidesteps oauth_callback()'s new-user path, which
    # would otherwise create a User with no org_id (NOT NULL in this schema).
    try:
        await user_manager.get_by_email(account_email)
    except exceptions.UserNotExists:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "No account found for this email. Contact your organization administrator.",
        ) from None

    verified = await email_verified_check(access_token)

    try:
        user = await user_manager.oauth_callback(
            oauth_name=oauth_name,
            access_token=access_token,
            account_id=account_id,
            account_email=account_email,
            expires_at=token_data.get("expires_in"),
            refresh_token=token_data.get("refresh_token"),
            request=request,
            associate_by_email=verified,
            is_verified_by_default=verified,
        )
    except exceptions.UserAlreadyExists:
        # associate_by_email=False (unverified email) and this email already
        # has an account - narrow catch so a genuine failure inside
        # oauth_callback() (DB error, etc.) surfaces as a 500 instead of
        # being silently downgraded to a misleading 400.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Could not sign in with this account") from None

    if user.mfa_enabled:
        pending_token = create_mfa_pending_token(str(user.id))
        query = urlencode({"mfa_pending_token": pending_token})
        return RedirectResponse(url=f"{settings.frontend_base_url}/oauth/callback#{query}")

    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account disabled")

    tokens = await _issue_tokens(db, user)
    await log_event(
        db,
        action="user.login",
        org_id=user.org_id,
        actor_user_id=user.id,
        ip_address=request.client.host if request.client else None,
    )
    await db.commit()
    return _redirect_with_tokens(tokens)


@router.get("/google/authorize")
async def google_authorize(request: Request) -> RedirectResponse:
    redirect_uri = str(request.url_for("google_callback"))
    auth_url = await google_client.get_authorization_url(redirect_uri, scope=["openid", "email", "profile"])
    return RedirectResponse(url=auth_url)


@router.get("/google/callback", name="google_callback")
async def google_callback(
    request: Request,
    code: str,
    db: AsyncSession = Depends(get_db),
    user_manager: UserManager = Depends(get_user_manager),
) -> RedirectResponse:
    return await _handle_callback(
        oauth_name="google",
        code=code,
        client=google_client,
        email_verified_check=_google_email_verified,
        request=request,
        db=db,
        user_manager=user_manager,
    )


@router.get("/microsoft/authorize")
async def microsoft_authorize(request: Request) -> RedirectResponse:
    redirect_uri = str(request.url_for("microsoft_callback"))
    auth_url = await microsoft_client.get_authorization_url(redirect_uri, scope=["openid", "email", "profile"])
    return RedirectResponse(url=auth_url)


@router.get("/microsoft/callback", name="microsoft_callback")
async def microsoft_callback(
    request: Request,
    code: str,
    db: AsyncSession = Depends(get_db),
    user_manager: UserManager = Depends(get_user_manager),
) -> RedirectResponse:
    return await _handle_callback(
        oauth_name="microsoft",
        code=code,
        client=microsoft_client,
        email_verified_check=_microsoft_email_verified,
        request=request,
        db=db,
        user_manager=user_manager,
    )


class OAuthMfaVerifyRequest(BaseModel):
    pending_token: str
    code: str


@router.post("/oauth/mfa-verify", response_model=TokenResponse, dependencies=[Depends(_mfa_verify_rate_limit)])
async def oauth_mfa_verify(
    body: OAuthMfaVerifyRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user_manager: UserManager = Depends(get_user_manager),
) -> TokenResponse:
    try:
        payload = decode_token(body.pending_token)
        if payload.get("type") != "mfa_pending":
            raise ValueError
        user_id = uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired code") from None

    try:
        user = await user_manager.get(user_id)
    except exceptions.UserNotExists:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired code") from None

    if not user.mfa_enabled or not verify_totp(decrypt_secret(user.mfa_secret or ""), body.code):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid MFA code")

    tokens = await _issue_tokens(db, user)
    await log_event(
        db,
        action="user.login",
        org_id=user.org_id,
        actor_user_id=user.id,
        ip_address=request.client.host if request.client else None,
    )
    await db.commit()
    return tokens
