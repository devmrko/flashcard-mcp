"""Engine and table definition. Oracle uses the ADB wallet in thin mode (same as vector-knowledge)."""

from pathlib import Path
from typing import Any

from sqlalchemy import (
    Column,
    DateTime,
    Engine,
    Float,
    Identity,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
)

from flashcard.config import ROOT, Settings

metadata = MetaData()

cards = Table(
    "fc_card",
    metadata,
    Column("id", Integer, Identity(), primary_key=True),
    Column("deck", String(40), nullable=False),
    Column("front", String(500), nullable=False),
    Column("front_key", String(500), nullable=False),
    Column("back", String(2000), nullable=False),
    Column("example", String(2000)),
    Column("note", String(2000)),
    Column("source", String(20), nullable=False),
    Column("created_by", String(60)),
    Column("updated_by", String(60)),
    Column("ease", Float, nullable=False),
    Column("interval_days", Float, nullable=False),
    Column("reps", Integer, nullable=False),
    Column("lapses", Integer, nullable=False),
    Column("due_at", DateTime, nullable=False, index=True),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    UniqueConstraint("deck", "front_key", name="fc_card_deck_front_uk"),
)


# One row per review answer. Kept separately from fc_card (which only holds the current schedule)
# so practice history, streaks and accuracy can be computed later.
reviews = Table(
    "fc_review",
    metadata,
    Column("id", Integer, Identity(), primary_key=True),
    Column("card_id", Integer, nullable=False, index=True),
    Column("deck", String(40), nullable=False),
    Column("rating", String(10), nullable=False),
    Column("reviewed_at", DateTime, nullable=False, index=True),
    Column("interval_before", Float, nullable=False),
    Column("interval_after", Float, nullable=False),
    Column("ease_after", Float, nullable=False),
    Column("reviewed_by", String(60)),
)


# MCP OAuth (FLASHCARD_MCP_AUTH=google, docs/design/0002-mcp-oauth.md).
oauth_clients = Table(
    "fc_oauth_client",
    metadata,
    Column("client_id", String(64), primary_key=True),
    Column("info", Text, nullable=False),  # OAuthClientInformationFull as JSON
    Column("created_at", DateTime, nullable=False),
)

# Only the SHA-256 of each token is stored. An access token and its refresh token share pair_id.
oauth_tokens = Table(
    "fc_oauth_token",
    metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("kind", String(10), nullable=False),  # access | refresh
    Column("pair_id", String(64), nullable=False, index=True),
    Column("client_id", String(64), nullable=False),
    Column("subject", String(320), nullable=False),  # Google email
    Column("scopes", String(500), nullable=False),
    Column("resource", String(500)),
    Column("expires_at", Integer, nullable=False),  # unix seconds
)


def make_engine(settings: Settings) -> Engine:
    if settings.db == "sqlite":
        return create_engine(
            settings.sqlite_url, connect_args={"check_same_thread": False}, pool_pre_ping=True
        )
    wallet = Path(settings.wallet_dir).expanduser()
    if not wallet.is_absolute():
        wallet = ROOT / wallet
    if not (wallet / "tnsnames.ora").is_file():
        raise ValueError("FLASHCARD_WALLET_DIR must contain tnsnames.ora")
    args: dict[str, Any] = {
        "user": settings.db_user,
        "password": settings.db_password.get_secret_value(),
        "dsn": settings.db_dsn,
        "config_dir": str(wallet),
        "wallet_location": str(wallet),
        "tcp_connect_timeout": 10,
    }
    if settings.wallet_password.get_secret_value():
        args["wallet_password"] = settings.wallet_password.get_secret_value()
    return create_engine(
        "oracle+oracledb://",
        connect_args=args,
        pool_size=3,
        max_overflow=0,
        pool_pre_ping=True,
        hide_parameters=True,
    )
