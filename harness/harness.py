"""
Harness Engineering — S4.

El harness es la ÚNICA puerta de entrada al graph. `main.py` no instancia
modelos ni arma prompts: siembra el estado aquí y hace avanzar el pipeline
nodo a nodo.

Misma API que el harness de S3, con lo propio de S4 encima:

  · **Contexto.** Carga instructions.md + constitution.md + allowed_tools.json
    UNA sola vez y los deja en `state["context"]`. Sin harness, `context` viaja
    vacío y los nodos corren en modo libre — esa es la diferencia que mide
    `compare()`.

  · **Control paso a paso.** `step()` ejecuta un solo nodo y devuelve el
    control, para que main.py muestre la salida y pida aprobación.

  · **Review loop.** El estado arrastra `review_round`, `review_feedback` y
    `last_verdict`. Al reanudar hay que restaurarlos, o el developer pierde
    las observaciones del chapter.

Uso típico desde main.py:

    from harness.harness import run_with_harness, next_node, step, retry_node

    run_with_harness(repo_name, snapshot, project_type, thread_id="s4-01")
    while next_node(thread_id):
        nodo, estado = step(thread_id)
        # ... mostrar salida, pedir [s/r/n], eventualmente retry_node(...)
"""
import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

from graph.graph import graph, NODES, MAX_REVIEW_ROUNDS, NODE_FUNCTIONS, DATA_NODE_FUNCTIONS, build_graph, include_data
from graph.state import WorkflowState

# ── Rutas del harness ─────────────────────────────────────────────
_BASE          = Path(__file__).parent
_INSTRUCTIONS  = _BASE / "context"  / "instructions.md"
_CONSTITUTION  = _BASE / "context"  / "constitution.md"
_ALLOWED_TOOLS = _BASE / "policies" / "allowed_tools.json"
_EVIDENCE_DIR  = _BASE.parent / "evidence"
_EVIDENCE_DIR.mkdir(exist_ok=True)

# Nodo → función, para reejecutar uno solo en el retry manual.
_NODE_FNS = DATA_NODE_FUNCTIONS
_data_graph = None
_data_threads = set()


def _session_graph(thread_id):
    return _data_graph if thread_id in _data_threads else graph

STATE_FIELD = {
    "architect": "out_architect",
    "data": "out_data",
    "backend": "out_backend",
    "frontend": "out_frontend",
    "testing": "out_testing",
    "security": "out_security",
    "chapter": "out_chapter",
}


# Artefactos que el participante dejó en su repo los Días 1-3. Son la
# fuente de verdad del producto: complementan la constitución genérica.
_PRODUCT_DOCS = (
    "REGLAS.md",                    # Día 1: reglas del laboratorio
    "docs/GOOD_PRACTICES.md",       # Día 1: convenciones del código
    "docs/ENDPOINTS.md",            # Día 1: contrato que no se rompe
    "HALLAZGOS.md",                 # Día 1: hallazgos con Id H-xx
    ".github/skills/reqflow-definition-of-done/SKILL.md",  # Día 3: DoD
)
_PRODUCT_GLOBS = ("openspec/specs/**/*.md",)  # Día 3: specs aprobadas
_MAX_DOC_CHARS = 4000


def load_product_context(repo_dir: Path | None) -> str:
    """Lee los artefactos de los Días 1-3 presentes en el repo clonado."""
    if repo_dir is None:
        return ""
    files = [repo_dir / rel for rel in _PRODUCT_DOCS]
    for pattern in _PRODUCT_GLOBS:
        files += sorted(repo_dir.glob(pattern))
    parts = [
        f"--- {f.relative_to(repo_dir).as_posix()} ---\n"
        + f.read_text(encoding="utf-8", errors="ignore")[:_MAX_DOC_CHARS]
        for f in files if f.is_file()
    ]
    if not parts:
        print("  ⚠️  El repo no trae artefactos de los Días 1-3 (REGLAS, ENDPOINTS, specs...).")
        return ""
    print(f"  [HARNESS] Contexto del producto: {len(parts)} artefactos del repo.")
    return "=== CONTEXTO DEL PRODUCTO (Días 1-3, prevalece sobre supuestos) ===\n" + "\n\n".join(parts)


