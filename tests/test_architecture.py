import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ArchitectureTests(unittest.TestCase):
    def test_domain_and_scheduler_do_not_import_app_or_transport(self):
        paths = (
            list((ROOT / "src/commands").glob("*.py"))
            + list((ROOT / "src/storage").glob("*.py"))
            + list((ROOT / "src/formats").glob("*.py"))
            + [
                ROOT / "src/job_chain.py",
                ROOT / "src/job_runtime.py",
                ROOT / "src/extraction.py",
                ROOT / "src/schema.py",
            ]
        )
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
                    r"\b(?:refresh|renderRows|settings|loadRows|showView)\s*=\s*(?:async\s+)?function",
                )

    def test_runtime_source_does_not_contain_tests_or_packaging(self):
        self.assertFalse(list((ROOT / "src").glob("test_*.py")))
        self.assertFalse(list((ROOT / "src").glob("*.spec")))

    def test_ui_state_and_native_edit_guard_have_one_owner(self):
        html = (ROOT / "src/ui/index.html").read_text(encoding="utf-8")
        self.assertLess(html.index("/state.js"), html.index("/studio.js"))
        for path in (ROOT / "src/ui").glob("*.js"):
            if path.name == "state.js":
                continue
            with self.subTest(module=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertNotRegex(
                    text, r"window\.(?:processTab|markKind|connectionSnapshot)"
                )
                self.assertNotRegex(
                    text, r"const (?:oldPrev|oldNext|oldOpen|oldProjectChange|oldMcp)\b"
                )
        for path in [ROOT / "src/close_behavior.py", ROOT / "src/update_controller.py"]:
            self.assertIn("studioState.dirty.size", path.read_text(encoding="utf-8"))

    def test_store_is_a_composition_facade(self):
        tree = ast.parse((ROOT / "src/core.py").read_text(encoding="utf-8"))
        store = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        for method in store.body:
            if isinstance(method, ast.FunctionDef) and method.name != "__init__":
                with self.subTest(method=method.name):
                    self.assertEqual(len(method.body), 1)
                    self.assertIsInstance(method.body[0], ast.Return)
        for path in (ROOT / "src/storage").glob("*.py"):
            imports = [
                n.module
                for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
                if isinstance(n, ast.ImportFrom)
            ]
            self.assertNotIn("core", imports)
