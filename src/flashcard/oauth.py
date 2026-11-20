"""OAuth authorization server for MCP clients; the person is checked by Google sign-in.

MCP clients register themselves (RFC 7591) and run the authorization-code + PKCE flow against this
app. Google does not accept dynamic registration, so this app issues its own tokens and only
delegates "who is this person" to Google. Design: docs/design/0002-mcp-oauth.md.
"""

import hashlib
import re
import secrets
import time
from dataclasses import dataclass, field

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from sqlalchemy import Engine, delete, func, insert, select

from flashcard import auth as signin
from flashcard.db import oauth_clients, oauth_tokens
from flashcard.store import now_utc

ACCESS_SECONDS = 3600
REFRESH_SECONDS = 30 * 86400
PENDING_SECONDS = 600  # time to finish the Google sign-in
CODE_SECONDS = 300  # time for the client to swap our code for tokens
# Registration and /authorize need no sign-in, so cap what strangers can make us store.
MAX_CLIENTS = 200
MAX_PENDING = 200


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def client_label(name: str | None) -> str:
    """Short lowercase name for created_by, e.g. "Claude Code" -> "claude-code"."""
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug[:20] or "client"


@dataclass
class _Pending:
    client_id: str
    params: AuthorizationParams
    nonce: str
    expires_at: float


