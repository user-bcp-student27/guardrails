"""Optional seventh node, real LangGraph pauses and persisted mode, offline."""
import contextlib
import io
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
import graph.nodes as nodes
from graph.graph import include_data, MAX_REVIEW_ROUNDS
from harness import harness as H
from test_graph_hitl import Stub
from guardrails.output_check import validate_output
import main as app


class DataWorkflowTests(unittest.TestCase):
    def test_seventh_order_review_feedback_resume_and_human_retry(self):
        stub = Stub(["APROBADO CON OBSERVACIONES", "APROBADO"])
        data_prompts = []
        def invoke(prompt):
            if prompt.startswith("Eres el responsable de Datos y persistencia"):
                data_prompts.append(prompt)
                stub.calls.append("data")
                return "Contrato de datos aprobado. NO EJECUTADO. " * 3
            return stub(prompt)
        with patch.dict("os.environ", {"GUARDRAILS_INCLUDE_DATA": "true"}), patch.object(nodes, "_invoke", side_effect=invoke), contextlib.redirect_stdout(io.StringIO()):
            H.run_without_harness("team/product", "schema actual", "ambos", "data-first")
            self.assertEqual(H.next_node("data-first"), "architect")
            H.step("data-first")
            self.assertEqual(H.next_node("data-first"), "data")
            H.step("data-first")
            saved = H.state("data-first")
            self.assertEqual(H.next_node("data-first"), "backend")
            with patch.object(app, "THREAD_ID", "data-first"), patch("builtins.input", side_effect=["r", "s"]):
                self.assertIn("Contrato de datos", app._hitl_node("data", saved))
            with patch("builtins.input", return_value="n"), self.assertRaises(SystemExit):
                app._hitl_node("data", H.state("data-first"))
            before = len(stub.calls)
            H.resume_with_harness("team/product", "schema actual", "ambos", "data-resumed", H.state("data-first"))
            self.assertEqual(len(stub.calls), before)
            self.assertEqual(H.next_node("data-resumed"), "backend")
            final = H.run_to_end("data-resumed")
            self.assertTrue(final["approved"])
            expected_round = ["data", "backend", "frontend", "testing", "security", "chapter"]
            self.assertEqual(stub.calls, ["architect", "data", *expected_round, *expected_round])
            self.assertIn("OBSERVACIONES DE LA RONDA ANTERIOR", data_prompts[-1])
            for role in ("backend", "testing", "security", "chapter"):
                self.assertIn("Contrato de datos aprobado", stub.prompt_de(role))
            # Resume after Data in a later review round without replaying stale downstream reports.
            stub.veredictos = ["APROBADO CON OBSERVACIONES", "APROBADO"]
            H.run_without_harness("team/product", "schema actual", "ambos", "data-mid-round")
            for _ in range(8):
                H.step("data-mid-round")
            partial = {**H.state("data-mid-round"), "resume_after": "data"}
            H.resume_with_harness("team/product", "schema actual", "ambos", "data-mid-restored", partial)
            self.assertEqual(H.next_node("data-mid-restored"), "backend")
            with patch.dict("os.environ", {"GUARDRAILS_INCLUDE_DATA": "false"}), self.assertRaises(ValueError):
                H.resume_with_harness("team/product", "schema actual", "ambos", "wrong-mode", final)

    def test_activation_and_output_gate(self):
        with patch.dict("os.environ", {"GUARDRAILS_INCLUDE_DATA": "bad"}), self.assertRaises(ValueError):
            include_data()
        incomplete = {"include_data": True, "out_analysis": "a" * 60, "out_plan": "b" * 60,
                      "out_testing": "t" * 60, "out_security": "s" * 60, "out_chapter": "aprobado " * 10}
        self.assertEqual(validate_output(incomplete).field, "out_data")
        with_data = {**incomplete, "out_data": "d" * 60, "out_backend": "### app.py\n```python\nvalue=1\n```"}
        self.assertTrue(validate_output(with_data).valid)
        self.assertEqual(validate_output({**with_data, "out_data": "d" * 60 + " ghp_secret"}).rule_code, "OUT-04")

    def test_data_review_loop_respects_round_limit(self):
        stub = Stub(["RECHAZADO"] * MAX_REVIEW_ROUNDS)
        def invoke(prompt):
            if prompt.startswith("Eres el responsable de Datos y persistencia"):
                stub.calls.append("data")
                return "Datos propuestos. NO EJECUTADO."
            return stub(prompt)
        with patch.dict("os.environ", {"GUARDRAILS_INCLUDE_DATA": "true"}), patch.object(nodes, "_invoke", side_effect=invoke), contextlib.redirect_stdout(io.StringIO()):
            H.run_without_harness("team/product", "schema actual", "frontend", "data-limit")
            final = H.run_to_end("data-limit")
        self.assertEqual(final["review_round"], MAX_REVIEW_ROUNDS)
        self.assertFalse(final["approved"])
        self.assertEqual(stub.calls.count("data"), MAX_REVIEW_ROUNDS)

    def test_snapshot_includes_existing_sql_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "schema.sql").write_text("CREATE TABLE existing_accounts(id INTEGER PRIMARY KEY);")
            snapshot, count = app._snapshot(root)
            self.assertEqual(count, 1)
            self.assertIn("existing_accounts", snapshot)
