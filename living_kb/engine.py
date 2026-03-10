from __future__ import annotations

import json
import re
from pathlib import Path
from uuid import uuid4

from living_kb.models import KnowledgeBase, Principle, Thought, utc_now_iso

DEFAULT_TAG_KEYWORDS: dict[str, tuple[str, ...]] = {
    "prompting": ("prompt", "instruction", "few-shot", "chain-of-thought"),
    "evaluation": ("eval", "evaluate", "metric", "benchmark", "quality"),
    "automation": ("workflow", "automation", "agent", "autonomous"),
    "context": ("context", "memory", "retrieve", "knowledge"),
    "product": ("outcome", "impact", "user", "product", "value"),
    "reliability": ("guardrail", "safety", "risk", "failure", "fallback"),
}

STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "to",
    "with",
}


class LivingKnowledgeBaseEngine:
    def __init__(
        self,
        db_path: str | Path = "kb/knowledge_base.json",
        output_markdown_path: str | Path = "kb/knowledge_base.md",
        merge_threshold: float = 0.30,
    ) -> None:
        self.db_path = Path(db_path)
        self.output_markdown_path = Path(output_markdown_path)
        self.merge_threshold = merge_threshold

    def load(self) -> KnowledgeBase:
        if not self.db_path.exists():
            return KnowledgeBase()
        payload = json.loads(self.db_path.read_text(encoding="utf-8"))
        return KnowledgeBase.from_dict(payload)

    def save(self, kb: KnowledgeBase) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path.write_text(json.dumps(kb.to_dict(), indent=2), encoding="utf-8")
        self.output_markdown_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_markdown_path.write_text(self.render_markdown(kb), encoding="utf-8")

    def add_thought(self, text: str) -> KnowledgeBase:
        kb = self.load()
        thought = Thought(
            id=f"thought_{uuid4().hex[:8]}",
            text=normalize_ws(text),
            tags=self.extract_tags(text),
        )
        kb.thoughts.append(thought)

        candidate = self.distill_principle(thought)
        existing = self.best_matching_principle(candidate, kb.principles)
        if existing and self.similarity(candidate, existing) >= self.merge_threshold:
            self.merge_principle(existing, candidate, thought.id)
        else:
            candidate.source_thought_ids = [thought.id]
            candidate.confidence = self.compute_confidence(1)
            kb.principles.append(candidate)

        kb.version += 1
        kb.updated_at = utc_now_iso()
        self.sort_principles(kb.principles)
        self.save(kb)
        return kb

    def extract_tags(self, text: str) -> list[str]:
        lower = text.lower()
        tags = set(re.findall(r"#([a-z0-9_\-]+)", lower))
        for tag, keywords in DEFAULT_TAG_KEYWORDS.items():
            if any(word in lower for word in keywords):
                tags.add(tag)
        return sorted(tags)

    def distill_principle(self, thought: Thought) -> Principle:
        cleaned = normalize_ws(thought.text)
        first_sentence = split_sentences(cleaned)[0]
        title = build_title(first_sentence)
        return Principle(
            id=f"principle_{uuid4().hex[:8]}",
            title=title,
            statement=first_sentence,
            tags=thought.tags,
            source_thought_ids=[],
        )

    def best_matching_principle(
        self, candidate: Principle, principles: list[Principle]
    ) -> Principle | None:
        if not principles:
            return None
        return max(principles, key=lambda p: self.similarity(candidate, p))

    def similarity(self, left: Principle, right: Principle) -> float:
        left_text = left.title + " " + left.statement
        right_text = right.title + " " + right.statement
        left_tokens = content_tokens(left_text)
        right_tokens = content_tokens(right_text)
        if not left_tokens or not right_tokens:
            token_sim = 0.0
            token_containment = 0.0
        else:
            overlap = left_tokens & right_tokens
            token_sim = len(overlap) / len(left_tokens | right_tokens)
            token_containment = len(overlap) / min(len(left_tokens), len(right_tokens))
        left_phrases = content_bigrams(left_text)
        right_phrases = content_bigrams(right_text)
        if not left_phrases or not right_phrases:
            phrase_sim = 0.0
            phrase_containment = 0.0
            phrase_overlap_count = 0
        else:
            overlap = left_phrases & right_phrases
            phrase_overlap_count = len(overlap)
            phrase_sim = len(overlap) / len(left_phrases | right_phrases)
            phrase_containment = len(overlap) / min(len(left_phrases), len(right_phrases))
        left_tags = set(left.tags)
        right_tags = set(right.tags)
        tag_sim = 0.0 if not (left_tags or right_tags) else len(left_tags & right_tags) / len(left_tags | right_tags)
        lexical_sim = max(token_sim, token_containment)
        phrase_strength = max(phrase_sim, phrase_containment)
        shared_term_boost = 0.05 if len(left_tokens & right_tokens) >= 3 else 0.0
        shared_phrase_boost = 0.06 if phrase_overlap_count >= 1 else 0.0
        score = (
            (lexical_sim * 0.65)
            + (phrase_strength * 0.15)
            + (tag_sim * 0.2)
            + shared_term_boost
            + shared_phrase_boost
        )
        return min(1.0, score)

    def merge_principle(self, target: Principle, incoming: Principle, thought_id: str) -> None:
        target.statement = combine_statements(target.statement, incoming.statement)
        target.title = choose_better_title(target.title, incoming.title)
        target.tags = sorted(set(target.tags) | set(incoming.tags))
        if thought_id not in target.source_thought_ids:
            target.source_thought_ids.append(thought_id)
        target.confidence = self.compute_confidence(len(target.source_thought_ids))
        target.updated_at = utc_now_iso()

    @staticmethod
    def sort_principles(principles: list[Principle]) -> None:
        principles.sort(
            key=lambda p: (p.confidence, len(p.source_thought_ids), p.updated_at),
            reverse=True,
        )

    @staticmethod
    def compute_confidence(source_count: int) -> float:
        # Confidence grows with more confirming thoughts, but has a ceiling.
        return round(min(0.95, 0.5 + (source_count * 0.08)), 2)

    def render_markdown(self, kb: KnowledgeBase) -> str:
        lines = [
            "# Living AI Development Knowledge Base",
            "",
            f"- Version: {kb.version}",
            f"- Last Updated (UTC): {kb.updated_at}",
            f"- Thoughts Captured: {len(kb.thoughts)}",
            f"- Active Principles: {len(kb.principles)}",
            "",
            "## Core Principles (Auto-Rewritten)",
            "",
        ]
        if not kb.principles:
            lines += [
                "_No principles yet. Add your first thought to start evolving this knowledge base._",
                "",
            ]
        for idx, principle in enumerate(kb.principles, start=1):
            tags = ", ".join(principle.tags) if principle.tags else "none"
            lines += [
                f"### {idx}. {principle.title}",
                f"- Statement: {principle.statement}",
                f"- Tags: {tags}",
                f"- Confidence: {principle.confidence}",
                f"- Built From Thoughts: {len(principle.source_thought_ids)}",
                "",
            ]

        lines += [
            "## How This Stays 'Living'",
            "",
            "Each new thought is distilled, compared against existing principles, then either merged into the most similar principle or added as a new one.",
            "The resulting document is regenerated from the latest full state every time, so the structure improves rather than becoming an append-only dump.",
            "",
        ]
        return "\n".join(lines).strip() + "\n"


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", normalize_ws(text))
    return [p.strip() for p in parts if p.strip()] or [normalize_ws(text)]


