# -*- coding: utf-8 -*-
"""Check del graph de S4 — no llama al LLM ni al MCP.

Verifica lo que el pipeline necesita garantizar:

  1. HITL vivo: con `interrupt_before`, cada `graph.invoke(None, config)`
     ejecuta EXACTAMENTE un nodo y vuelve a pausar.
  2. Review loop: mientras el veredicto no sea APROBADO limpio, vuelve a
     backend arrastrando las observaciones, hasta MAX_REVIEW_ROUNDS.
  3. El feedback llega: en la ronda 2 el prompt del dev incluye lo que el
     chapter observo en la ronda 1.
  4. El analisis sale del snapshot REAL, no de un brief fijo.

Ejecutar:  .venv\\Scripts\\python.exe test_graph_hitl.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import graph.nodes as nodes
from graph.graph import graph, NODES, MAX_REVIEW_ROUNDS


class Stub:
    """Reemplaza a nodes._invoke. Guarda prompts y guiona al chapter."""

    ROLES = [
        ("Eres el arquitecto de software del equipo", "architect"),
        ("Eres un desarrollador backend",   "backend"),
        ("Eres un desarrollador frontend/API",     "frontend"),
        ("Eres el Ingeniero de pruebas", "testing"),
        ("Eres el Revisor de seguridad", "security"),
        ("Eres el Chapter del equipo",           "chapter"),
    ]

    def __init__(self, veredictos):
        self.calls = []
        self.prompts = []
        self.veredictos = list(veredictos)

    def __call__(self, prompt: str) -> str:
        rol = next((r for marca, r in self.ROLES if marca in prompt), None)
        assert rol, f"prompt sin rol reconocible: {prompt[:90]!r}"
        self.calls.append(rol)
        self.prompts.append((rol, prompt))
        if rol == "architect":
            return "## Analysis\nHallazgo Alta: configuración en app/db.py.\n## Plan\nExtraer configuración de app/db.py y probar el contrato."
        if rol == "chapter":
            v = self.veredictos.pop(0) if self.veredictos else "APROBADO"
            return f"Veredicto final: {v}. Observacion: falta manejo de errores en app/db.py"
        return f"### app/demo.py\n```python\n# salida simulada de {rol}\n```"

    def prompt_de(self, rol, n=0):
        """N-esimo prompt que recibio ese rol."""
        del_rol = [p for r, p in self.prompts if r == rol]
        return del_rol[n] if n < len(del_rol) else ""


SNAP = "### app/db.py\n```python\nconn = sqlite3.connect('hardcoded.db')\n```"


def seed(thread_id, project_type="ambos", context="(constitucion simulada)",
         snapshot=SNAP, repo="owner/repo"):
    estado = {
        "repo_name": repo, "snapshot": snapshot,
        "project_type": project_type, "context": context,
        "out_analysis": "", "out_plan": "", "out_backend": "",
        "out_frontend": "", "out_chapter": "",
        "review_round": 0, "review_feedback": "", "last_verdict": "",
        "approved": False,
    }
    cfg = {"configurable": {"thread_id": thread_id}}
    graph.invoke(estado, cfg)
    return cfg


def siguiente(cfg):
    nxt = graph.get_state(cfg).next
    return nxt[0] if nxt else None


def correr_todo(cfg, tope=40):
    for _ in range(tope):
        if siguiente(cfg) is None:
            return
        graph.invoke(None, cfg)
    raise AssertionError("el pipeline no termino dentro del tope de pasos")


def test_hitl_un_nodo_por_invoke():
    nodes._invoke = Stub(["APROBADO"])
    cfg = seed("t-hitl")

    assert siguiente(cfg) == "architect"
    assert not nodes._invoke.calls, "sembrar el estado NO debe ejecutar nodos"

    recorrido = []
    for esperado in NODES:
        actual = siguiente(cfg)
        assert actual == esperado, f"esperaba pausa antes de '{esperado}', esta en '{actual}'"
        antes = len(nodes._invoke.calls)
        graph.invoke(None, cfg)
        assert len(nodes._invoke.calls) - antes == 1, f"'{esperado}' disparo mas de 1 llamada"
        recorrido.append(esperado)

    assert siguiente(cfg) is None
    print(f"1. HITL: {len(NODES)} nodos, 1 invoke = 1 nodo  ->  {' -> '.join(recorrido)}  OK")


def test_review_loop():
    # Observaciones, rechazo, y recien a la tercera aprueba.
    nodes._invoke = Stub(["APROBADO CON OBSERVACIONES", "RECHAZADO", "APROBADO"])
    cfg = seed("t-loop")
    correr_todo(cfg)

    vals = graph.get_state(cfg).values
    backends  = nodes._invoke.calls.count("backend")
    chapters = nodes._invoke.calls.count("chapter")
    assert chapters == 3, f"esperaba 3 revisiones, hubo {chapters}"
    assert backends == 3, f"esperaba 3 rondas de backend, hubo {backends}"
    assert vals["approved"] is True, "deberia terminar aprobado"
    assert vals["review_round"] == 3, f"review_round={vals['review_round']}"
    print(f"2. Review loop: {backends} rondas dev, {chapters} revisiones, "
          f"aprobado al final (max {MAX_REVIEW_ROUNDS})  OK")


def test_feedback_llega_al_dev():
    nodes._invoke = Stub(["APROBADO CON OBSERVACIONES", "APROBADO"])
    cfg = seed("t-feedback")
    correr_todo(cfg)

    stub = nodes._invoke
    p1 = stub.prompt_de("backend", 0)
    p2 = stub.prompt_de("backend", 1)
    assert p1 and p2, "esperaba dos pasadas por backend"
    assert "OBSERVACIONES DE LA RONDA ANTERIOR" not in p1, \
        "la ronda 1 no deberia traer feedback: todavia no hubo review"
    assert "OBSERVACIONES DE LA RONDA ANTERIOR" in p2, \
        "la ronda 2 DEBE traer las observaciones del chapter"
    assert "app/db.py" in p2, "el feedback concreto del chapter no llego al dev"
    print("3. Feedback: ronda 1 sin observaciones, ronda 2 con las del chapter  OK")


def test_analisis_sale_del_repo_real():
    """El hallazgo depende del snapshot, no de un brief fijo."""
    nodes._invoke = Stub(["APROBADO"])
    seed("t-repo-a", snapshot="### a.py\n```python\nSECRET = 'x'\n```", repo="org/alpha")
    graph.invoke(None, {"configurable": {"thread_id": "t-repo-a"}})
    p_a = nodes._invoke.prompt_de("architect")

    nodes._invoke = Stub(["APROBADO"])
    seed("t-repo-b", snapshot="### b.ts\n```ts\nconst q = 1\n```", repo="org/beta")
    graph.invoke(None, {"configurable": {"thread_id": "t-repo-b"}})
    p_b = nodes._invoke.prompt_de("architect")

    assert "org/alpha" in p_a and "SECRET" in p_a, "el analisis no recibio el repo A"
    assert "org/beta" in p_b and "const q" in p_b, "el analisis no recibio el repo B"
    assert p_a != p_b, "el prompt de analisis no cambia con el repo: sigue habiendo brief fijo"
    print("4. Analisis: el prompt cambia con el snapshot real, sin brief fijo  OK")


def test_salta_por_project_type():
    nodes._invoke = Stub(["APROBADO"])
    cfg = seed("t-backend-solo", project_type="backend")
    correr_todo(cfg)

    llamadas = nodes._invoke.calls
    assert "backend" in llamadas
    assert "frontend" not in llamadas, "frontend NO deberia correr con project_type=backend"
    print(f"5. project_type='backend': frontend saltado  ->  {llamadas}  OK")


def main():
    test_hitl_un_nodo_por_invoke()
    test_review_loop()
    test_feedback_llega_al_dev()
    test_analisis_sale_del_repo_real()
    test_salta_por_project_type()
    print("\nTodo OK — graph de S4 verificado (HITL + review loop + feedback + repo real).")


if __name__ == "__main__":
    main()
