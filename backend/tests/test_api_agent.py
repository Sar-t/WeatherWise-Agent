"""The real graph behind the API: status/token events, SQLite checkpoints (follow-ups across a
restart), and no precise coordinates in checkpoints. Fake LLMs, recorded weather, no network."""

from pathlib import Path

import aiosqlite
import pytest
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from backend.agent.graph import build_graph, state_serde
from backend.agent.schemas import QueryUnderstanding, UserContext
from backend.api import schemas
from backend.api.agent_service import GraphAgentService
from backend.api.app import _ctx
from backend.services import weather
from backend.services.cache import InMemoryCache
from backend.tests.agent_fakes import (
    QueuedExtractor,
    RecordingChatModel,
    fixture_now,
    install_fake_geocoding,
    mock_onecall,
)

pytestmark = pytest.mark.anyio
JAIPUR_NOW = fixture_now("jaipur")
REPLY_CHUNKS = ["Light rain ", "is unlikely; ", "expect about 27°C."]


class StreamingChatModel(RecordingChatModel):
    """Streams its reply in chunks, like a real chat model."""

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls.append(messages)
        for chunk in REPLY_CHUNKS:
            yield ChatGenerationChunk(message=AIMessageChunk(content=chunk))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch, respx_mock) -> None:
    install_fake_geocoding(monkeypatch)
    monkeypatch.setattr(weather, "cache", InMemoryCache())
    mock_onecall(respx_mock)


async def service(db: Path, *understandings: QueryUnderstanding):
    conn = await aiosqlite.connect(db)
    saver = AsyncSqliteSaver(conn, serde=state_serde())
    await saver.setup()
    extractor = QueuedExtractor(*understandings)
    graph = build_graph(extractor=extractor, answer_llm=StreamingChatModel(), checkpointer=saver,
                        retriever=lambda **kwargs: [])
    return GraphAgentService(graph, saver), extractor, conn


async def test_status_events_follow_the_graph_and_tokens_come_from_the_answer(tmp_path: Path) -> None:
    agent, _, conn = await service(tmp_path / "cp.sqlite", QueryUnderstanding(
        intent="weather", locations=["Jaipur"], date_expr="tomorrow", time_expr="evening"))
    events = []

    async def emit(event: str, text: str) -> None:
        events.append((event, text))

    try:
        response = await agent.run("Will it rain in Jaipur tomorrow evening?", "conv-1", UserContext(now=JAIPUR_NOW),
                                   emit)
    finally:
        await conn.close()
    statuses = [t for e, t in events if e == "status"]
    assert statuses == ["understanding", "finding location", "fetching weather", "looking up guidance",
                        "writing answer"]
    assert [t for e, t in events if e == "token"] == REPLY_CHUNKS
    assert events.index(("status", "writing answer")) < events.index(("token", REPLY_CHUNKS[0]))
    assert response.kind == "answer" and response.text == "".join(REPLY_CHUNKS)
    assert "total" in response.debug.timings


async def test_follow_ups_survive_a_restart(tmp_path: Path) -> None:
    db = tmp_path / "cp.sqlite"
    first, _, conn = await service(db, QueryUnderstanding(intent="weather", locations=["Jaipur"],
                                                          date_expr="tomorrow", time_expr="evening"))
    await first.run("Jaipur tomorrow evening?", "conv-1", UserContext(now=JAIPUR_NOW))
    await conn.close()  # "restart": a new connection, saver and graph on the same file

    second, extractor, conn = await service(db, QueryUnderstanding(intent="weather", locations=["Delhi"],
                                                                   is_follow_up=True))
    try:
        await second.run("and Delhi?", "conv-1", UserContext(now=JAIPUR_NOW))
        assert extractor.calls[0]["previous"].locations == ["Jaipur"]  # memory came from the checkpoint
        await second.delete_thread("conv-1")
        assert await second.checkpointer.aget_tuple({"configurable": {"thread_id": "conv-1"}}) is None
    finally:
        await conn.close()


async def test_checkpoints_never_hold_precise_coordinates(tmp_path: Path) -> None:
    db = tmp_path / "cp.sqlite"
    agent, _, conn = await service(db, QueryUnderstanding(intent="weather", date_expr="tomorrow"))  # here
    ctx = _ctx(schemas.ChatRequest(message="weather here?", location=schemas.LatLon(lat=26.912345, lon=75.787654)))
    try:
        response = await agent.run("weather here?", "conv-geo", ctx.model_copy(update={"now": JAIPUR_NOW}))
    finally:
        await conn.close()
    assert response.kind == "answer" and response.cards[0].location.lat == 26.91
    blob = db.read_bytes()
    for digits in (b"26.912", b"75.787", b"75.788"):
        assert digits not in blob
