# Living AI Knowledge Base (MVP)

This repository now includes a lightweight **living AI development framework** that turns raw "thoughts" into an evolving, structured knowledge base.

## What problem this solves

Instead of appending random notes forever, the engine:

1. Accepts a new thought.
2. Distills it into a candidate principle.
3. Compares it with the current knowledge base.
4. Merges it into an existing principle (if similar) **or** creates a new principle.
5. Rewrites the full markdown output from the latest state.

This creates a cleaner "core operating knowledge base" that improves over time.

## Project structure

- `living_kb/models.py` - typed data models for thought/principle/knowledge-base state
- `living_kb/engine.py` - core logic for tag extraction, similarity matching, merging, and markdown generation
- `living_kb/cli.py` - command-line interface
- `tests/test_engine.py` - unit tests for core behaviors

## Quick start

From repository root:

```bash
python -m living_kb.cli init
python -m living_kb.cli add --text "Use explicit acceptance criteria in every AI workflow."
python -m living_kb.cli add --text "Agent tasks should include acceptance criteria so output quality is measurable."
python -m living_kb.cli show
```

Generated files:

- `kb/knowledge_base.json` - source-of-truth state
- `kb/knowledge_base.md` - rewritten human-readable knowledge base

Optional tuning:

```bash
python -m living_kb.cli --merge-threshold 0.35 add --text "your thought here"
```

## Design notes

- Similarity is based on weighted overlap between content tokens and tags.
- Confidence increases when multiple thoughts reinforce the same principle.
- Markdown is regenerated each run so the final output stays concise and structured.

## Run tests

```bash
python -m unittest discover -s tests -v
```
