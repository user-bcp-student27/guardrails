# -*- coding: utf-8 -*-
"""Check del harness y los guardrails de S4 — no llama al LLM, MCP ni git.

  1. `load_context()` arma instructions + constitution + policies.
  2. `run_with_harness()` siembra SIN ejecutar nodos; `run_without_harness()`
     deja `context` vacio.
  3. `step()` avanza exactamente un nodo por llamada.
  4. `retry_node()` reejecuta y reinyecta la salida.
  5. `resume_with_harness()` restaura tambien el review loop (ronda +
     feedback), no solo los artefactos.
  6. Guardrail de entrada: corta snapshots vacios, chicos o con secretos.
  7. Guardrail de salida: valida el state REAL del pipeline.

Ejecutar:  .venv\\Scripts\\python.exe test_harness_flow.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import graph.nodes as nodes
from harness import harness as H
from guardrails.input_check import validate_snapshot
from guardrails.output_check import validate_output, validate_chapter_verdict
from test_graph_hitl import Stub, SNAP

REPO = "OWNER/workshop-requirements-api"


def test_load_context():
    ctx = H.load_context()
    assert ctx, "load_context() devolvio vacio: faltan archivos del harness"
    for marca in ("INSTRUCCIONES DEL SISTEMA", "CONSTITUCIÓN", "POLÍTICAS DE HERRAMIENTAS"):
        assert marca in ctx, f"el contexto no incluye '{marca}'"
    print(f"1. load_context(): {len(ctx)} chars, 3 bloques  OK")


def test_siembra_sin_ejecutar():
    nodes._invoke = Stub(["APROBADO"])
    st = H.run_with_harness(REPO, SNAP, "ambos", "t-con")
    assert not nodes._invoke.calls, "sembrar NO debe ejecutar nodos"
    assert st["context"], "run_with_harness deberia poblar context"
    assert st["review_round"] == 0
    assert H.next_node("t-con") == "architect"

    st2 = H.run_without_harness(REPO, SNAP, "ambos", "t-sin")
    assert st2["context"] == "", "run_without_harness deberia dejar context vacio"
    print("2. Siembra: 0 nodos ejecutados; context poblado con harness, vacio sin el  OK")


def test_step_un_nodo():
    nodes._invoke = Stub(["APROBADO"])
    H.run_with_harness(REPO, SNAP, "ambos", "t-step")

    recorrido = []
    while H.next_node("t-step") is not None:
        antes = len(nodes._invoke.calls)
        nodo, _ = H.step("t-step")
        assert len(nodes._invoke.calls) - antes == 1, f"step('{nodo}') disparo mas de 1 llamada"
        recorrido.append(nodo)

    assert recorrido == ["architect", "backend", "frontend", "testing", "security", "chapter"], recorrido
    assert H.step("t-step")[0] is None, "step() sobre un pipeline terminado deberia dar None"
    print(f"3. step(): 1 nodo por llamada  ->  {' -> '.join(recorrido)}  OK")


def test_retry_node():
    nodes._invoke = Stub(["APROBADO"])
    H.run_with_harness(REPO, SNAP, "ambos", "t-retry")
    H.step("t-retry")
    primero = H.state("t-retry")["out_architect"]

    base = Stub(["APROBADO"])
    nodes._invoke = lambda p: base(p) + "\n<<reintento>>"
    estado = H.retry_node("t-retry", "architect")

    assert "<<reintento>>" in estado["out_architect"], "retry_node no reemplazo la salida"
    assert estado["out_architect"] != primero
    assert H.next_node("t-retry") == "backend"
    print("4. retry_node(): reejecuta y reinyecta la salida  OK")


def test_resume_restaura_review_loop():
    """Lo propio de S4: al retomar no se pierden las observaciones."""
    nodes._invoke = Stub(["APROBADO"])
    H.resume_with_harness(
        REPO, SNAP, "ambos", "t-resume",
        outputs={
            "out_architect": "## Analysis\nanalisis ya aprobado\n## Plan\nplan ya aprobado",
            "out_analysis":    "analisis ya aprobado",
            "out_plan":        "plan ya aprobado",
            "out_backend":     "",
            "out_frontend":    "",
            "out_chapter":    "",
            "review_round":    1,
            "review_feedback": "falta manejo de errores en app/db.py",
        },
    )
    assert not nodes._invoke.calls, "el resume NO debe reejecutar nodos"

    st = H.state("t-resume")
    assert st["out_analysis"] == "analisis ya aprobado"
    assert st["review_round"] == 1, f"review_round={st['review_round']}, esperaba 1"
    assert "app/db.py" in st["review_feedback"], "se perdio el feedback del chapter"
    assert H.next_node("t-resume") == "backend"

    # Y el dev que sigue debe recibir esas observaciones.
    espia = Stub(["APROBADO"])
    nodes._invoke = espia
    H.step("t-resume")
    p = espia.prompt_de("backend")
    assert "OBSERVACIONES DE LA RONDA ANTERIOR" in p, \
        "tras el resume el dev deberia recibir el feedback"
    assert "app/db.py" in p
    print("5. resume: adelanta sin reejecutar y conserva ronda + feedback  OK")


def test_guardrail_entrada():
    casos = [
        ("", False, "vacio"),
        ("corto", False, "demasiado chico"),
        ("### app.py\n" + "x = 1\n" * 100, True, "valido"),
        ("### c.py\n" + "x=1\n" * 100 + "KEY='sk-abcdefghij1234567890'", False, "secreto"),
        ("### c.py\n" + "x=1\n" * 100 + "card='4111111111111111'", False, "PII"),
    ]
    for texto, esperado, etiqueta in casos:
        r = validate_snapshot(texto)
        assert r.valid is esperado, f"snapshot {etiqueta}: valid={r.valid}, esperaba {esperado}"
    print(f"6. Guardrail de entrada: {len(casos)} casos (vacio, chico, valido, secreto, PII)  OK")


def test_guardrail_salida():
    bueno = {
        "out_analysis": "Hallazgo 1: config hardcodeada en app/db.py. Severidad Alta." * 2,
        "out_plan":     "Objetivo 1: extraer configuracion a variables de entorno." * 2,
        "out_backend":  "### app/db.py\n```python\nimport os\n```",
        "out_frontend": "",
        "out_testing": "Casos de prueba con comandos reproducibles. Estado NO EJECUTADO. " * 2,
        "out_security": "Revisión con evidencia y verificaciones de seguridad pendientes. " * 2,
        "out_chapter": ("Los cambios cumplen el plan y no introducen riesgos. "
                         "Calificacion 8/10. Veredicto final: APROBADO"),
        "review_round": 1,
    }
    r = validate_output(bueno)
    assert r.valid, f"el state valido fue rechazado: [{r.rule_code}] {r.reason}"

    fallos = [
        ({**bueno, "out_analysis": "corto"},                              "OUT-01"),
        ({**bueno, "out_chapter": "Revise los cambios con detenimiento pero "
                                   "prefiero no pronunciarme todavia."},   "OUT-02"),
        ({**bueno, "out_backend": "", "out_frontend": ""},                "OUT-03"),
        ({**bueno, "out_backend": "cambie cosas pero sin cabecera"},      "OUT-03"),
        ({**bueno, "out_plan": bueno["out_plan"] + " key=sk-abcdefghij1234567890"}, "OUT-04"),
    ]
    for estado, codigo in fallos:
        r = validate_output(estado)
        assert not r.valid, f"esperaba fallo {codigo}, paso"
        assert r.rule_code == codigo, f"esperaba {codigo}, dio {r.rule_code}"

    # El veredicto conservador: "con observaciones" NO aprueba.
    assert validate_chapter_verdict("VEREDICTO: APROBADO").valid
    assert not validate_chapter_verdict("APROBADO CON OBSERVACIONES").valid
    assert not validate_chapter_verdict("RECHAZADO").valid
    assert not validate_chapter_verdict("bla bla").valid
    print(f"7. Guardrail de salida: 1 valido + {len(fallos)} fallos por codigo, "
          "veredicto conservador  OK")


def test_contexto_del_producto():
    """El harness suma los artefactos de los Dias 1-3 que trae el repo clonado."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        (repo / "REGLAS.md").write_text("Cobertura minima 85%", encoding="utf-8")
        spec = repo / "openspec" / "specs" / "tasks"
        spec.mkdir(parents=True)
        (spec / "spec.md").write_text("Scenario: GET /api/tasks devuelve 200", encoding="utf-8")
        ctx = H.load_context(repo)
    assert "=== CONTEXTO DEL PRODUCTO" in ctx
    assert "Cobertura minima 85%" in ctx and "GET /api/tasks devuelve 200" in ctx
    assert "=== CONTEXTO DEL PRODUCTO" not in H.load_context(), "sin repo no hay bloque de producto"
    print("8. load_context(repo_dir): suma REGLAS.md y openspec/specs del repo  OK")


def test_gate_g2():
    """La rubrica ponderada suma 100 con todo en verde y corta bajo 80."""
    from evals.gate_g2 import gate_g2, CRITERIA
    ok = [{"test": c, "passed": True} for _, checks in CRITERIA.values() if checks for c in checks]
    assert gate_g2(ok, {"R1": True}, pr_opened=True)["score"] == 100.0
    sin_pr = gate_g2(ok, {"R1": True, "R2": False}, pr_opened=False)
    assert sin_pr["score"] == 75.0 and not sin_pr["passed"], sin_pr
    print("9. Gate G2: 100% con todo OK, 75% sin PR y con media constitucion -> no pasa  OK")


def main():
    test_load_context()
    test_siembra_sin_ejecutar()
    test_step_un_nodo()
    test_retry_node()
    test_resume_restaura_review_loop()
    test_guardrail_entrada()
    test_guardrail_salida()
    test_contexto_del_producto()
    test_gate_g2()
    print("\nTodo OK — harness y guardrails de S4 verificados.")


if __name__ == "__main__":
    main()