@dataclass
class GoogleOAuthProvider:
    """OAuthAuthorizationServerProvider backed by the app DB, with Google as the sign-in step."""

    engine: Engine
    google_client_id: str
    google_client_secret: str
    allowed: set[str]
    callback_url: str  # this app's /auth/callback, registered in the Google console
    pending: dict[str, _Pending] = field(default_factory=dict)  # google state -> pending sign-in
    codes: dict[str, AuthorizationCode] = field(default_factory=dict)

    # --- clients (dynamic registration) ---------------------------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        with self.engine.connect() as conn:
            info = conn.execute(
                select(oauth_clients.c.info).where(oauth_clients.c.client_id == client_id)
            ).scalar_one_or_none()
        return OAuthClientInformationFull.model_validate_json(info) if info else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        # RegistrationError is a frozen dataclass: raise it outside any `with` block, because
        # contextlib re-raising assigns __traceback__ and fails on frozen instances.
        with self.engine.connect() as conn:
            count = conn.execute(select(func.count()).select_from(oauth_clients)).scalar_one()
        if count >= MAX_CLIENTS:
            raise RegistrationError("invalid_client_metadata", "too many registered clients")
        with self.engine.begin() as conn:
            conn.execute(
                insert(oauth_clients).values(
                    client_id=client_info.client_id,
                    info=client_info.model_dump_json(exclude_none=True),
                    created_at=now_utc(),
                )
            )

    # --- authorization: hand the person to Google, then back to the client --------------------

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        self._expire()
        if len(self.pending) >= MAX_PENDING:
            raise AuthorizeError("temporarily_unavailable", "too many sign-ins in progress, try again later")
        state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        self.pending[state] = _Pending(str(client.client_id), params, nonce, time.time() + PENDING_SECONDS)
        return signin.authorize_url(self.google_client_id, self.callback_url, state, nonce)

    def is_pending(self, state: str) -> bool:
        return state in self.pending

    def finish_google_sign_in(self, state: str, code: str) -> str:
        """Called from /auth/callback. Returns the client redirect URL carrying our code.

        Raises signin.SignInError (shown to the person) when Google sign-in or the allow list fails.
        """
        self._expire()
        pending = self.pending.pop(state, None)
        if pending is None:
            raise signin.SignInError("로그인 요청이 만료되었습니다. AI 앱에서 다시 연결하세요.")
        claims = signin.exchange_code(
            code, self.google_client_id, self.google_client_secret, self.callback_url
        )
        email = signin.check_claims(claims, self.google_client_id, pending.nonce, self.allowed)
        params = pending.params
        our_code = secrets.token_urlsafe(32)
        self.codes[our_code] = AuthorizationCode(
            code=our_code,
            scopes=params.scopes or [],
            expires_at=time.time() + CODE_SECONDS,
            client_id=pending.client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
            subject=email,
        )
        return construct_redirect_uri(str(params.redirect_uri), code=our_code, state=params.state)

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        self._expire()
        found = self.codes.get(authorization_code)
        return found if found and found.client_id == client.client_id else None

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        if self.codes.pop(authorization_code.code, None) is None:  # one use only
            raise TokenError("invalid_grant", "authorization code already used")
        return self._issue(
            str(client.client_id),
            authorization_code.subject or "",
            authorization_code.scopes,
            authorization_code.resource,
        )

    # --- tokens -----------------------------------------------------------------------------

    def _issue(self, client_id: str, subject: str, scopes: list[str], resource: str | None) -> OAuthToken:
        access, refresh, pair = (secrets.token_urlsafe(32) for _ in range(3))
        now = int(time.time())
        common = {
            "pair_id": pair,
            "client_id": client_id,
            "subject": subject,
            "scopes": " ".join(scopes),
            "resource": resource,
        }
        with self.engine.begin() as conn:
            conn.execute(
                insert(oauth_tokens),
                [
                    {
                        **common,
                        "token_hash": _hash(access),
                        "kind": "access",
                        "expires_at": now + ACCESS_SECONDS,
                    },
                    {
                        **common,
                        "token_hash": _hash(refresh),
                        "kind": "refresh",
                        "expires_at": now + REFRESH_SECONDS,
                    },
                ],
            )
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=ACCESS_SECONDS,
            refresh_token=refresh,
            scope=" ".join(scopes) or None,
        )

    def _row(self, token: str, kind: str):
        with self.engine.connect() as conn:
            row = (
                conn.execute(
                    select(oauth_tokens).where(
                        oauth_tokens.c.token_hash == _hash(token), oauth_tokens.c.kind == kind
                    )
                )
                .mappings()
                .first()
            )
        if row is None or row["expires_at"] < time.time() or row["subject"] not in self.allowed:
            return None
        return row

    async def load_access_token(self, token: str) -> AccessToken | None:
        row = self._row(token, "access")
        if row is None:
            return None
        return AccessToken(
            token=token,
            client_id=row["client_id"],
            scopes=row["scopes"].split() if row["scopes"] else [],
            expires_at=row["expires_at"],
            resource=row["resource"],
            subject=row["subject"],
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        row = self._row(refresh_token, "refresh")
        if row is None or row["client_id"] != client.client_id:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=row["client_id"],
            scopes=row["scopes"].split() if row["scopes"] else [],
            expires_at=row["expires_at"],
            resource=row["resource"],
            subject=row["subject"],
        )

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        await self.revoke_token(refresh_token)  # rotate: the old access + refresh pair stops working
        return self._issue(str(client.client_id), refresh_token.subject or "", scopes, refresh_token.resource)

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        with self.engine.begin() as conn:
            pair = conn.execute(
                select(oauth_tokens.c.pair_id).where(oauth_tokens.c.token_hash == _hash(token.token))
            ).scalar_one_or_none()
            if pair:
                conn.execute(delete(oauth_tokens).where(oauth_tokens.c.pair_id == pair))
            conn.execute(delete(oauth_tokens).where(oauth_tokens.c.expires_at < int(time.time())))

    def client_name(self, client_id: str) -> str:
        with self.engine.connect() as conn:
            info = conn.execute(
                select(oauth_clients.c.info).where(oauth_clients.c.client_id == client_id)
            ).scalar_one_or_none()
        return client_label(
            OAuthClientInformationFull.model_validate_json(info).client_name if info else None
        )

    def _expire(self) -> None:
        now = time.time()
        for state in [s for s, p in self.pending.items() if p.expires_at < now]:
            del self.pending[state]
        for code in [c for c, a in self.codes.items() if a.expires_at < now]:
            del self.codes[code]
