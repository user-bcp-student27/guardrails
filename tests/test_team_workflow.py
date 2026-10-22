"""Exercise the six separate node implementations and their CLI artifacts."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agents.architect import architect_node
import graph.nodes as nodes
from graph.graph import NODES
from guardrails.output_check import validate_output
from harness import harness as H
import main as app
from test_graph_hitl import Stub


ROLES = ["architect", "backend", "frontend", "testing", "security", "chapter"]
SNAPSHOT = "### app/db.py\n```python\n" + "value = 1\n" * 40 + "```"


class TeamWorkflowTests(unittest.TestCase):
    def test_six_separate_nodes_and_review_context(self):
        stub = Stub(["APROBADO CON OBSERVACIONES", "APROBADO"])
        self.assertEqual(NODES, ROLES)
        for name in ROLES:
            self.assertTrue(
                (Path(app.__file__).parent / "agents" / f"{name}.py").is_file()
            )
        with (
            patch.object(nodes, "_invoke", stub),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            H.run_without_harness("team/product", SNAPSHOT, "ambos", "team-context")
            result = H.run_to_end("team-context")
        self.assertEqual(stub.calls, ROLES + ROLES[1:])
        self.assertTrue(result["approved"])
        for role in ("testing", "security"):
            self.assertIn(f"salida simulada de {role}", stub.prompt_de("chapter"))
            self.assertIn("OBSERVACIONES DE LA RONDA ANTERIOR", stub.prompt_de(role, 1))

    def test_resume_keeps_testing_and_security_pending(self):
        stub = Stub(["APROBADO"])
        with (
            patch.object(nodes, "_invoke", stub),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            H.run_without_harness("team/product", SNAPSHOT, "ambos", "team-save")
            for _ in range(4):
                H.step("team-save")
            saved = H.state("team-save")
            self.assertEqual(H.next_node("team-save"), "security")
            before = len(stub.calls)
            H.resume_with_harness(
                "team/product", SNAPSHOT, "ambos", "team-resume", saved
            )
            self.assertEqual(len(stub.calls), before)
            self.assertEqual(H.next_node("team-resume"), "security")
            restored = H.state("team-resume")
            self.assertEqual(restored["out_testing"], saved["out_testing"])
            self.assertEqual(restored["out_analysis"], saved["out_analysis"])
            H.run_to_end("team-resume")
        self.assertEqual(stub.calls[before:], ["security", "chapter"])

    def test_output_gate_requires_both_review_reports(self):
        good = {
            "out_analysis": "Hallazgo de severidad Alta en app/db.py con evidencia. "
            * 2,
            "out_plan": "Extraer configuración de app/db.py conservando el contrato. "
            * 2,
            "out_backend": "### app/db.py\n```python\nimport os\n```",
            "out_testing": "Pruebas y comandos reproducibles. Estado NO EJECUTADO. "
            * 2,
            "out_security": "Hallazgos con evidencia, severidad y verificación propuesta. "
            * 2,
            "out_chapter": "Los cambios propuestos conservan el contrato. VEREDICTO: APROBADO",
            "review_round": 1,
        }
        self.assertTrue(validate_output(good).valid)
        for field in ("out_testing", "out_security"):
            with self.subTest(field=field):
                result = validate_output({**good, field: ""})
                self.assertFalse(result.valid)
                self.assertEqual(result.field, field)
                result = validate_output(
                    {**good, field: good[field] + " ghp_example-secret"}
                )
                self.assertEqual(result.rule_code, "OUT-04")

    def test_architect_rejects_missing_plan(self):
        with self.assertRaisesRegex(ValueError, "## Plan"):
            architect_node(
                {"repo_name": "team/product", "snapshot": SNAPSHOT},
                lambda _: "Unstructured response",
            )

    def test_cli_persists_all_six_reports(self):
        self.check_cli(False)

    def test_cli_persists_seven_reports_and_approved_migration(self):
        self.check_cli(True)

    def check_cli(self, with_data):
        stub = Stub(["APROBADO"])

        def invoke(prompt):
            if prompt.startswith("Eres el responsable de Datos y persistencia"):
                stub.calls.append("data")
                return "Modelo aprobado de datos y migración propuesta. NO EJECUTADO. " * 2 + "\n### migrations/002_accounts.sql\n```sql\nCREATE TABLE accounts(id INTEGER PRIMARY KEY);\n```"
            response = stub(prompt)
            if stub.calls[-1] == "architect":
                return (
                    "## Analysis\n"
                    + "Hallazgo Alta en app/db.py con evidencia. " * 3
                    + "\n## Plan\n"
                    + "Corregir app/db.py y verificar el contrato. " * 3
                )
            if stub.calls[-1] == "testing":
                return (
                    "Propuesta de pruebas. NO EJECUTADO. Verificar el contrato. " * 3
                    + "\n### tests/test_db.py\n```python\nassert True\n```"
                )
            if stub.calls[-1] == "security":
                return (
                    "Revisión con evidencia de app/db.py y verificaciones pendientes. "
                    * 3
                )
            return response

        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict("os.environ", {"GUARDRAILS_INCLUDE_DATA": str(with_data).lower()}))
            root = Path(tmp)
            output = root / "output"
            output.mkdir()
            product = root / "product"
            product.mkdir()
            stack.enter_context(patch.object(app, "OUTPUT_DIR", output))
            stack.enter_context(
                patch.object(app, "CHECKPOINT_FILE", output / ".checkpoint.json")
            )
            stack.enter_context(patch.object(app, "GITHUB_OWNER", "team"))
            stack.enter_context(patch.object(app, "GITHUB_REPO", "product"))
            stack.enter_context(patch.object(app, "THREAD_ID", "team-cli"))
            stack.enter_context(
                patch.object(app, "_clone_or_pull", return_value=product)
            )
            stack.enter_context(
                patch.object(app, "_snapshot", return_value=(SNAPSHOT, 1))
            )
            stack.enter_context(
                patch.object(app, "_confirm_type", return_value="ambos")
            )
            stack.enter_context(patch.object(H, "load_context", return_value=""))
            stack.enter_context(patch.object(nodes, "_invoke", side_effect=invoke))
            stack.enter_context(
                patch.object(app, "_git_branch_push", return_value="test-branch")
            )
            # Stop before publication; the approved checkpoint is the artifact under test.
            stack.enter_context(
                patch.object(
                    app, "_gh_create_pr", side_effect=RuntimeError("test: no remote PR")
                )
            )
            stack.enter_context(patch("builtins.input", return_value="s"))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            with self.assertRaises(SystemExit) as stopped:
                app.main()
            self.assertEqual(stopped.exception.code, 1)
            expected = ["architect", "data", *ROLES[1:]] if with_data else ROLES
            self.assertEqual(stub.calls, expected)
            for filename, _ in (app.DATA_OUTPUT_FILES if with_data else app.OUTPUT_FILES).values():
                self.assertTrue((output / filename).is_file(), filename)
            self.assertTrue((product / "tests/test_db.py").is_file())
            saved = json.loads(app.CHECKPOINT_FILE.read_text())
            for role in expected:
                self.assertTrue(saved[app.CKPT_FIELD[role]])
            if with_data:
                self.assertTrue((product / "migrations/002_accounts.sql").is_file())
                self.assertTrue(saved["include_data"])
                self.assertIn("Modelo aprobado", stub.prompt_de("backend"))
                checkpoint_before = app.CHECKPOINT_FILE.read_text()
                with patch.dict("os.environ", {"GUARDRAILS_INCLUDE_DATA": "false"}):
                    with self.assertRaises(SystemExit):
                        app.main()
                self.assertEqual(app.CHECKPOINT_FILE.read_text(), checkpoint_before)
            self.assertEqual(saved["last_verdict"], "aprobado")
            self.assertIn("Hallazgo Alta", saved["analysis"])


if __name__ == "__main__":
    unittest.main()
