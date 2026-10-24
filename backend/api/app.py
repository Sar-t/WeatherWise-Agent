"""WeatherWise HTTP API (FastAPI). Run: uvicorn backend.api.app:app --port 8000

All routes are under /api/v1. Clients identify themselves with an anonymous X-Client-Id (a UUID the
frontend generates and stores); conversations are scoped to it. No login yet.
"""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.agent.schemas import AgentResponse, UserContext
from backend.api import schemas
from backend.api.agent_service import AgentService
from backend.api.dashboard import AmbiguousPlace, build_dashboard, find_location
from backend.api.limits import MinuteLimiter, day_start, seconds_to_next_day
from backend.api.repository import ConversationRepository, SqlConversationRepository
from backend.core.config import Settings, get_settings
from backend.core.logging import setup_logging
from backend.core.redact import install_log_redaction, redact
from backend.services.errors import LocationNotFoundError, WeatherWiseError

log = logging.getLogger("backend.api")
API = "/api/v1"
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


class ApiError(Exception):
    """An error with a user-safe message, returned in the one JSON error shape."""

    def __init__(self, status: int, code: str, message: str, details: dict | None = None,
                 headers: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.details, self.headers = status, code, message, details, headers


def _error(status: int, code: str, message: str, details: dict | None = None,
           headers: dict[str, str] | None = None) -> JSONResponse:
    body = schemas.ErrorResponse(error=schemas.ErrorBody(code=code, message=redact(message),
                                                         request_id=request_id_var.get(), details=details))
    return JSONResponse(status_code=status, content=body.model_dump(exclude_none=True), headers=headers)


def _app_version() -> str:
    try:
        return version("weatherwise")
    except PackageNotFoundError:
        return "0.0.0"


def guidelines_status() -> dict:
    """Warm up the embedding model and store; report whether the index has documents."""
    from backend.services import guidelines

    try:
        seconds = guidelines.warm_up()
        store, _ = guidelines._resources()
        documents = len({chunk.split("#")[0] for chunk in store.hashes()})
    except Exception as exc:  # start anyway; /health says why
        return {"loaded": False, "documents": 0, "detail": f"{type(exc).__name__}: {redact(str(exc))[:200]}"}
    detail = None if documents else "index is empty: run python -m ingestion.build_index"
    return {"loaded": documents > 0, "documents": documents, "detail": detail, "warm_up_s": round(seconds, 1)}


async def _real_agent(settings: Settings):
    """The LangGraph agent with a SQLite checkpointer, so follow-ups survive restarts."""
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    from backend.agent.graph import build_graph, state_serde
    from backend.api.agent_service import GraphAgentService

    Path(settings.checkpoint_db).parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(settings.checkpoint_db)
    saver = AsyncSqliteSaver(conn, serde=state_serde())
    await saver.setup()
    return GraphAgentService(build_graph(checkpointer=saver), saver), conn


def create_app(
    settings: Settings | None = None,
    agent: AgentService | None = None,
    repository: ConversationRepository | None = None,
    guidelines_probe: Callable[[], dict] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    """Build the app. Tests inject a fake agent, a temporary repository, a guidelines probe and a clock."""
    settings = settings or get_settings()
    install_log_redaction()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        repo = repository or SqlConversationRepository(settings.database_url)
        await repo.open()
        closers = [repo.close]
        app.state.repo = repo
        if agent is None:
            app.state.agent, conn = await _real_agent(settings)
            closers.append(conn.close)
        else:
            app.state.agent = agent
        probe = guidelines_probe or guidelines_status
        app.state.guidelines = await asyncio.to_thread(probe)
        app.state.database = True
        log.info("api ready", extra={"guidelines": app.state.guidelines, "agent": type(app.state.agent).__name__})
        try:
            yield
        finally:
            for close in reversed(closers):
                await close()

    app = FastAPI(title="WeatherWise API", version=_app_version(), lifespan=lifespan,
                  responses={400: {"model": schemas.ErrorResponse}, 429: {"model": schemas.ErrorResponse}})
    app.state.settings = settings
    app.state.chat_limiter = MinuteLimiter(settings.rate_limit_per_minute)
    app.state.dashboard_limiter = MinuteLimiter(settings.dashboard_rate_per_minute)
    app.state.clock = clock
    app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
                       allow_methods=["*"], allow_headers=["*"], expose_headers=["X-Request-ID", "Retry-After"])

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        token = request_id_var.set(request_id[:64])
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:  # handled here, so the error carries the request id
            log.error("unhandled error", extra={"error": f"{type(exc).__name__}: {exc}", "request_id": request_id[:64]})
            response = _error(500, "internal_error", "Something went wrong on our side. Please try again.")
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = request_id[:64]
        # The path only: query strings can carry coordinates and place names.
        log.info("request", extra={"request_id": request_id[:64], "method": request.method, "path": request.url.path,
                                   "status": response.status_code,
                                   "ms": round((time.perf_counter() - started) * 1000)})
        return response

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError) -> JSONResponse:
        return _error(exc.status, exc.code, exc.message, exc.details, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = sorted({".".join(str(p) for p in e.get("loc", ()) if p not in ("body", "query")) for e in exc.errors()})
        return _error(400, "bad_request", f"Invalid request: check {', '.join(f for f in fields if f) or 'the body'}.")

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return _error(exc.status_code, code, "Not found." if exc.status_code == 404 else "Request not allowed.")

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled error", extra={"error": f"{type(exc).__name__}: {exc}"})
        return _error(500, "internal_error", "Something went wrong on our side. Please try again.")

    register_routes(app)
    return app


# --- dependencies -------------------------------------------------------------------------------------


def client_id(x_client_id: str | None = Header(default=None, alias="X-Client-Id")) -> str:
    try:
        return str(uuid.UUID(x_client_id or ""))
    except ValueError:
        raise ApiError(400, "bad_request", "Send an anonymous X-Client-Id header (a UUID).") from None


def _ctx(body: schemas.ChatRequest) -> UserContext:
    """The user's context for this turn. Coordinates are rounded to 2 decimals (~1 km) right here, so
    no precise position is ever stored, checkpointed or logged."""
    tz = (body.timezone or "").strip()
    if tz:
        try:
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError):
            raise ApiError(400, "bad_request", "Unknown timezone; send an IANA name like Asia/Kolkata.") from None
    lat = round(body.location.lat, 2) if body.location else None
    lon = round(body.location.lon, 2) if body.location else None
    return UserContext(lat=lat, lon=lon, timezone=tz)


