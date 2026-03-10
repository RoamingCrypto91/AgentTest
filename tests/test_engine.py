from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from living_kb.engine import LivingKnowledgeBaseEngine


class LivingKnowledgeBaseEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)
        self.db_path = self.base_path / "knowledge_base.json"
        self.md_path = self.base_path / "knowledge_base.md"
        self.engine = LivingKnowledgeBaseEngine(
            db_path=self.db_path, output_markdown_path=self.md_path, merge_threshold=0.35
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_add_thought_creates_first_principle(self) -> None:
        kb = self.engine.add_thought(
            "Use explicit acceptance criteria in every AI workflow to reduce ambiguity."
        )
        self.assertEqual(len(kb.thoughts), 1)
        self.assertEqual(len(kb.principles), 1)
        self.assertIn("ambiguity", kb.principles[0].statement.lower())
        self.assertTrue(self.db_path.exists())
        self.assertTrue(self.md_path.exists())

    def test_similar_thoughts_merge_into_single_principle(self) -> None:
        self.engine.add_thought(
            "Always define clear acceptance criteria before asking an agent to execute."
        )
        kb = self.engine.add_thought(
            "Agent tasks should start with explicit acceptance criteria so quality is measurable."
        )
        self.assertEqual(len(kb.thoughts), 2)
        self.assertEqual(len(kb.principles), 1)
        principle = kb.principles[0]
        self.assertGreaterEqual(principle.confidence, 0.66)
        self.assertEqual(len(principle.source_thought_ids), 2)

    def test_different_topics_create_new_principles(self) -> None:
        self.engine.add_thought("Track prompt templates in version control for reproducibility.")
        kb = self.engine.add_thought("Run post-deploy evals daily to detect regressions quickly.")
        self.assertEqual(len(kb.principles), 2)


if __name__ == "__main__":
    unittest.main()
