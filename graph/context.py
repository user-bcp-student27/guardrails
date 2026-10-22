"""Contexto compartido por los nodos; no contiene prompts de roles."""

from graph.state import WorkflowState

_CTX_LIMIT = 4000


def _ctx(text: str, max_chars: int = _CTX_LIMIT) -> str:
    """Trunca el texto conservando inicio y fin, para no desbordar el prompt."""
    if not text or len(text) <= max_chars:
        return text
    half = max_chars // 2
    return (
        text[:half]
        + f"\n\n[... +{len(text) - max_chars} chars omitidos ...]\n\n"
        + text[-half:]
    )


def _runs(state: WorkflowState, area: str) -> bool:
    """True si el project_type elegido incluye esta área de implementación."""
    return state.get("project_type", "ambos") in (area, "ambos")


def _const_block(state: WorkflowState, nota: str) -> str:
    """Bloque de constitución para el prompt, vacío si se corre sin harness."""
    ctx = state.get("context", "")
    return f"\n{ctx}\n\n⚠️  {nota}\n\n" if ctx else ""


def _ronda(state: WorkflowState) -> int:
    """
    Ronda del dev que está por correr. `review_round` cuenta las revisiones
    ya emitidas (lo incrementa el chapter), así que el dev va una adelante.
    """
    return state.get("review_round", 0) + 1


def _feedback_block(state: WorkflowState) -> str:
    """Observaciones de la ronda anterior — el corazón del review loop de S4."""
    if state.get("review_round", 0) < 1:
        return ""  # primera ronda: todavía no hubo review
    feedback = state.get("review_feedback", "")
    if not feedback:
        return ""
    return (
        "\n⚠️  OBSERVACIONES DE LA RONDA ANTERIOR — incorpóralas TODAS:\n"
        f"{_ctx(feedback, 1500)}\n\n"
    )
