import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ArchitectureTests(unittest.TestCase):
    def test_domain_and_scheduler_do_not_import_app_or_transport(self):
        paths = list((ROOT / "src/commands").glob("*.py")) + [
            ROOT / "src/job_chain.py",
            ROOT / "src/job_runtime.py",
            ROOT / "src/extraction.py",
            ROOT / "src/schema.py",
        ]
        for path in paths:
            with self.subTest(module=path.name):
                for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                    if isinstance(node, ast.ImportFrom):
                        self.assertNotIn(node.module, {"app", "desktop", "http_server"})
                    elif isinstance(node, ast.Import):
                        self.assertFalse(
                            {alias.name for alias in node.names}
                            & {"app", "desktop", "http_server"}
                        )

    def test_ui_lifecycle_has_a_single_owner(self):
        for path in (ROOT / "src/ui").glob("*.js"):
            text = path.read_text(encoding="utf-8")
            with self.subTest(module=path.name):
                self.assertNotRegex(
                    text,
                    r"\b(?:refresh|renderRows|settings|loadRows)\s*=\s*(?:async\s+)?function",
                )

    def test_runtime_source_does_not_contain_tests_or_packaging(self):
        self.assertFalse(list((ROOT / "src").glob("test_*.py")))
        self.assertFalse(list((ROOT / "src").glob("*.spec")))
