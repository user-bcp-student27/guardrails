"""
Estado compartido del workflow LangGraph — pipeline de refactorización S4.

Misma forma que S3 (el repo real entra por `snapshot`, no un brief fijo), más
los campos del **review loop**, que es lo que S4 agrega: el chapter no sólo
aprueba o rechaza, deja observaciones que el developer debe incorporar en la
ronda siguiente.

Lo siembra el harness (`harness.run_with_harness`) y lo hace avanzar `main.py`,
un nodo por vez.
"""

from typing import TypedDict


class WorkflowState(TypedDict):
    """
    Estado del pipeline de refactorización de un repositorio real.

    Entrada (la siembra el harness antes del primer nodo):
        repo_name    : "owner/repo" del repositorio clonado.
        snapshot     : Código leído del repo. NO es un brief fijo: cambia con
                       cada repositorio, y de ahí salen los hallazgos reales.
        project_type : "backend" | "frontend" | "ambos". Determina qué nodos
                       de implementación corren y cuáles se saltan.
        context      : instructions + constitution + policies, cargados una
                       sola vez por el harness. Cada nodo decide cómo usarlo.
                       Cadena vacía = modo libre, sin constitución.

    Outputs acumulados, uno por nodo:
        out_analysis : Problemas de calidad detectados en el código real.
        out_plan     : Plan de refactorización del arquitecto.
        out_backend  : Archivos backend refactorizados (formato ### ruta/archivo).
        out_frontend : Archivos frontend/API refactorizados (mismo formato).
        out_chapter : Veredicto técnico del Chapter Chapter.

    Review loop — lo propio de S4:
        review_round    : Ronda actual del ciclo dev↔chapter (arranca en 0).
        review_feedback : Observaciones de la ronda anterior. Los nodos backend
                          y frontend las inyectan en su prompt cuando
                          review_round >= 1, para que el dev corrija en vez de
                          regenerar a ciegas.
        last_verdict    : "aprobado" | "observaciones" | "rechazado", según
                          `parse_verdict()`. Es lo que lee `should_retry()`.
        approved        : True sólo con APROBADO limpio. Atajo booleano de
                          last_verdict, para no repetir la comparación.
    """

    # ── Entrada ──────────────────────────────────────────────────
    repo_name: str
    snapshot: str
    project_type: str
    context: str
    include_data: bool

    # ── Outputs por nodo ─────────────────────────────────────────
    out_architect: str
    out_analysis: str
    out_plan: str
    out_data: str
    out_backend: str
    out_testing: str
    out_security: str
    out_frontend: str
    out_chapter: str

    # ── Review loop (S4) ─────────────────────────────────────────
    review_round: int
    review_feedback: str
    last_verdict: str
    approved: bool
