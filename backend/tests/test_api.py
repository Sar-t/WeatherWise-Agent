"""HTTP API: endpoints, client scoping, chat + SSE, limits, errors, CORS, health, privacy, dashboard.

A fake agent and a temporary SQLite database; no LLM, network or embedding model. The dashboard
runs the real services on recorded fixtures (respx) with fake geocoding.
"""

import json
import logging
import sqlite3
import uuid
from datetime import datetime, time, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.agent.schemas import AgentResponse, GuidelineSource
from backend.api.app import create_app
from backend.core.config import Settings
from backend.services import air_quality, geocoding, weather
from backend.services.cache import InMemoryCache
from backend.tests.agent_fakes import (
    ONECALL_BY_LAT_AQ,
    fixture_now,
    install_fake_geocoding,
    mock_air_quality,
    mock_onecall,
)

OW_KEY = "ow-secret-key-0123456789"
GOOGLE_KEY = "google-secret-key-9876543210"
CLIENT_A, CLIENT_B = str(uuid.uuid4()), str(uuid.uuid4())
AQ_NOW = fixture_now("delhi_aq")  # Thu 22 Oct 2026 12:00 IST


class FakeAgent:
    """Stands in for the graph: emits statuses and tokens, returns queued responses (or raises)."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.responses: list[AgentResponse] = []
        self.deleted: list[str] = []
        self.error: Exception | None = None

    async def run(self, message, conversation_id, ctx, emit=None) -> AgentResponse:
        self.calls.append((message, conversation_id, ctx))
        if self.error:
            raise self.error
        response = self.responses.pop(0) if self.responses else AgentResponse(text="It's sunny, 25°C.", kind="answer")
        if emit:
            for status in ("understanding", "finding location", "fetching weather", "writing answer"):
                await emit("status", status)
            if response.kind == "answer":
                for chunk in ("- It's ", "sunny, ", "25°C"):  # streamed raw; the final text is post-processed
                    await emit("token", chunk)
        return response

    async def delete_thread(self, conversation_id: str) -> None:
        self.deleted.append(conversation_id)


def settings(tmp_path: Path, **overrides) -> Settings:
    base = {"_env_file": None, "database_url": f"sqlite+aiosqlite:///{(tmp_path / 'api.db').as_posix()}",
            "openweather_api_key": OW_KEY, "google_api_key": GOOGLE_KEY, "geoapify_api_key": "geo-secret-123456789",
            "llm_model": "fake-model"}
    return Settings(**(base | overrides))


@pytest.fixture
def agent() -> FakeAgent:
    return FakeAgent()


@pytest.fixture
def make_client(tmp_path: Path, agent: FakeAgent, monkeypatch: pytest.MonkeyPatch):
    clients = []

    def make(probe=None, clock=None, **overrides) -> TestClient:
        s = settings(tmp_path, **overrides)
        monkeypatch.setattr("backend.core.config.get_settings", lambda: s)  # redaction knows the keys
        monkeypatch.setattr("backend.core.redact._secrets", lambda: [OW_KEY, GOOGLE_KEY, "geo-secret-123456789"])
        app = create_app(settings=s, agent=agent,
                         guidelines_probe=probe or (lambda: {"loaded": True, "documents": 16, "detail": None}),
                         clock=clock)
        client = TestClient(app, raise_server_exceptions=False)  # see the 500 response, as a browser would
        client.__enter__()
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client) -> TestClient:
    return make_client()


def h(client_id: str = CLIENT_A) -> dict:
    return {"X-Client-Id": client_id}


def chat(client: TestClient, message: str = "Will it rain in Jaipur?", client_id: str = CLIENT_A, **body):
    return client.post("/api/v1/chat", json={"message": message, **body}, headers=h(client_id))


def sse(response) -> list[tuple[str, dict]]:
    events, event = [], None
    for line in response.text.splitlines():
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: "):
            events.append((event, json.loads(line[6:])))
    return events


def assert_error(response, status: int, code: str) -> dict:
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error"} and body["error"]["code"] == code and body["error"]["message"]
    assert body["error"]["request_id"] == response.headers["X-Request-ID"]
    return body["error"]


# --- health ------------------------------------------------------------------------------------------


def test_health_reports_guidelines_models_and_version(client: TestClient) -> None:
    body = client.get("/api/v1/health").json()
    assert body["status"] == "ok" and body["database"] is True
    assert body["guidelines"] == {"loaded": True, "documents": 16, "detail": None}
    assert body["models"]["extraction"] == "fake-model" and "multilingual" in body["models"]["embedding"]
    assert body["version"]


def test_health_without_the_index_is_degraded_but_up(make_client) -> None:
    client = make_client(probe=lambda: {"loaded": False, "documents": 0,
                                        "detail": "index is empty: run python -m ingestion.build_index"})
    body = client.get("/api/v1/health").json()
    assert body["status"] == "degraded" and body["guidelines"]["loaded"] is False
    assert "build_index" in body["guidelines"]["detail"]
    assert chat(client).status_code == 200  # the agent still answers


# --- client id -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("headers", [{}, {"X-Client-Id": "not-a-uuid"}, {"X-Client-Id": ""}])
def test_missing_or_malformed_client_id_is_400(client: TestClient, headers: dict) -> None:
    assert_error(client.get("/api/v1/conversations", headers=headers), 400, "bad_request")
    assert_error(client.post("/api/v1/chat", json={"message": "hi"}, headers=headers), 400, "bad_request")


# --- conversations ------------------------------------------------------------------------------------------


def test_conversation_create_list_get_delete(client: TestClient, agent: FakeAgent) -> None:
    created = client.post("/api/v1/conversations", headers=h())
    assert created.status_code == 201
    cid = created.json()["id"]
    chat(client, "Will it rain in Jaipur tomorrow evening?", conversation_id=cid)

    [listed] = client.get("/api/v1/conversations", headers=h()).json()
    assert (listed["id"], listed["title"]) == (cid, "Will it rain in Jaipur tomorrow evening?")
    detail = client.get(f"/api/v1/conversations/{cid}", headers=h()).json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][1]["response"]["text"] == "It's sunny, 25°C."

    assert client.delete(f"/api/v1/conversations/{cid}", headers=h()).status_code == 204
    assert agent.deleted == [cid]  # the checkpoint thread goes too
    assert_error(client.get(f"/api/v1/conversations/{cid}", headers=h()), 404, "not_found")
    assert client.get("/api/v1/conversations", headers=h()).json() == []


def test_one_client_cannot_see_anothers_conversations(client: TestClient) -> None:
    cid = chat(client, client_id=CLIENT_A).json()["conversation_id"]
    assert client.get("/api/v1/conversations", headers=h(CLIENT_B)).json() == []
    assert_error(client.get(f"/api/v1/conversations/{cid}", headers=h(CLIENT_B)), 404, "not_found")
    assert_error(client.delete(f"/api/v1/conversations/{cid}", headers=h(CLIENT_B)), 404, "not_found")
    assert_error(chat(client, client_id=CLIENT_B, conversation_id=cid), 404, "not_found")
    assert len(client.get("/api/v1/conversations", headers=h(CLIENT_A)).json()) == 1  # untouched


def test_title_is_the_first_message_cut_to_60_characters(client: TestClient) -> None:
    long = "Is it a good idea to go cycling along the Marine Drive promenade in Mumbai this evening?"
    cid = chat(client, long).json()["conversation_id"]
    chat(client, "and tomorrow?", conversation_id=cid)
    [listed] = client.get("/api/v1/conversations", headers=h()).json()
    assert listed["title"] == long[:60].strip()


# --- chat ---------------------------------------------------------------------------------------------


def test_chat_creates_a_conversation_and_follow_ups_reuse_it(client: TestClient, agent: FakeAgent) -> None:
    first = chat(client, "Will it rain in Jaipur tomorrow?")
    assert first.status_code == 200
    body = first.json()
    cid = body["conversation_id"]
    assert (body["kind"], body["text"]) == ("answer", "It's sunny, 25°C.")
    assert set(body) == {"conversation_id", "kind", "text", "cards", "air_quality", "assessments", "sources"}

    second = chat(client, "and Delhi?", conversation_id=cid).json()
    assert second["conversation_id"] == cid
    assert [call[1] for call in agent.calls] == [cid, cid]  # same thread -> the graph's memory


def test_chat_returns_sources_and_shaped_cards(client: TestClient, agent: FakeAgent) -> None:
    agent.responses = [AgentResponse(text="CPCB advises care.", kind="answer", sources=[
        GuidelineSource(title="NAQI Poor", source_name="CPCB", source_url="https://example.gov.in/aqi")])]
    body = chat(client).json()
    assert body["sources"] == [{"title": "NAQI Poor", "source_name": "CPCB", "url": "https://example.gov.in/aqi"}]


def test_chat_unknown_conversation_is_404(client: TestClient) -> None:
    assert_error(chat(client, conversation_id=str(uuid.uuid4())), 404, "not_found")


@pytest.mark.parametrize("message", ["", "   ", "x" * 501])
def test_message_length_is_checked(client: TestClient, agent: FakeAgent, message: str) -> None:
    assert_error(chat(client, message), 400, "bad_request")
    assert agent.calls == []
    assert chat(client, "x" * 500).status_code == 200


def test_bad_timezone_and_body_are_400(client: TestClient) -> None:
    assert_error(chat(client, timezone="Mars/Olympus"), 400, "bad_request")
    assert_error(client.post("/api/v1/chat", json={"text": "hi"}, headers=h()), 400, "bad_request")
    assert_error(chat(client, location={"lat": 123, "lon": 0}), 400, "bad_request")


def test_agent_error_kind_is_200_and_kept_in_the_conversation(client: TestClient, agent: FakeAgent) -> None:
    agent.responses = [AgentResponse(text="The assistant is busy right now. Please try again in a minute.",
                                     kind="error")]
    body = chat(client).json()
    assert body["kind"] == "error" and "busy" in body["text"]


# --- streaming ----------------------------------------------------------------------------------------------


def test_stream_order_status_tokens_then_canonical_message(client: TestClient) -> None:
    with client.stream("POST", "/api/v1/chat/stream", json={"message": "Weather in Jaipur?"}, headers=h()) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        r.read()
    events = sse(r)
    kinds = [e for e, _ in events]
    assert kinds == ["status"] * 4 + ["token"] * 3 + ["message"]
    assert [d["text"] for e, d in events if e == "status"] == ["understanding", "finding location",
                                                                "fetching weather", "writing answer"]
    streamed = "".join(d["text"] for e, d in events if e == "token")
    final = events[-1][1]
    assert streamed == "- It's sunny, 25°C" and final["text"] == "It's sunny, 25°C."  # canonical replaces streamed
    assert final["kind"] == "answer" and final["conversation_id"]
    assert set(final) >= {"cards", "assessments", "air_quality", "sources"}


def test_clarification_streams_no_tokens(client: TestClient, agent: FakeAgent) -> None:
    agent.responses = [AgentResponse(text="Which city did you mean?", kind="clarification")]
    with client.stream("POST", "/api/v1/chat/stream", json={"message": "weather?"}, headers=h()) as r:
        r.read()
    kinds = [e for e, _ in sse(r)]
    assert "token" not in kinds and kinds[-1] == "message"


def test_stream_error_event_on_agent_failure(client: TestClient, agent: FakeAgent) -> None:
    agent.error = RuntimeError(f"boom, key {OW_KEY}")
    with client.stream("POST", "/api/v1/chat/stream", json={"message": "weather?"}, headers=h()) as r:
        r.read()
    events = sse(r)
    assert events[-1][0] == "error" and events[-1][1]["code"] == "internal_error"
    assert OW_KEY not in r.text and "boom" not in r.text


def test_stream_error_event_for_an_error_reply(client: TestClient, agent: FakeAgent) -> None:
    agent.responses = [AgentResponse(text="The assistant is busy right now. Please try again in a minute.",
                                     kind="error")]
    with client.stream("POST", "/api/v1/chat/stream", json={"message": "weather?"}, headers=h()) as r:
        r.read()
    event, data = sse(r)[-1]
    assert event == "error" and data["code"] == "agent_error" and "busy" in data["message"]


def test_stream_validation_errors_are_plain_http_errors(client: TestClient) -> None:
    assert_error(client.post("/api/v1/chat/stream", json={"message": "x" * 501}, headers=h()), 400, "bad_request")


# --- limits ----------------------------------------------------------------------------------------------


def test_per_minute_limit_is_429_with_retry_after(make_client) -> None:
    client = make_client(rate_limit_per_minute=2)
    assert chat(client).status_code == chat(client).status_code == 200
    error = assert_error(chat(client), 429, "rate_limited")
    assert "too quickly" in error["message"]
    third = chat(client)
    assert 1 <= int(third.headers["Retry-After"]) <= 60
    assert chat(client, client_id=CLIENT_B).status_code == 200  # per client


def test_daily_cap_is_429_and_error_turns_dont_count(make_client, agent: FakeAgent) -> None:
    client = make_client(daily_message_cap=2)
    agent.responses = [AgentResponse(text="busy", kind="error"), AgentResponse(text="busy", kind="error"),
                       AgentResponse(text="Which city?", kind="clarification"), AgentResponse(text="ok", kind="answer")]
    for _ in range(4):  # 2 error turns (not counted), then a clarification and an answer (counted)
        assert chat(client).status_code == 200
    error = assert_error(chat(client), 429, "daily_limit")
    assert "today's limit of 2" in error["message"]
    assert 0 < int(chat(client).headers["Retry-After"]) <= 24 * 3600


# --- errors, CORS, keys ---------------------------------------------------------------------------------------


def test_unknown_route_uses_the_error_shape(client: TestClient) -> None:
    assert_error(client.get("/api/v1/nothing-here"), 404, "not_found")


def test_unexpected_errors_are_500_without_details(client: TestClient, agent: FakeAgent) -> None:
    agent.error = RuntimeError(f"database exploded with key {OW_KEY}")
    response = chat(client)
    assert_error(response, 500, "internal_error")
    assert OW_KEY not in response.text and "exploded" not in response.text and "Traceback" not in response.text


def test_cors_allows_the_configured_origin_only(client: TestClient) -> None:
    preflight = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "x-client-id"}
    ok = client.options("/api/v1/chat", headers={"Origin": "http://localhost:3000", **preflight})
    assert ok.headers["access-control-allow-origin"] == "http://localhost:3000"
    other = client.options("/api/v1/chat", headers={"Origin": "https://evil.example", **preflight})
    assert "access-control-allow-origin" not in other.headers
    simple = client.get("/api/v1/health", headers={"Origin": "http://localhost:3000"})
    assert simple.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "X-Request-ID" in simple.headers["access-control-expose-headers"]


def test_responses_and_logs_never_contain_keys(client: TestClient, agent: FakeAgent,
                                                caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    agent.responses = [AgentResponse(text=f"leaked {OW_KEY} and {GOOGLE_KEY}", kind="answer")]
    with client.stream("POST", "/api/v1/chat/stream", json={"message": "weather?"}, headers=h()) as r:
        r.read()
    texts = [r.text, chat(client).text, client.get("/api/v1/health").text,
             client.get("/api/v1/conversations", headers=h()).text, caplog.text]
    for text in texts:
        assert OW_KEY not in text and GOOGLE_KEY not in text


def test_request_id_is_echoed(client: TestClient) -> None:
    response = client.get("/api/v1/health", headers={"X-Request-ID": "trace-123"})
    assert response.headers["X-Request-ID"] == "trace-123"


# --- privacy: coordinates ------------------------------------------------------------------------------------


PRECISE = {"lat": 26.912345, "lon": 75.787654}


def test_coordinates_are_rounded_before_the_agent_db_and_logs(client: TestClient, agent: FakeAgent,
                                                             caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    caplog.set_level(logging.DEBUG)
    chat(client, "Weather here?", location=PRECISE)
    with client.stream("POST", "/api/v1/chat/stream", json={"message": "and now?", "location": PRECISE},
                       headers=h()) as r:
        r.read()
    ctx = agent.calls[0][2]
    assert (ctx.lat, ctx.lon) == (26.91, 75.79)  # the agent only ever sees 2 decimals
    rows = sqlite3.connect(tmp_path / "api.db").execute("SELECT * FROM messages").fetchall()
    stored = json.dumps(rows) + caplog.text
    for digits in ("26.912", "75.787", "75.788"):
        assert digits not in stored


# --- dashboard --------------------------------------------------------------------------------------------------


@pytest.fixture
def recorded(respx_mock, monkeypatch: pytest.MonkeyPatch):
    install_fake_geocoding(monkeypatch)
    for module in (weather, air_quality, geocoding):
        monkeypatch.setattr(module, "cache", InMemoryCache())
    mock_air_quality(respx_mock)
    mock_onecall(respx_mock, by_lat=ONECALL_BY_LAT_AQ)


def dashboard(client: TestClient, client_id: str = CLIENT_A, **params):
    return client.get("/api/v1/dashboard", params=params, headers=h(client_id))


def test_dashboard_for_a_place(make_client, recorded) -> None:
    client = make_client(clock=lambda: AQ_NOW)
    body = dashboard(client, place="Delhi").json()
    assert body["location"]["name"] == "New Delhi" and body["location"]["timezone"] == "Asia/Kolkata"
    assert body["location"]["lat"] == 28.61  # 2 decimals in every response
    current = body["current"]
    assert {"temp_c", "feels_like_c", "icon", "humidity_pct", "wind_kmh", "wind_dir", "uv_index", "visibility_km",
            "pressure_hpa", "sunrise", "sunset"} <= set(current)
    assert current["icon"] in {"clear-day", "partly-cloudy", "cloudy", "haze", "fog"}
    assert current["sunrise"] < current["time"] < current["sunset"]
    assert len(body["hourly"]) == 24 and all(isinstance(p["temp_c"], int) for p in body["hourly"])
    assert 7 <= len(body["daily"]) <= 8
    air = body["air_quality"]
    assert air["scale"] == "naqi" and air["category"] and len(air["hourly"]) >= 20
    assert body["alerts"] == []
    outdoor = body["outdoor_today"]
    assert outdoor["day"] == "today" and outdoor["best_block"] and outdoor["verdict"] in {
        "suitable", "marginal", "not_suitable"}


def test_json_is_gzipped_but_the_chat_stream_is_not(make_client, recorded, agent: FakeAgent) -> None:
    client = make_client(clock=lambda: AQ_NOW)
    response = dashboard(client, place="Delhi")
    assert response.headers["content-encoding"] == "gzip"
    assert response.json()["location"]["name"] == "New Delhi"  # httpx decodes it

    with client.stream("POST", "/api/v1/chat/stream", json={"message": "weather?"},
                       headers={**h(), "Accept-Encoding": "gzip"}) as r:
        body = r.read().decode()
    assert "content-encoding" not in r.headers  # streamed events must reach the browser as they come
    assert "event: message" in body


def test_dashboard_late_evening_shows_tomorrow(make_client, recorded) -> None:
    late = AQ_NOW.replace(hour=22, minute=30)  # fewer than 2 hours left today
    client = make_client(clock=lambda: late)
    outdoor = dashboard(client, place="Delhi").json()["outdoor_today"]
    assert outdoor["day"] == "tomorrow"
    start = datetime.fromisoformat(outdoor["best_block"]["start"])
    assert start.date() == (late + timedelta(days=1)).date() and 6 <= start.hour < 22


def test_dashboard_without_a_fitting_block_today_shows_tomorrow(make_client, recorded, monkeypatch) -> None:
    from backend.api import dashboard as dashboard_module

    real = dashboard_module.assess_activity

    def no_block_today(activity, weather_results, air, now=None, **kwargs):
        result = real(activity, weather_results, air, now=now, **kwargs)
        is_today = weather_results[0].window.start.date() == AQ_NOW.date()
        return result.model_copy(update={"best_blocks": []}) if is_today else result

    monkeypatch.setattr(dashboard_module, "assess_activity", no_block_today)
    client = make_client(clock=lambda: AQ_NOW)
    assert dashboard(client, place="Delhi").json()["outdoor_today"]["day"] == "tomorrow"


def _assert_waking_hours(outdoor: dict, now: datetime) -> None:
    """The dashboard's window sits within 06:00-22:00 local on one day, today or tomorrow."""
    block = outdoor["best_block"]
    start, end = datetime.fromisoformat(block["start"]), datetime.fromisoformat(block["end"])
    day = now.date() if outdoor["day"] == "today" else (now + timedelta(days=1)).date()
    assert start.date() == day and end.date() == day, (start, end)  # never crosses midnight
    assert start.time() >= time(6) and end.time() <= time(22), (start, end)
    if outdoor["day"] == "today":
        assert start >= now.replace(minute=0, second=0, microsecond=0)


