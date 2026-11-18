"""FastAPI app: mobile review UI (HTTP Basic) + MCP endpoint at /m/<token>/mcp."""

import base64
import contextlib
import secrets
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import quote, urlsplit

import mobile_foundation
from fastapi import APIRouter, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import Engine
from starlette.types import ASGIApp, Receive, Scope, Send

from flashcard import srs
from flashcard.config import Settings
from flashcard.db import make_engine
from flashcard.mcp_server import CLIENT_SCOPE_KEY, build_mcp
from flashcard.store import CardError, CardStore, deck_label, normalize_deck, now_utc

TEMPLATES = Path(__file__).parent / "templates"
STATIC = Path(__file__).parent / "static"
RATING_LABELS = {"again": "모름", "hard": "애매", "good": "앎"}


def _interval_text(due_seconds: float) -> str:
    minutes = due_seconds / 60
    if minutes < 60:
        return f"{round(minutes)}분"
    days = minutes / 1440
    if days < 1:
        return f"{round(minutes / 60)}시간"
    return f"{round(days)}일" if days < 60 else f"{round(days / 30)}개월"


def _tag_client(app: ASGIApp, label: str) -> ASGIApp:
    """Mount wrapper: records which client token the request used; MCP tools read it as created_by."""

    async def tagged(scope: Scope, receive: Receive, send: Send) -> None:
        await app({**scope, CLIENT_SCOPE_KEY: label}, receive, send)

    return tagged


def _web_user(request: Request) -> str:
    header = request.headers.get("authorization", "")
    try:
        user = base64.b64decode(header[6:]).decode().partition(":")[0] if header else ""
    except ValueError:
        user = ""
    return f"web:{user or 'local'}"


def create_app(settings: Settings, engine: Engine | None = None) -> FastAPI:
    store = CardStore(engine or make_engine(settings))
    mcp = build_mcp(store, settings.public_host)
    mcp_app = mcp.streamable_http_app()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with mcp.session_manager.run():
            yield
        store.engine.dispose()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    templates = Jinja2Templates(directory=TEMPLATES)
    mobile_foundation.install(app, templates)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    templates.env.globals["bool"] = bool

    auth_value = settings.web_auth.get_secret_value()

    def require_auth(request: Request) -> None:
        if auth_value:
            header = request.headers.get("authorization", "")
            given = ""
            if header.lower().startswith("basic "):
                try:
                    given = base64.b64decode(header[6:]).decode()
                except ValueError:
                    given = ""
            if not secrets.compare_digest(given.encode(), auth_value.encode()):
                raise HTTPException(401, headers={"WWW-Authenticate": 'Basic realm="flashcard"'})
        if request.method == "POST":
            origin = request.headers.get("origin")
            if origin and origin != "null" and urlsplit(origin).netloc != request.headers.get("host"):
                raise HTTPException(403, "cross-origin form post")

    web = APIRouter(dependencies=[Depends(require_auth)])

    def render(request: Request, name: str, status: int = 200, **ctx: object) -> HTMLResponse:
        return templates.TemplateResponse(request, name, ctx, status_code=status)

    def deck_or_404(deck: str) -> str:
        try:
            return normalize_deck(deck)
        except CardError as exc:
            raise HTTPException(404) from exc

    @web.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        return render(request, "home.html", decks=store.decks())

    @web.get("/d/{deck}", response_class=HTMLResponse)
    def deck_page(request: Request, deck: str, q: str = "", error: str = "") -> HTMLResponse:
        deck = deck_or_404(deck)
        _, due = store.next_due(deck)
        found = store.search(q, deck, 100)
        return render(
            request, "deck.html", deck=deck, label=deck_label(deck), due=due, cards=found, q=q, error=error
        )

    @web.post("/d/{deck}/add")
    def add_card(
        request: Request,
        deck: str,
        front: str = Form(""),
        back: str = Form(""),
        example: str = Form(""),
        note: str = Form(""),
        new_deck: str = Form(""),
    ) -> Response:
        try:
            target = normalize_deck(new_deck) if new_deck.strip() else deck_or_404(deck)
            card, _ = store.add(target, front, back, example, note, source="web", by=_web_user(request))
        except CardError as exc:
            return RedirectResponse(f"/d/{quote(deck)}?error={quote(str(exc))}#add", 303)
        return RedirectResponse(f"/c/{card.id}?saved=1", 303)

    @web.get("/d/{deck}/review", response_class=HTMLResponse)
    def review_page(request: Request, deck: str) -> HTMLResponse:
        deck = deck_or_404(deck)
        card, due = store.next_due(deck)
        choices = []
        if card:
            now = now_utc()
            state = srs.State(card.ease, card.interval_days, card.reps, card.lapses)
            for rating in srs.RATINGS:
                _, when = srs.schedule(state, rating, now)
                choices.append(
                    {
                        "rating": rating,
                        "label": RATING_LABELS[rating],
                        "after": _interval_text((when - now).total_seconds()),
                    }
                )
        return render(
            request, "review.html", deck=deck, label=deck_label(deck), card=card, due=due, choices=choices
        )

    @web.post("/c/{card_id}/review")
    def review_card(card_id: int, rating: str = Form(...)) -> Response:
        try:
            card = store.review(card_id, rating)  # type: ignore[arg-type]
        except CardError as exc:
            raise HTTPException(400, str(exc)) from exc
        return RedirectResponse(f"/d/{card.deck}/review", 303)

    @web.get("/c/{card_id}", response_class=HTMLResponse)
    def card_page(request: Request, card_id: int, saved: str = "", error: str = "") -> HTMLResponse:
        card = store.get(card_id)
        if card is None:
            raise HTTPException(404)
        return render(
            request, "card.html", card=card, label=deck_label(card.deck), saved=bool(saved), error=error
        )

    @web.post("/c/{card_id}")
    def edit_card(
        request: Request,
        card_id: int,
        deck: str = Form(...),
        front: str = Form(...),
        back: str = Form(...),
        example: str = Form(""),
        note: str = Form(""),
    ) -> Response:
        try:
            store.edit(
                card_id, by=_web_user(request), deck=deck, front=front, back=back, example=example, note=note
            )
        except CardError as exc:
            return RedirectResponse(f"/c/{card_id}?error={quote(str(exc))}", 303)
        return RedirectResponse(f"/c/{card_id}?saved=1", 303)

    @web.post("/c/{card_id}/delete")
    def delete_card(card_id: int) -> Response:
        card = store.get(card_id)
        if card is None:
            raise HTTPException(404)
        store.remove(card_id)
        return RedirectResponse(f"/d/{card.deck}", 303)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(web)
    tokens = settings.client_tokens or ({"local": "local"} if settings.local_only else {})
    for token, label in tokens.items():
        app.mount(f"/m/{token}", _tag_client(mcp_app, label))
    app.state.store = store
    return app