def load_issue_context(repo_full: str) -> str:
    """Trae el Issue del Project ReqFlow indicado en GITHUB_ISSUE (vía `gh`)."""
    number = os.getenv("GITHUB_ISSUE", "").strip()
    if not number or not repo_full:
        return ""
    r = subprocess.run(
        ["gh", "issue", "view", number, "--repo", repo_full, "--json", "number,title,body"],
        capture_output=True, text=True, encoding="utf-8",
    )
    if r.returncode != 0:
        print(f"  ⚠️  No se pudo leer el issue #{number}: {r.stderr.strip()[:200]}")
        return ""
    issue = json.loads(r.stdout)
    print(f"  [HARNESS] Alcance: issue #{issue['number']} — {issue['title']}")
    return (
        "=== ALCANCE: ISSUE DEL PROJECT REQFLOW (no resuelvas nada fuera de él) ===\n"
        f"#{issue['number']} {issue['title']}\n\n{issue['body']}"
    )


# ── Carga de contexto ─────────────────────────────────────────────
def load_context(repo_dir: Path | None = None, repo_full: str = "") -> str:
    """
    Arma el system context: instrucciones + constitución + política de
    herramientas + artefactos del producto + issue en alcance. Cadena vacía
    si falta la constitución — en ese caso el pipeline corre en modo libre.
    """
    try:
        instructions = _INSTRUCTIONS.read_text(encoding="utf-8")
        constitution = _CONSTITUTION.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        print(f"  ⚠️  harness/context/ no encontrado ({e.filename}) — sin constitución.")
        return ""

    bloques = [
        "=== INSTRUCCIONES DEL SISTEMA ===\n" + instructions,
        "=== CONSTITUCIÓN (restricciones obligatorias) ===\n" + constitution,
    ]

    # Las policies son opcionales: si faltan, el resto del contexto sigue valiendo.
    try:
        pol = json.loads(_ALLOWED_TOOLS.read_text(encoding="utf-8"))
        bloques.append(
            "=== POLÍTICAS DE HERRAMIENTAS ===\n"
            f"Permitidas: {', '.join(pol.get('allowed', []))}\n"
            f"Bloqueadas: {', '.join(pol.get('blocked', []))}"
        )
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"  ⚠️  allowed_tools.json no utilizable ({e}) — contexto sin políticas.")

    bloques += filter(None, [load_product_context(repo_dir), load_issue_context(repo_full)])

    ctx = "\n\n".join(bloques)
    print(f"  [HARNESS] Contexto cargado ({len(ctx)} chars).")
    return ctx


# ── Manejo de sesión del graph ────────────────────────────────────
def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def state(thread_id: str) -> dict:
    """Estado actual del pipeline para ese thread."""
    return _session_graph(thread_id).get_state(_config(thread_id)).values


def next_node(thread_id: str) -> str | None:
    """Nodo que se ejecutaría a continuación, o None si el pipeline terminó."""
    nxt = _session_graph(thread_id).get_state(_config(thread_id)).next
    return nxt[0] if nxt else None


def _start(repo_name: str, snapshot: str, project_type: str,
           thread_id: str, context: str) -> dict:
    """Siembra el estado inicial. El graph queda pausado ANTES del primer nodo."""
    global _data_graph
    with_data = include_data()
    if with_data:
        if _data_graph is None:
            _data_graph = build_graph(True)
        _data_threads.add(thread_id)
    else:
        _data_threads.discard(thread_id)
    inicial: WorkflowState = {
        "include_data": with_data,
        "out_data": "",
        "repo_name":       repo_name,
        "snapshot":        snapshot,
        "project_type":    project_type,
        "context":         context,
        "out_architect":   "",
        "out_analysis":    "",
        "out_plan":        "",
        "out_backend":     "",
        "out_frontend":    "",
        "out_testing":     "",
        "out_security":    "",
        "out_chapter":    "",
        "review_round":    0,
        "review_feedback": "",
        "last_verdict":    "",
        "approved":        False,
    }
    _session_graph(thread_id).invoke(inicial, _config(thread_id))
    return state(thread_id)


def run_with_harness(repo_name: str, snapshot: str, project_type: str,
                     thread_id: str, repo_dir: Path | None = None) -> dict:
    """
    Arranca el pipeline CON el contexto del harness inyectado.
    No ejecuta ningún nodo: deja el graph listo y pausado.
    """
    print(f"\n[harness] Arrancando CON harness (thread: {thread_id})")
    return _start(repo_name, snapshot, project_type, thread_id,
                  load_context(repo_dir, repo_name))


def run_without_harness(repo_name: str, snapshot: str, project_type: str,
                        thread_id: str, repo_dir: Path | None = None) -> dict:
    """
    Arranca el pipeline SIN contexto: los nodos corren en modo libre.
    Es la rama de control de `compare()`.
    """
    print(f"\n[harness] Arrancando SIN harness (thread: {thread_id})")
    return _start(repo_name, snapshot, project_type, thread_id, "")


