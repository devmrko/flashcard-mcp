"""flashcard serve | init-db | check-db | new-token"""

import argparse
import secrets
import sys

from sqlalchemy import func, select, text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="flashcard")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="run the web + MCP server")
    sub.add_parser("init-db", help="create tables (idempotent)")
    sub.add_parser("check-db", help="connect and count cards")
    sub.add_parser("new-token", help="print a random MCP path token")
    args = parser.parse_args(argv)

    if args.cmd == "new-token":
        print(secrets.token_urlsafe(32))
        return 0

    from flashcard.config import Settings
    from flashcard.db import cards, make_engine, metadata

    settings = Settings()
    if args.cmd == "serve":
        import uvicorn

        from flashcard.web import create_app

        uvicorn.run(
            create_app(settings), host=settings.host, port=settings.port, log_level="info", access_log=False
        )
        return 0

    engine = make_engine(settings)
    if args.cmd == "init-db":
        metadata.create_all(engine)
        print("tables ready: fc_card")
        return 0
    with engine.connect() as conn:
        conn.execute(text("SELECT 1 FROM dual" if engine.dialect.name == "oracle" else "SELECT 1"))
        count = conn.execute(select(func.count()).select_from(cards)).scalar_one()
    print(f"ok: {engine.dialect.name}, fc_card rows = {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
