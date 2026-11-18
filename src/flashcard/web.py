"""FastAPI app: mobile review UI (HTTP Basic) + MCP endpoint at /m/<token>/mcp."""

import base64
import contextlib
import secrets
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlsplit

import mobile_foundation
from fastapi import APIRouter, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import Engine
from starlette.types import ASGIApp, Receive, Scope, Send

from flashcard import srs, stats
from flashcard.config import Settings
from flashcard.db import make_engine
from flashcard.mcp_server import CLIENT_SCOPE_KEY, build_mcp
from flashcard.store import REVERSED_DECKS, Card, CardError, CardStore, deck_label, normalize_deck, now_utc

TEMPLATES = Path(__file__).parent / "templates"
STATIC = Path(__file__).parent / "static"
TABS = [
    {"key": "decks", "label": "덱", "href": "/", "icon": "book"},
    {"key": "browse", "label": "찾아보기", "href": "/browse", "icon": "search"},
    {"key": "stats", "label": "통계", "href": "/stats", "icon": "chart"},
]
RATING_LABELS = {"again": "다시", "hard": "어려움", "good": "알맞음", "easy": "쉬움"}
RATING_TONES = {"again": "bad", "hard": "warn", "good": "ok", "easy": "info"}


def card_status(card: Card, now: datetime) -> dict[str, str]:
    """Queue state shown as text + tone (colour is never the only signal)."""
    kind = srs.kind(srs.State(card.ease, card.interval_days, card.reps, card.lapses))
    if kind == "new":
        return {"text": "새 카드", "tone": "info"}
    if kind == "learn":
        return {"text": "다시 학습", "tone": "bad"}
    if card.due_at <= now:
        return {"text": "복습할 때", "tone": "ok"}
    return {"text": _interval_text((card.due_at - now).total_seconds()) + " 뒤", "tone": "muted"}


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

    def tab(request: Request, name: str, active: str, **ctx: object) -> HTMLResponse:
        return render(request, name, tabs=TABS, active=active, **ctx)

    @web.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        now = now_utc()
        today = stats.utc_start_of(stats.local_today(now))
        decks = store.decks()
        return tab(
            request,
            "home.html",
            "decks",
            decks=decks,
            due_total=sum(d["due"] for d in decks),
            today=len(store.reviews_since(today)),
        )

    @web.get("/d/{deck}", response_class=HTMLResponse)
    def deck_page(request: Request, deck: str) -> HTMLResponse:
        deck = deck_or_404(deck)
        return render(request, "deck.html", deck=deck, label=deck_label(deck), c=store.deck_counts(deck))

    @web.get("/d/{deck}/review", response_class=HTMLResponse)
    def review_page(request: Request, deck: str, show: int = 0) -> HTMLResponse:
        deck = deck_or_404(deck)
        card, _ = store.next_due(deck)
        counts = store.deck_counts(deck)
        choices = []
        shown = bool(card and show == card.id)
        if card and shown:
            now = now_utc()
            state = srs.State(card.ease, card.interval_days, card.reps, card.lapses)
            for rating in srs.RATINGS:
                _, when = srs.schedule(state, rating, now)
                choices.append(
                    {
                        "rating": rating,
                        "label": RATING_LABELS[rating],
                        "tone": RATING_TONES[rating],
                        "after": _interval_text((when - now).total_seconds()),
                    }
                )
        reversed_ = deck in REVERSED_DECKS
        kind = srs.kind(srs.State(card.ease, card.interval_days, card.reps, card.lapses)) if card else ""
        return render(
            request,
            "review.html",
            deck=deck,
            label=deck_label(deck),
            card=card,
            kind=kind,
            c=counts,
            shown=shown,
            choices=choices,
            prompt=(card.back if reversed_ else card.front) if card else "",
            answer=(card.front if reversed_ else card.back) if card else "",
            hint="영어로 말해 보세요" if reversed_ else "",
        )

    @web.post("/c/{card_id}/review")
    def review_card(request: Request, card_id: int, rating: str = Form(...)) -> Response:
        try:
            card = store.review(card_id, rating, by=_web_user(request))  # type: ignore[arg-type]
        except CardError as exc:
            raise HTTPException(400, str(exc)) from exc
        return RedirectResponse(f"/d/{card.deck}/review", 303)

    @web.get("/browse", response_class=HTMLResponse)
    def browse(request: Request, q: str = "", deck: str = "") -> HTMLResponse:
        deck = deck_or_404(deck) if deck else ""
        found = store.search(q, deck or None, 200)
        now = now_utc()
        rows = [{"card": c, "status": card_status(c, now)} for c in found]
        return tab(
            request,
            "browse.html",
            "browse",
            rows=rows,
            q=q,
            deck=deck,
            decks=store.decks(),
            deck_label=deck_label,
        )

    @web.get("/stats", response_class=HTMLResponse)
    def stats_page(request: Request) -> HTMLResponse:
        now = now_utc()
        since = stats.utc_start_of(stats.local_today(now) - timedelta(days=365))
        summary = stats.summarize(store.reviews_since(since), now, window=30)
        return tab(
            request,
            "stats.html",
            "stats",
            s=summary,
            lapsed=store.most_lapsed(10),
            deck_label=deck_label,
        )

    @web.get("/add", response_class=HTMLResponse)
    def add_page(request: Request, deck: str = "english", error: str = "") -> HTMLResponse:
        deck = deck_or_404(deck)
        return render(request, "add.html", deck=deck, decks=store.decks(), error=error)

    def _add(request: Request, deck: str, front: str, back: str, example: str, note: str) -> Response:
        try:
            card, created = store.add(deck, front, back, example, note, source="web", by=_web_user(request))
        except CardError as exc:
            return RedirectResponse(f"/add?deck={quote(deck)}&error={quote(str(exc))}", 303)
        return RedirectResponse(f"/c/{card.id}?saved={'1' if created else 'exists'}", 303)

    @web.post("/add")
    def add_card(
        request: Request,
        deck: str = Form("english"),
        new_deck: str = Form(""),
        front: str = Form(""),
        back: str = Form(""),
        example: str = Form(""),
        note: str = Form(""),
    ) -> Response:
        target = new_deck.strip() or deck
        try:
            target = normalize_deck(target)
        except CardError as exc:
            return RedirectResponse(f"/add?error={quote(str(exc))}", 303)
        return _add(request, target, front, back, example, note)

    @web.post("/d/{deck}/add")
    def add_card_to_deck(
        request: Request,
        deck: str,
        front: str = Form(""),
        back: str = Form(""),
        example: str = Form(""),
        note: str = Form(""),
        new_deck: str = Form(""),
    ) -> Response:
        return add_card(request, deck, new_deck, front, back, example, note)

    @web.get("/c/{card_id}", response_class=HTMLResponse)
    def card_page(request: Request, card_id: int, saved: str = "", error: str = "") -> HTMLResponse:
        card = store.get(card_id)
        if card is None:
            raise HTTPException(404)
        history = store.history(card_id, 30)
        return render(
            request,
            "card.html",
            card=card,
            label=deck_label(card.deck),
            state=card_status(card, now_utc()),
            history=history,
            to_local=stats.to_local,
            rating_labels=RATING_LABELS,
            rating_tones=RATING_TONES,
            saved=saved,
            error=error,
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
        return RedirectResponse(f"/browse?deck={card.deck}", 303)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(web)
    tokens = settings.client_tokens or ({"local": "local"} if settings.local_only else {})
    for token, label in tokens.items():
        app.mount(f"/m/{token}", _tag_client(mcp_app, label))
    app.state.store = store
    return app
