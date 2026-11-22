"""Google sign-in (OpenID Connect authorization-code flow).

Used by the web UI and, through oauth.GoogleOAuthProvider, by MCP clients when MCP_AUTH=google.
Spec: docs/spec/google-signin-mcp-connect.md.
"""

import base64
import json
import time
from urllib.parse import urlencode

import httpx

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
ISSUERS = {"https://accounts.google.com", "accounts.google.com"}


class SignInError(Exception):
    """Sign-in failed; the message is safe to show to the user."""


def authorize_url(client_id: str, redirect_uri: str, state: str, nonce: str) -> str:
    query = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email",
        "state": state,
        "nonce": nonce,
        "prompt": "select_account",
    }
    return f"{AUTHORIZE_URL}?{urlencode(query)}"


def exchange_code(code: str, client_id: str, client_secret: str, redirect_uri: str) -> dict:
    """Swap the authorization code for an ID token and return the token's claims.

    The ID token comes straight from Google's token endpoint over TLS, so per OpenID Connect
    Core 3.1.3.7 the TLS server check stands in for the signature check. The caller still
    checks issuer, audience, expiry and nonce (check_claims).
    """
    try:
        resp = httpx.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=10,
        )
    except httpx.HTTPError as exc:
        raise SignInError("Google에 연결하지 못했습니다. 잠시 뒤 다시 시도하세요.") from exc
    if resp.status_code != 200:
        raise SignInError("Google이 로그인 코드를 거부했습니다. 다시 로그인하세요.")
    token = resp.json().get("id_token", "")
    try:
        payload = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError) as exc:
        raise SignInError("Google 응답을 읽지 못했습니다.") from exc


def check_claims(
    claims: dict, client_id: str, nonce: str, allowed: set[str], now: float | None = None
) -> str:
    """Return the signed-in email if the ID token is for this app and the email is allowed."""
    now = time.time() if now is None else now
    if claims.get("iss") not in ISSUERS or claims.get("aud") != client_id:
        raise SignInError("이 앱에 발급된 로그인이 아닙니다.")
    if float(claims.get("exp", 0)) < now:
        raise SignInError("로그인이 만료되었습니다. 다시 로그인하세요.")
    if not nonce or claims.get("nonce") != nonce:
        raise SignInError("로그인 요청이 일치하지 않습니다. 다시 로그인하세요.")
    email = str(claims.get("email", "")).lower()
    if not email or claims.get("email_verified") is not True:
        raise SignInError("Google 계정의 이메일이 확인되지 않았습니다.")
    if email not in allowed:
        raise SignInError(f"{email} 계정은 이 앱을 쓸 수 없습니다. 관리자에게 허용 목록 추가를 요청하세요.")
    return email


def safe_next(target: str) -> str:
    """Only same-site relative paths, so the sign-in flow cannot redirect elsewhere."""
    if target.startswith("/") and not target.startswith(("//", "/\\")):
        return target
    return "/"
