"""Settings from environment / .env. The server refuses to start exposed without auth."""

import re
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FLASHCARD_", env_file=ROOT / ".env", extra="ignore")

    host: str = "127.0.0.1"
    port: int = 8810
    # Public domain behind the reverse proxy (e.g. flashcard.example.com). Empty: localhost only.
    public_host: str = ""

    # Storage: "sqlite" (one local file, default) or "oracle" (Oracle Autonomous DB, wallet thin mode).
    db: Literal["sqlite", "oracle"] = "sqlite"
    sqlite_path: str = "flashcard.db"  # relative paths are under the project folder
    # oracle only:
    db_user: str = ""
    db_password: SecretStr = SecretStr("")
    db_dsn: str = ""
    wallet_dir: str = ""
    wallet_password: SecretStr = SecretStr("")

    # Web sign-in: "simple" (HTTP Basic with web_auth) or "google" (Google sign-in, allow-listed emails).
    auth: Literal["simple", "google"] = "simple"
    web_auth: SecretStr = SecretStr("")  # simple: "user:pass"
    google_client_id: str = ""
    google_client_secret: SecretStr = SecretStr("")
    allowed_emails: str = ""  # google: comma-separated
    session_secret: SecretStr = SecretStr("")  # google: signs the session cookie, >= 32 chars
    # MCP sign-in: "token" (secret path /m/<token>/mcp) or "google" (OAuth at /mcp, Google sign-in;
    # needs auth=google). Path tokens below keep working in google mode when set.
    mcp_auth: Literal["token", "google"] = "token"
    # "label=token,label=token" - one token per client/account; the label is recorded as created_by.
    mcp_tokens: SecretStr = SecretStr("")

    @property
    def allowed_email_set(self) -> set[str]:
        return {e.strip().lower() for e in self.allowed_emails.split(",") if e.strip()}

    @property
    def base_url(self) -> str:
        """Public origin of this app (OAuth issuer, Google redirect)."""
        return f"https://{self.public_host}" if self.public_host else f"http://localhost:{self.port}"

    @property
    def sqlite_url(self) -> str:
        path = Path(self.sqlite_path).expanduser()
        return f"sqlite:///{path if path.is_absolute() else ROOT / path}"

    @property
    def local_only(self) -> bool:
        return self.host in {"127.0.0.1", "localhost", "::1"}

    @property
    def client_tokens(self) -> dict[str, str]:
        """token -> client label"""
        result: dict[str, str] = {}
        for item in self.mcp_tokens.get_secret_value().split(","):
            if item.strip():
                label, _, token = item.strip().partition("=")
                result[token.strip()] = label.strip()
        return result

    @model_validator(mode="after")
    def check(self) -> "Settings":
        items = [i.strip() for i in self.mcp_tokens.get_secret_value().split(",") if i.strip()]
        labels = set()
        for item in items:
            label, sep, token = item.partition("=")
            label, token = label.strip(), token.strip()
            if not sep or not LABEL_RE.match(label):
                raise ValueError("FLASHCARD_MCP_TOKENS entries must be label=token (label: a-z, 0-9, '-')")
            if len(token) < 32 or not token.replace("-", "").replace("_", "").isalnum():
                raise ValueError("each MCP token must be >= 32 URL-safe characters")
            labels.add(label)
        if len(labels) != len(items) or len(self.client_tokens) != len(items):
            raise ValueError("FLASHCARD_MCP_TOKENS labels and tokens must be unique")
        oracle_fields = {
            "FLASHCARD_DB_USER": self.db_user,
            "FLASHCARD_DB_PASSWORD": self.db_password.get_secret_value(),
            "FLASHCARD_DB_DSN": self.db_dsn,
            "FLASHCARD_WALLET_DIR": self.wallet_dir,
        }
        if self.db == "oracle":
            missing = [name for name, value in oracle_fields.items() if not value]
            if missing:
                raise ValueError("FLASHCARD_DB=oracle needs " + ", ".join(missing))
        elif self.db_dsn:
            # Guard against silently switching an Oracle install to an empty SQLite file.
            raise ValueError("FLASHCARD_DB_DSN is set but FLASHCARD_DB is sqlite: set FLASHCARD_DB=oracle")
        token = bool(items)
        auth = self.web_auth.get_secret_value()
        if auth and ":" not in auth:
            raise ValueError("FLASHCARD_WEB_AUTH must be user:pass")
        if self.auth == "google":
            missing = [
                name
                for name, value in {
                    "FLASHCARD_GOOGLE_CLIENT_ID": self.google_client_id,
                    "FLASHCARD_GOOGLE_CLIENT_SECRET": self.google_client_secret.get_secret_value(),
                    "FLASHCARD_ALLOWED_EMAILS": self.allowed_emails.strip(),
                    "FLASHCARD_SESSION_SECRET": self.session_secret.get_secret_value(),
                }.items()
                if not value
            ]
            if missing:
                raise ValueError("FLASHCARD_AUTH=google needs " + ", ".join(missing))
            if len(self.session_secret.get_secret_value()) < 32:
                raise ValueError("FLASHCARD_SESSION_SECRET must be >= 32 characters (flashcard new-token)")
        if self.mcp_auth == "google" and self.auth != "google":
            raise ValueError("FLASHCARD_MCP_AUTH=google needs FLASHCARD_AUTH=google (same Google client)")
        web_ok = self.auth == "google" or bool(auth)
        mcp_ok = self.mcp_auth == "google" or token
        if not self.local_only and (not web_ok or not mcp_ok):
            raise ValueError(
                "Binding beyond localhost requires web sign-in (FLASHCARD_WEB_AUTH or FLASHCARD_AUTH=google)"
                " and MCP sign-in (FLASHCARD_MCP_TOKENS or FLASHCARD_MCP_AUTH=google)"
            )
        return self
