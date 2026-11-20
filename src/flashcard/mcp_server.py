"""MCP tools for saving and finding flashcards during a conversation."""

from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from flashcard.store import CardError, CardStore

INSTRUCTIONS = """\
Personal flashcard store. The user reviews these cards later on a phone (spaced repetition).

When to save: when the user asks, or when you correct the user's English, teach a natural expression,
or explain an IT/psychology concept worth remembering. Save quietly in the background of the
conversation; mention it in one short phrase at most.

Decks: "english" (expressions, corrections), "it" (IT terms, papers, patterns), "psychology"
(psychology, habits, health ideas). Other lowercase deck names (e.g. "aws-saa") are allowed.

Card format:
- english: the user reviews this deck Korean-first and must recall the English, so:
  front = the natural English expression or full sentence;
  back = what the user wanted to say, as ONE natural Korean sentence with no English words in it
  (it is the prompt shown first, so it must not give away the answer);
  example = another natural English example sentence;
  note = nuance/usage and what the user said before the correction (e.g. "전에 말한 표현: ...").
- it / psychology: front = the term or question; back = a short explanation (2-4 sentences, Korean or
  English as the user prefers); example = a concrete example or use case.

Call search_cards before saving if unsure whether a card exists. save_card never overwrites; use
update_card with the returned id to change an existing card.
"""


CLIENT_SCOPE_KEY = "flashcard.client"


def client_label(ctx: Context) -> str:
    """Label of the token the request came in on (set by the per-token mount in web.py)."""
    request = ctx.request_context.request
    label = request.scope.get(CLIENT_SCOPE_KEY) if request is not None else None
    return f"mcp:{label or 'unknown'}"


def build_mcp(store: CardStore, public_host: str) -> FastMCP:
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[
            *([public_host] if public_host else []),
            "127.0.0.1:*",
            "localhost:*",
            "127.0.0.1",
            "localhost",
        ],
        allowed_origins=[
            *([f"https://{public_host}"] if public_host else []),
            "https://claude.ai",
            "https://chatgpt.com",
            "http://127.0.0.1:*",
            "http://localhost:*",
        ],
    )
    mcp = FastMCP(
        "flashcard",
        instructions=INSTRUCTIONS,
        stateless_http=True,
        json_response=True,
        streamable_http_path="/mcp",
        transport_security=security,
    )

    @mcp.tool()
    def list_decks() -> list[dict[str, Any]]:
        """List decks with total card count and how many cards are due for review now."""
        return store.decks()

    @mcp.tool()
    def search_cards(query: str = "", deck: str | None = None, limit: int = 10) -> dict[str, Any]:
        """Find cards whose front, back or example contains the query (case-insensitive).
        Empty query returns the most recent cards. Optionally restrict to one deck."""
        try:
            found = store.search(query, deck, limit)
        except CardError as exc:
            return {"error": str(exc)}
        return {"count": len(found), "cards": [c.public() for c in found]}

    @mcp.tool()
    def save_card(
        ctx: Context,
        deck: str,
        front: str,
        back: str,
        example: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Save a new flashcard. If the deck already has a card with the same front, nothing is changed
        and status "exists" is returned with that card."""
        try:
            card, created = store.add(deck, front, back, example, note, source="mcp", by=client_label(ctx))
        except CardError as exc:
            return {"error": str(exc)}
        return {"status": "created" if created else "exists", "card": card.public()}

    @mcp.tool()
    def update_card(
        ctx: Context,
        card_id: int,
        deck: str | None = None,
        front: str | None = None,
        back: str | None = None,
        example: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Change fields of an existing card. Omitted fields stay as they are; an empty string clears
        example or note."""
        try:
            card = store.edit(
                card_id, by=client_label(ctx), deck=deck, front=front, back=back, example=example, note=note
            )
        except CardError as exc:
            return {"error": str(exc)}
        return {"status": "updated", "card": card.public()}

    return mcp