# --- routes --------------------------------------------------------------------------------------------


def register_routes(app: FastAPI) -> None:
    def repo(request: Request) -> ConversationRepository:
        return request.app.state.repo

    @app.get(f"{API}/health", response_model=schemas.HealthOut)
    async def health(request: Request) -> schemas.HealthOut:
        settings: Settings = request.app.state.settings
        guidelines = getattr(request.app.state, "guidelines", {"loaded": False, "documents": 0, "detail": "starting"})
        database = getattr(request.app.state, "database", False)
        return schemas.HealthOut(
            status="ok" if guidelines.get("loaded") and database else "degraded",
            version=_app_version(), database=database,
            guidelines={k: guidelines.get(k) for k in ("loaded", "documents", "detail")},
            models={"embedding": settings.embedding_model,
                    "extraction": settings.extraction_model or settings.llm_model,
                    "answer": settings.answer_model or settings.llm_model})

    # conversations

    @app.post(f"{API}/conversations", response_model=schemas.ConversationOut, status_code=201)
    async def create_conversation(request: Request, client: str = Depends(client_id)) -> schemas.ConversationOut:
        c = await repo(request).create(client)
        return schemas.ConversationOut(id=c.id, title=c.title, created_at=c.created_at, updated_at=c.updated_at)

    @app.get(f"{API}/conversations", response_model=list[schemas.ConversationOut])
    async def list_conversations(request: Request, client: str = Depends(client_id)) -> list[schemas.ConversationOut]:
        return [schemas.ConversationOut(id=c.id, title=c.title, created_at=c.created_at, updated_at=c.updated_at)
                for c in await repo(request).list_conversations(client)]

    @app.get(f"{API}/conversations/{{conversation_id}}", response_model=schemas.ConversationDetail)
    async def get_conversation(conversation_id: str, request: Request,
                               client: str = Depends(client_id)) -> schemas.ConversationDetail:
        c = await _owned(request, client, conversation_id)
        messages = [schemas.MessageOut(role=m.role, text=m.text, kind=m.kind, created_at=m.created_at,
                                       response=schemas.ChatResponse(**m.response) if m.response else None)
                    for m in await repo(request).messages(client, conversation_id)]
        return schemas.ConversationDetail(conversation=schemas.ConversationOut(
            id=c.id, title=c.title, created_at=c.created_at, updated_at=c.updated_at), messages=messages)

    @app.delete(f"{API}/conversations/{{conversation_id}}", status_code=204)
    async def delete_conversation(conversation_id: str, request: Request, client: str = Depends(client_id)) -> Response:
        await _owned(request, client, conversation_id)
        await repo(request).delete(client, conversation_id)
        await request.app.state.agent.delete_thread(conversation_id)
        return Response(status_code=204)

    # chat

    @app.post(f"{API}/chat", response_model=schemas.ChatResponse)
    async def chat(body: schemas.ChatRequest, request: Request, client: str = Depends(client_id)) -> schemas.ChatResponse:
        message, conversation, ctx = await _accept(request, client, body)
        response = await _run(request, client, conversation.id, message, ctx)
        return await _store(request, client, conversation.id, message, response)

    @app.post(f"{API}/chat/stream", responses={200: {"content": {"text/event-stream": {}}}})
    async def chat_stream(body: schemas.ChatRequest, request: Request, client: str = Depends(client_id)):
        """Server-Sent Events: `status`* -> `token`* -> `message` (the canonical response), or `error`.
        Clarifications and declines (no LLM) send `message` without tokens."""
        message, conversation, ctx = await _accept(request, client, body)
        request_id = request_id_var.get()
        queue: asyncio.Queue = asyncio.Queue()

        async def emit(event: str, text: str) -> None:
            await queue.put((event, {"text": text}))

        async def worker() -> None:
            token = request_id_var.set(request_id)
            try:
                response = await _run(request, client, conversation.id, message, ctx, emit)
                api = await _store(request, client, conversation.id, message, response)
                if response.kind == "error":
                    await queue.put(("error", {"code": "agent_error", "message": api.text, "request_id": request_id,
                                               "conversation_id": conversation.id}))
                else:
                    await queue.put(("message", api.model_dump(mode="json")))
            except Exception as exc:
                log.error("stream failed", extra={"error": f"{type(exc).__name__}: {exc}", "request_id": request_id})
                await queue.put(("error", {"code": "internal_error", "request_id": request_id,
                                           "message": "Something went wrong on our side. Please try again.",
                                           "conversation_id": conversation.id}))
            finally:
                request_id_var.reset(token)
                await queue.put(None)

        async def events() -> AsyncIterator[str]:
            task = asyncio.create_task(worker())
            try:
                while (item := await queue.get()) is not None:
                    event, data = item
                    yield f"event: {event}\ndata: {redact(json.dumps(data, ensure_ascii=False))}\n\n"
            finally:
                await task

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # dashboard

    @app.get(f"{API}/dashboard", response_model=schemas.DashboardOut,
             responses={409: {"model": schemas.ErrorResponse}})
    async def dashboard(request: Request, client: str = Depends(client_id),
                        lat: float | None = Query(default=None, ge=-90, le=90),
                        lon: float | None = Query(default=None, ge=-180, le=180),
                        place: str | None = Query(default=None, max_length=100)) -> schemas.DashboardOut:
        """Weather, air quality, alerts and an outdoor outlook for a place. No LLM; doesn't count
        toward the daily chat cap."""
        if not place and (lat is None or lon is None):
            raise ApiError(400, "bad_request", "Send lat and lon, or place.")
        if wait := request.app.state.dashboard_limiter.check(client):
            raise ApiError(429, "rate_limited", "Too many dashboard requests; please wait a moment.",
                           headers={"Retry-After": str(wait)})
        try:
            location = await find_location(lat, lon, place.strip() if place else None)
            now = request.app.state.clock() if request.app.state.clock else None
            return await build_dashboard(location, now)
        except AmbiguousPlace as exc:
            raise ApiError(409, "ambiguous_place", "Which place did you mean?",
                           details={"candidates": [schemas.candidate(c).model_dump() for c in exc.candidates]}) from None
        except LocationNotFoundError:
            raise ApiError(404, "not_found", "No place found with that name.") from None
        except WeatherWiseError as exc:
            log.warning("dashboard upstream error", extra={"error": type(exc).__name__})
            raise ApiError(502, "upstream_error", "Weather data is temporarily unavailable, please try again.") from None