def build_title(sentence: str, max_words: int = 8) -> str:
    tokens = [t for t in re.findall(r"[a-zA-Z0-9\-]+", sentence) if t.lower() not in STOP_WORDS]
    if not tokens:
        return "Untitled Principle"
    selected = tokens[:max_words]
    return " ".join(word.capitalize() for word in selected)


def content_tokens(text: str) -> set[str]:
    return {
        normalize_token(token)
        for token in re.findall(r"[a-zA-Z0-9\-]+", text)
        if token.lower() not in STOP_WORDS
    }


def content_bigrams(text: str) -> set[str]:
    tokens = [
        normalize_token(token)
        for token in re.findall(r"[a-zA-Z0-9\-]+", text)
        if token.lower() not in STOP_WORDS
    ]
    return {f"{tokens[i]} {tokens[i + 1]}" for i in range(len(tokens) - 1)}


def normalize_token(token: str) -> str:
    t = token.lower()
    if len(t) > 4 and t.endswith("ing"):
        t = t[:-3]
    elif len(t) > 3 and t.endswith("ed"):
        t = t[:-2]
    elif len(t) > 4 and t.endswith("es"):
        t = t[:-2]
    elif len(t) > 3 and t.endswith("s"):
        t = t[:-1]
    return t


def combine_statements(existing: str, incoming: str) -> str:
    if incoming.lower() in existing.lower():
        return existing
    existing_parts = split_sentences(existing)
    incoming_parts = split_sentences(incoming)
    merged: list[str] = []
    seen = set()
    for sentence in existing_parts + incoming_parts:
        key = sentence.lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(sentence)
    return " ".join(merged[:2])


def choose_better_title(existing: str, incoming: str) -> str:
    # Prefer the shorter title when both are meaningful to keep headings concise.
    if len(incoming) < len(existing) and len(incoming.split()) >= 2:
        return incoming
    return existing