def test_dashboard_delhi_afternoon_gets_a_daytime_window(make_client, recorded) -> None:
    # Seen live: at 13:06 the coolest block was 22:30-00:30. It must be a waking-hours block or tomorrow.
    now = AQ_NOW.replace(hour=13, minute=6)
    client = make_client(clock=lambda: now)
    _assert_waking_hours(dashboard(client, place="Delhi").json()["outdoor_today"], now)


@pytest.mark.parametrize("hour", [12, 15, 17, 19, 20, 21, 23])  # the recording starts at 12:00
def test_dashboard_window_stays_in_waking_hours(make_client, recorded, hour) -> None:
    now = AQ_NOW.replace(hour=hour, minute=6)
    client = make_client(clock=lambda: now)
    outdoor = dashboard(client, place="Delhi").json()["outdoor_today"]
    _assert_waking_hours(outdoor, now)
    if hour >= 20:  # fewer than 2 hours left before 22:00
        assert outdoor["day"] == "tomorrow"


def test_dashboard_by_coordinates_reverse_geocodes_rounded(make_client, recorded) -> None:
    client = make_client(clock=lambda: AQ_NOW)
    body = dashboard(client, lat=28.613895, lon=77.209006).json()
    assert (body["location"]["lat"], body["location"]["lon"]) == (28.61, 77.21)
    assert "28.6138" not in json.dumps(body)