def resume_with_harness(repo_name: str, snapshot: str, project_type: str,
                        thread_id: str, outputs: dict,
                        repo_dir: Path | None = None) -> dict:
    """
    Retoma una sesión anterior a partir de los artefactos ya aprobados.

    El MemorySaver vive en proceso: al reiniciar `main.py` el estado del graph
    se perdió, aunque el checkpoint en disco conserve los artefactos. Aquí
    resembramos y adelantamos con `update_state(as_node=...)` sin reejecutar
    ningún nodo.

    Args:
        outputs: campos ya resueltos. Además de los `out_*`, conviene pasar
                 `review_round` y `review_feedback`: sin ellos el developer
                 perdería las observaciones del chapter al retomar.

    Returns:
        Estado resultante. `next_node()` apunta al primer nodo sin salida.
    """
    if outputs.get("include_data", False) != include_data():
        raise ValueError("El checkpoint pertenece a otro flujo de seis o siete nodos")
    _start(repo_name, snapshot, project_type, thread_id,
           load_context(repo_dir, repo_name))
    cfg = _config(thread_id)

    architecture = {key: outputs[key] for key in ("out_analysis", "out_plan") if key in outputs}
    if architecture:
        _session_graph(thread_id).update_state(cfg, architecture)

    # Restaurar el contador y el feedback del review loop antes de avanzar.
    loop_state = {
        k: outputs[k]
        for k in ("review_round", "review_feedback", "last_verdict")
        if outputs.get(k)
    }
    if loop_state:
        _session_graph(thread_id).update_state(cfg, loop_state)

    resume_after = outputs.get("resume_after")
    active_nodes = DATA_NODE_FUNCTIONS if state(thread_id).get("include_data") else NODE_FUNCTIONS
    if resume_after in active_nodes:
        restored = {field: outputs[field] for node, field in STATE_FIELD.items()
                    if node in active_nodes and field in outputs}
        _session_graph(thread_id).update_state(cfg, restored, as_node=resume_after)
        return state(thread_id)

    adelantados = []
    for nodo in (DATA_NODE_FUNCTIONS if state(thread_id).get("include_data") else NODE_FUNCTIONS):
        valor = outputs.get(STATE_FIELD[nodo], "")
        if not valor:
            break                       # primer nodo pendiente: aquí seguimos
        _session_graph(thread_id).update_state(cfg, {STATE_FIELD[nodo]: valor}, as_node=nodo)
        adelantados.append(nodo)

    if adelantados:
        print(f"[harness] Adelantado sin reejecutar: {' → '.join(adelantados)}")
    if loop_state.get("review_round"):
        print(f"[harness] Review loop restaurado en ronda {loop_state['review_round']}")
    return state(thread_id)


def step(thread_id: str) -> tuple[str | None, dict]:
    """
    Ejecuta EXACTAMENTE un nodo y devuelve (nombre_del_nodo, estado).
    Si el pipeline ya terminó, devuelve (None, estado).

    Depende de `interrupt_before` en graph.py: sin eso, un solo invoke
    correría el pipeline entero y se perdería el HITL.
    """
    nodo = next_node(thread_id)
    if nodo is None:
        return None, state(thread_id)
    _session_graph(thread_id).invoke(None, _config(thread_id))
    return nodo, state(thread_id)


def retry_node(thread_id: str, nodo: str) -> dict:
    """
    Reejecuta un nodo ya corrido y reinyecta su salida en el estado.
    Lo usa el `[r]` del HITL cuando el humano no acepta la salida.
    """
    if nodo not in _NODE_FNS or nodo == "data" and thread_id not in _data_threads:
        raise ValueError(f"nodo desconocido: {nodo} (esperaba uno de {NODES})")
    cfg = _config(thread_id)
    salida = _NODE_FNS[nodo](_session_graph(thread_id).get_state(cfg).values)
    _session_graph(thread_id).update_state(cfg, salida, as_node=nodo)
    return state(thread_id)


def run_to_end(thread_id: str, max_steps: int = 60) -> dict:
    """Corre el pipeline hasta el final sin HITL. Lo usan compare() y evals/."""
    for _ in range(max_steps):
        if next_node(thread_id) is None:
            break
        _session_graph(thread_id).invoke(None, _config(thread_id))
    return state(thread_id)


# ── Evidencia ─────────────────────────────────────────────────────
def save_evidence(filename: str, content: str) -> Path:
    """Guarda un artefacto de evidencia en evidence/."""
    path = _EVIDENCE_DIR / filename
    path.write_text(content, encoding="utf-8")
    print(f"[harness] Evidencia guardada: {path.name}")
    return path


