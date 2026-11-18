import pytest
from sqlalchemy import create_engine

from flashcard.config import Settings
from flashcard.db import metadata
from flashcard.store import CardStore

TOKEN = "t" * 40
CODEX_TOKEN = "c" * 40


@pytest.fixture
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'fc.db'}", connect_args={"check_same_thread": False})
    metadata.create_all(eng)
    return eng


@pytest.fixture
def store(engine):
    return CardStore(engine)


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'unused.db'}",
        web_auth="me:pw",
        mcp_tokens=f"claude={TOKEN}, codex={CODEX_TOKEN}",
        public_host="flashcard.example.com",
    )