def test_dashboard_ambiguous_place_is_409_with_candidates(make_client, recorded) -> None:
    client = make_client(clock=lambda: AQ_NOW)
    error = assert_error(dashboard(client, place="Aurangabad"), 409, "ambiguous_place")
    candidates = error["details"]["candidates"]
    assert len(candidates) >= 2 and {"name", "state", "country", "lat", "lon"} <= set(candidates[0])


def test_dashboard_bad_input_and_unknown_place(make_client, recorded) -> None:
    client = make_client(clock=lambda: AQ_NOW)
    assert_error(dashboard(client), 400, "bad_request")
    assert_error(dashboard(client, lat=28.6), 400, "bad_request")
    assert_error(dashboard(client, place="Atlantis"), 404, "not_found")


def test_dashboard_has_its_own_limit_and_skips_the_chat_cap(make_client, recorded) -> None:
    client = make_client(clock=lambda: AQ_NOW, dashboard_rate_per_minute=2, daily_message_cap=1)
    assert dashboard(client, place="Delhi").status_code == dashboard(client, place="Delhi").status_code == 200
    third = dashboard(client, place="Delhi")
    assert_error(third, 429, "rate_limited")
    assert int(third.headers["Retry-After"]) >= 1
    assert chat(client).status_code == 200  # dashboard calls didn't use the daily chat cap
