"""API keys must never reach exception messages or logs, whatever goes wrong."""

import io
import logging

import httpx
import pytest

from backend.core.config import get_settings
from backend.core.logging import JsonFormatter
from backend.core.redact import redact
from backend.services.errors import UpstreamAPIError
from backend.services.http import get_json

pytestmark = pytest.mark.anyio

OW_KEY = "ow-secret-0123456789abcdef"
GEO_KEY = "geo-secret-fedcba9876543210"
GOOGLE_KEY = "google-secret-AIzaSy0000000000"
URL = "https://api.example.test/data"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _keys(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENWEATHER_API_KEY", OW_KEY)
    monkeypatch.setenv("GEOAPIFY_API_KEY", GEO_KEY)
    monkeypatch.setenv("GOOGLE_API_KEY", GOOGLE_KEY)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def assert_no_keys(text: str) -> None:
    for key in (OW_KEY, GEO_KEY, GOOGLE_KEY):
        assert key not in text


# --- redact() -----------------------------------------------------------------------


@pytest.mark.parametrize("text, expected", [
    (f"{URL}?lat=1&appid={OW_KEY}&units=metric", f"{URL}?lat=1&appid=***&units=metric"),
    (f"{URL}?text=Delhi&apiKey={GEO_KEY}", f"{URL}?text=Delhi&apiKey=***"),
    (f"{URL}?key=abc123&q=x", f"{URL}?key=***&q=x"),  # any value of a key-carrying parameter
    ("GET /v1?api_key=zzz", "GET /v1?api_key=***"),
    (f"bad key {GOOGLE_KEY} in header", "bad key *** in header"),  # configured values, anywhere
    ("monkey=banana&turnkey=1", "monkey=banana&turnkey=1"),  # only whole parameter names
])
def test_redact(text: str, expected: str) -> None:
    assert redact(text) == expected


# --- exceptions from get_json ---------------------------------------------------------


async def test_httpx_error_message_never_has_the_key(respx_mock, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    respx_mock.get(URL).mock(side_effect=httpx.ConnectError(f"connection failed for {URL}?appid={OW_KEY}"))
    with pytest.raises(UpstreamAPIError) as exc:
        await get_json("OpenWeather", URL, {"appid": OW_KEY, "lat": 1})
    assert_no_keys(str(exc.value))
    assert exc.value.__cause__ is None and exc.value.__suppress_context__
    assert_no_keys(caplog.text)


async def test_other_errors_keep_their_type_but_lose_the_key(respx_mock) -> None:
    # e.g. respx's "not mocked" AssertionError, which quotes the full URL with its query string
    with pytest.raises(AssertionError) as exc:
        await get_json("Geoapify", f"{URL}/unmocked", {"apiKey": GEO_KEY, "text": "Delhi"})
    assert "not mocked" in str(exc.value) and "apiKey=***" in str(exc.value)
    assert_no_keys(str(exc.value))
    assert exc.value.__cause__ is None and exc.value.__suppress_context__


async def test_http_error_statuses_never_have_the_key(respx_mock) -> None:
    for status in (401, 429, 500, 503):
        respx_mock.get(URL).mock(return_value=httpx.Response(status, text=f"echo appid={OW_KEY}"))
        with pytest.raises(UpstreamAPIError) as exc:
            await get_json("OpenWeather", URL, {"appid": OW_KEY})
        assert_no_keys(str(exc.value))


# --- logs -----------------------------------------------------------------------------


def test_log_messages_args_and_tracebacks_are_redacted(caplog: pytest.LogCaptureFixture) -> None:
    log = logging.getLogger("backend.test.redaction")
    with caplog.at_level(logging.INFO):
        log.info("calling %s", f"{URL}?appid={OW_KEY}")
        try:
            raise RuntimeError(f"boom with key {GEO_KEY}")
        except RuntimeError:
            log.exception("failed")
    assert "appid=***" in caplog.text and "boom with key ***" in caplog.text
    assert_no_keys(caplog.text)


def test_json_log_lines_redact_extra_fields_too() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    log = logging.getLogger("backend.test.redaction.json")
    log.addHandler(handler)
    log.propagate = False
    try:
        log.warning("upstream failed", extra={"url": f"{URL}?appid={OW_KEY}", "token": GOOGLE_KEY})
    finally:
        log.removeHandler(handler)
    line = stream.getvalue()
    assert "appid=***" in line
    assert_no_keys(line)


async def test_agent_error_path_never_logs_the_key(respx_mock, caplog: pytest.LogCaptureFixture,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """End to end: a weather fetch that blows up mid-turn; the reply, debug and logs stay clean."""
    from backend.agent.graph import Agent, build_graph
    from backend.agent.schemas import QueryUnderstanding, UserContext
    from backend.services import weather
    from backend.services.cache import InMemoryCache
    from backend.tests.agent_fakes import QueuedExtractor, RecordingChatModel, fixture_now, install_fake_geocoding

    install_fake_geocoding(monkeypatch)
    monkeypatch.setattr(weather, "cache", InMemoryCache())
    respx_mock.get(weather.ONECALL_URL).mock(side_effect=httpx.ReadError(f"reset: {weather.ONECALL_URL}?appid={OW_KEY}"))
    extractor = QueuedExtractor(QueryUnderstanding(intent="weather", locations=["Jaipur"], date_expr="tomorrow"))
    agent = Agent(UserContext(now=fixture_now("jaipur")),
                  graph=build_graph(extractor=extractor, answer_llm=RecordingChatModel(),
                                    retriever=lambda **kwargs: []))
    with caplog.at_level(logging.DEBUG):
        response = await agent.ask("Jaipur tomorrow")
    assert response.kind == "error"
    assert_no_keys(response.text + str(response.debug.error) + caplog.text)