def _evidence_for(modo: str, thread_id: str, resultado: dict, elapsed: float) -> dict:
    return {
        "mode":          modo,
        "thread_id":     thread_id,
        "timestamp":     datetime.now().strftime("%Y%m%d_%H%M%S"),
        "elapsed_s":     elapsed,
        "repo":          resultado.get("repo_name", ""),
        "project_type":  resultado.get("project_type", ""),
        "approved":      resultado.get("approved"),
        "last_verdict":  resultado.get("last_verdict", ""),
        "review_rounds": resultado.get("review_round", 0),
        "max_rounds":    MAX_REVIEW_ROUNDS,
        "context_chars": len(resultado.get("context", "")),
        "output_chars": {
            k: len(resultado.get(k, "") or "")
            for k in ("out_analysis", "out_plan", "out_data", "out_backend",
                      "out_frontend", "out_testing", "out_security", "out_chapter")
        },
    }


# ── Compliance ────────────────────────────────────────────────────
def check_security_compliance(response_text: str) -> dict[str, bool]:
    """
    Verifica reglas de seguridad con patrones deterministas sobre el texto
    generado. Única implementación: `main.py` y `evals/` la importan de aquí.
    """
    # ponytail: heurística por palabras clave, mide si el output ATIENDE cada
    # regla, no si la cumple; la verificación real son los tests y contract_evals.
    t = (response_text or "").lower()
    has = lambda *words: any(w in t for w in words)  # noqa: E731
    return {
        "R1_contrato_preservado":  has("/auth/login", "/api/tasks", "/api/summary",
                                       "/api/requirements", "endpoints.md", "contrato")
                                   and "breaking change" not in t,
        "R2_secretos_por_entorno": has("getenv", "environ", "settings", "auth_token_secret",
                                       ".env", "variable de entorno"),
        "R3_datos_protegidos":     has("parametriz", "parameteriz", "preparedstatement",
                                       "placeholder", "hash", "bcrypt", "argon2", "pbkdf2"),
        "R4_token_firmado_y_exp":  has("token", "jwt") and has("exp", "expir")
                                   and has("firm", "sign", "hmac", "hs256", "rs256"),
        "R5_errores_genericos":    has("stack trace", "mensaje genérico", "mensajes genéricos",
                                       "generic", "exception_handler", "httpexception",
                                       "exceptionmapper", "innerhtml"),
        "R6_pruebas_y_trazabilidad": has("test", "prueba", "pytest")
                                     and re.search(r"\bh-\d+|#\d+", t) is not None,
    }


def _texto_evaluable(resultado: dict) -> str:
    """Concatena lo que producen los agentes, que es lo que se audita."""
    return " ".join(filter(None, [
        resultado.get("out_data", ""),
        resultado.get("out_backend", ""),
        resultado.get("out_frontend", ""),
        resultado.get("out_testing", ""),
        resultado.get("out_security", ""),
        resultado.get("out_chapter", ""),
    ])) or str(resultado)


# ── Comparativa SIN vs CON ────────────────────────────────────────
def compare(repo_name: str, snapshot: str, project_type: str, run_id: str,
            repo_dir: Path | None = None) -> dict:
    """
    Corre el pipeline completo dos veces sobre el MISMO repo —sin y con
    constitución— y devuelve el compliance de cada rama.

    Corre sin HITL: es una medición, no una sesión interactiva. Además del
    score reporta cuántas rondas de review consumió cada rama, que en S4 es
    tan informativo como el compliance: con constitución el chapter suele
    aprobar antes.

    Returns:
        {
          "without_harness": {"response", "compliance", "score", "rounds"},
          "with_harness":    {...},
        }
    """
    salida = {}
    for modo, arranque in (("without_harness", run_without_harness),
                           ("with_harness",    run_with_harness)):
        thread = f"{run_id}-{'sin' if modo.startswith('without') else 'con'}"
        inicio = time.time()
        arranque(repo_name, snapshot, project_type, thread, repo_dir)
        resultado = run_to_end(thread)
        elapsed = round(time.time() - inicio, 2)

        texto = _texto_evaluable(resultado)
        compliance = check_security_compliance(texto)
        score = f"{sum(compliance.values())}/{len(compliance)}"
        rondas = resultado.get("review_round", 0)

        ev = _evidence_for(modo, thread, resultado, elapsed)
        ev["compliance"] = compliance
        ev["score"] = score
        save_evidence(f"run_{modo}.json", json.dumps(ev, indent=2, ensure_ascii=False))
        save_evidence(f"run_{modo}.txt", texto)

        salida[modo] = {"response": texto, "compliance": compliance,
                        "score": score, "rounds": rondas}
        print(f"[harness] {modo}: {score} reglas · {rondas} ronda(s) de review ({elapsed}s)")

    return salida
