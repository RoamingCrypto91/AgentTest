from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class Thought:
    id: str
    text: str
    tags: list[str]
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "tags": self.tags,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Thought":
        return cls(
            id=payload["id"],
            text=payload["text"],
            tags=list(payload.get("tags", [])),
            created_at=payload.get("created_at", utc_now_iso()),
        )


@dataclass
class Principle:
    id: str
    title: str
    statement: str
    tags: list[str]
    source_thought_ids: list[str]
    confidence: float = 0.55
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "statement": self.statement,
            "tags": self.tags,
            "source_thought_ids": self.source_thought_ids,
            "confidence": self.confidence,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Principle":
        return cls(
            id=payload["id"],
            title=payload["title"],
            statement=payload["statement"],
            tags=list(payload.get("tags", [])),
            source_thought_ids=list(payload.get("source_thought_ids", [])),
            confidence=float(payload.get("confidence", 0.55)),
            created_at=payload.get("created_at", utc_now_iso()),
            updated_at=payload.get("updated_at", utc_now_iso()),
        )


@dataclass
class KnowledgeBase:
    version: int = 1
    updated_at: str = field(default_factory=utc_now_iso)
    principles: list[Principle] = field(default_factory=list)
    thoughts: list[Thought] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "updated_at": self.updated_at,
            "principles": [p.to_dict() for p in self.principles],
            "thoughts": [t.to_dict() for t in self.thoughts],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "KnowledgeBase":
        return cls(
            version=int(payload.get("version", 1)),
            updated_at=payload.get("updated_at", utc_now_iso()),
            principles=[Principle.from_dict(p) for p in payload.get("principles", [])],
            thoughts=[Thought.from_dict(t) for t in payload.get("thoughts", [])],
        )
