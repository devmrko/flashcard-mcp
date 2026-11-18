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


def make_engine(settings: Settings) -> Engine:
    if settings.database_url:
        args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
        return create_engine(settings.database_url, connect_args=args, pool_pre_ping=True)
    missing = [
        name
        for name, value in {
            "FLASHCARD_DB_USER": settings.db_user,
            "FLASHCARD_DB_PASSWORD": settings.db_password.get_secret_value(),
            "FLASHCARD_DB_DSN": settings.db_dsn,
            "FLASHCARD_WALLET_DIR": settings.wallet_dir,
        }.items()
        if not value
    ]
    if missing:
        raise ValueError("Missing database configuration: " + ", ".join(missing))
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