async def _owned(request: Request, client: str, conversation_id: str):
    c = await request.app.state.repo.get(client, conversation_id)
    if c is None:  # someone else's, or none: same answer, so IDs can't be probed
        raise ApiError(404, "not_found", "Conversation not found.")
    return c


async def _accept(request: Request, client: str, body: schemas.ChatRequest):
    """Validate, rate-limit and find/create the conversation, before any agent work starts."""
    settings: Settings = request.app.state.settings
    message = body.message.strip()
    if not message or len(message) > settings.max_message_chars:
        raise ApiError(400, "bad_request", f"Message must be 1-{settings.max_message_chars} characters.")
    ctx = _ctx(body)
    repo: ConversationRepository = request.app.state.repo
    if await repo.counted_messages_since(client, day_start()) >= settings.daily_message_cap:
        raise ApiError(429, "daily_limit", f"You've reached today's limit of {settings.daily_message_cap} messages. "
                       "It resets at midnight (India time).", headers={"Retry-After": str(seconds_to_next_day())})
    if wait := request.app.state.chat_limiter.check(client):
        raise ApiError(429, "rate_limited", "You're sending messages too quickly; please wait a moment.",
                       headers={"Retry-After": str(wait)})
    if body.conversation_id:
        conversation = await _owned(request, client, body.conversation_id)
    else:
        conversation = await repo.create(client)
    return message, conversation, ctx


async def _run(request: Request, client: str, conversation_id: str, message: str, ctx: UserContext,
               emit: Any = None) -> AgentResponse:
    response = await request.app.state.agent.run(message, conversation_id, ctx, emit)
    log.info("chat turn", extra={"request_id": request_id_var.get(), "conversation_id": conversation_id,
                                 "kind": response.kind, "timings": response.debug.timings,
                                 "llm_calls": response.debug.llm_calls, "error": response.debug.error})
    return response


async def _store(request: Request, client: str, conversation_id: str, message: str,
                 response: AgentResponse) -> schemas.ChatResponse:
    api = schemas.chat_response(response, conversation_id)
    await request.app.state.repo.add_exchange(client, conversation_id, message, response.kind,
                                              api.model_dump(mode="json"))
    return api


def _make_app() -> FastAPI:
    setup_logging(get_settings().log_level)
    return create_app()


app = _make_app()  # cheap: nothing heavy happens until startup (lifespan)
