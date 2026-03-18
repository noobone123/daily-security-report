from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from skill_lib import ValidationError, load_workspace, validate_workspace


class WorkspaceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]

    def test_repo_sample_planning_is_valid(self) -> None:
        payload = validate_workspace(self.repo_root)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["sources"], 5)
        self.assertEqual(payload["enabled_sources"], 0)
        self.assertEqual(payload["topics"], 2)

    def test_workspace_rejects_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_valid_workspace(root)
            (root / "planning" / "report-style.md").write_text(
                "---\nmode: invalid\n---\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValidationError, "frontmatter"):
                load_workspace(root)

    def test_source_requires_all_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_valid_workspace(root)
            (root / "planning" / "sources.toml").write_text(
                '[[sources]]\nid = "demo-source"\ntitle = "Demo Source"\nenabled = true\ntopics = ["demo-topic"]\nnotes = "No kind."\nfetch.url = "https://example.com/feed.xml"\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValidationError, "missing 'kind'"):
                load_workspace(root)

    def _write_valid_workspace(self, root: Path) -> None:
        (root / "planning").mkdir(parents=True, exist_ok=True)
        (root / "planning" / "report-style.md").write_text(
            "# Report Style\n\n## Audience\n\nAnalyst.\n\n## Language\n\nEnglish.\n\n## Output Format\n\nMarkdown.\n\n## Extra Instructions\n\nKeep it short.\n",
            encoding="utf-8",
        )
        (root / "planning" / "topics.md").write_text(
            "# Topics\n\n## Demo Topic\n\n### Care About\n\nSignals.\n\n### Usually Ignore\n\nNoise.\n\n### Reporting Angle\n\nExplain why it matters.\n",
            encoding="utf-8",
        )
        (root / "planning" / "sources.toml").write_text(
            '[[sources]]\nid = "demo-source"\ntitle = "Demo Source"\nkind = "rss"\nenabled = true\ntopics = ["demo-topic"]\nnotes = "Example source."\nfetch.url = "https://example.com/feed.xml"\n',
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
