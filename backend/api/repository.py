"""Conversations and messages, behind a small interface (SQLite now; Postgres later via DATABASE_URL).

Rows hold API-shaped JSON (coordinates already rounded to 2 decimals) and never a raw position.
"""

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

TITLE_CHARS = 60
COUNTED_KINDS = ("answer", "clarification", "decline")  # turns that count toward the daily cap


@dataclass
class Conversation:
    id: str
    client_id: str
    title: str
    created_at: datetime
    updated_at: datetime


@dataclass
class Message:
    role: str  # "user" | "assistant"
    text: str
    kind: str | None  # the turn's result kind (on both rows of an exchange)
    response: dict | None  # assistant rows: the API ChatResponse as JSON
    created_at: datetime


class ConversationRepository(Protocol):
    async def open(self) -> None: ...

    async def close(self) -> None: ...

    async def create(self, client_id: str) -> Conversation: ...

    async def list_conversations(self, client_id: str) -> list[Conversation]: ...

    async def get(self, client_id: str, conversation_id: str) -> Conversation | None: ...

    async def delete(self, client_id: str, conversation_id: str) -> bool: ...

    async def messages(self, client_id: str, conversation_id: str) -> list[Message]: ...

    async def add_exchange(self, client_id: str, conversation_id: str, user_text: str, kind: str,
                           response: dict) -> None: ...

    async def counted_messages_since(self, client_id: str, since: datetime) -> int: ...


class _Base(DeclarativeBase):
    pass


class _ConversationRow(_Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(36), index=True)
    title: Mapped[str] = mapped_column(String(TITLE_CHARS + 1), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class _MessageRow(_Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[str] = mapped_column(String(36), index=True)
    role: Mapped[str] = mapped_column(String(10))
    text: Mapped[str] = mapped_column(Text)
    kind: Mapped[str | None] = mapped_column(String(20), nullable=True)
    response_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


def _utc(dt: datetime) -> datetime:
    """SQLite drops tzinfo; values are stored as UTC."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _conversation(row: _ConversationRow) -> Conversation:
    return Conversation(row.id, row.client_id, row.title, _utc(row.created_at), _utc(row.updated_at))


class SqlConversationRepository:
    """SQLAlchemy (async) implementation; `database_url` like "sqlite+aiosqlite:///data/weatherwise.db"."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self._engine: AsyncEngine | None = None
        self._sessions: async_sessionmaker | None = None

    async def open(self) -> None:
        if self.database_url.startswith("sqlite") and ":///" in self.database_url:
            path = self.database_url.split(":///", 1)[1]
            if path and path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._engine = create_async_engine(self.database_url)
        self._sessions = async_sessionmaker(self._engine, expire_on_commit=False)
        async with self._engine.begin() as conn:
            await conn.run_sync(_Base.metadata.create_all)

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()

    async def create(self, client_id: str) -> Conversation:
        now = datetime.now(timezone.utc)
        row = _ConversationRow(id=str(uuid.uuid4()), client_id=client_id, title="", created_at=now, updated_at=now)
        async with self._sessions() as session, session.begin():
            session.add(row)
        return _conversation(row)

    async def list_conversations(self, client_id: str) -> list[Conversation]:
        async with self._sessions() as session:
            rows = (await session.scalars(select(_ConversationRow).where(_ConversationRow.client_id == client_id)
                                          .order_by(_ConversationRow.updated_at.desc()))).all()
        return [_conversation(r) for r in rows]

    async def get(self, client_id: str, conversation_id: str) -> Conversation | None:
        async with self._sessions() as session:
            row = await session.get(_ConversationRow, conversation_id)
        return _conversation(row) if row is not None and row.client_id == client_id else None

    async def delete(self, client_id: str, conversation_id: str) -> bool:
        async with self._sessions() as session, session.begin():
            row = await session.get(_ConversationRow, conversation_id)
            if row is None or row.client_id != client_id:
                return False
            await session.execute(delete(_MessageRow).where(_MessageRow.conversation_id == conversation_id))
            await session.delete(row)
        return True

    async def messages(self, client_id: str, conversation_id: str) -> list[Message]:
        async with self._sessions() as session:
            rows = (await session.scalars(select(_MessageRow).where(
                _MessageRow.conversation_id == conversation_id, _MessageRow.client_id == client_id)
                .order_by(_MessageRow.id))).all()
        return [Message(r.role, r.text, r.kind, json.loads(r.response_json) if r.response_json else None,
                        _utc(r.created_at)) for r in rows]

    async def add_exchange(self, client_id: str, conversation_id: str, user_text: str, kind: str,
                           response: dict) -> None:
        now = datetime.now(timezone.utc)
        async with self._sessions() as session, session.begin():
            conversation = await session.get(_ConversationRow, conversation_id)
            if not conversation.title:
                conversation.title = user_text[:TITLE_CHARS].strip()
            conversation.updated_at = now
            session.add(_MessageRow(conversation_id=conversation_id, client_id=client_id, role="user",
                                    text=user_text, kind=kind, created_at=now))
            session.add(_MessageRow(conversation_id=conversation_id, client_id=client_id, role="assistant",
                                    text=response.get("text", ""), kind=kind,
                                    response_json=json.dumps(response, ensure_ascii=False, default=str),
                                    created_at=now))

    async def counted_messages_since(self, client_id: str, since: datetime) -> int:
        """User messages since `since` whose turn produced an answer, clarification or decline."""
        async with self._sessions() as session:
            return await session.scalar(select(func.count()).select_from(_MessageRow).where(
                _MessageRow.client_id == client_id, _MessageRow.role == "user",
                _MessageRow.kind.in_(COUNTED_KINDS), _MessageRow.created_at >= since.astimezone(timezone.utc)))
