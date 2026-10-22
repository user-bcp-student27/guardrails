"""
Guardrail de salida — S4.

Valida el estado final del graph antes de abrir el PR. Un output inválido no
se entrega: se rechaza y se registra con un código de regla, para que quede
claro qué falló y dónde.

Las reglas miran el estado del pipeline de refactorización — los campos que
los nodos realmente producen (`out_analysis`, `out_plan`, `out_backend`…),
no un brief generado.
"""
import re
from typing import NamedTuple


class OutputValidationResult(NamedTuple):
    valid:     bool
    reason:    str
    rule_code: str          # código de la regla que falló (vacío si OK)
    field:     str          # campo del state que falló (vacío si OK)


# ── Umbrales ──────────────────────────────────────────────────────
MIN_OUTPUT_CHARS = 50         # mínimo por campo obligatorio

# Campos que SIEMPRE debe producir el pipeline. backend/frontend quedan
# fuera: dependen de project_type y pueden estar legítimamente vacíos.
CAMPOS_OBLIGATORIOS = ("out_analysis", "out_plan", "out_testing", "out_security", "out_chapter")

# El chapter tiene que haber emitido un veredicto legible.
TERMINOS_VEREDICTO = ("aprobado", "rechazado")

# Los devs emiten archivos con cabecera "### ruta/archivo.ext". Sin eso,
# `_extract_files()` no puede escribir nada y el PR saldría vacío.
PATRON_ARCHIVO = re.compile(r"###\s*\S+\.\w+")

# Nada de esto puede viajar en el output.
TERMINOS_BLOQUEADOS: list[str] = [
    "sk-",
    "ghp_",
    "-----BEGIN",
    "password:",
    "contraseña:",
]


def validate_output(state: dict) -> OutputValidationResult:
    """
    Valida el estado final del graph antes del push y el PR.

    Args:
        state: WorkflowState final del pipeline de refactorización.

    Returns:
        OutputValidationResult con el resultado y el detalle del fallo.

    Reglas:
        OUT-01  campos obligatorios con contenido suficiente
        OUT-02  el chapter emitió un veredicto legible
        OUT-03  los devs produjeron bloques de archivo extraíbles
        OUT-04  no hay secretos en ningún output
    """
    # ── OUT-01: campos obligatorios ──────────────────────────────
    for campo in (*CAMPOS_OBLIGATORIOS, *(("out_data",) if state.get("include_data") else ())):
        contenido = (state.get(campo) or "").strip()
        if len(contenido) < MIN_OUTPUT_CHARS:
            return OutputValidationResult(
                False,
                f"Output insuficiente en '{campo}' "
                f"(mínimo {MIN_OUTPUT_CHARS} caracteres, recibido {len(contenido)})",
                "OUT-01",
                campo,
            )

    # ── OUT-02: veredicto legible ────────────────────────────────
    chapter = (state.get("out_chapter") or "").lower()
    if not any(t in chapter for t in TERMINOS_VEREDICTO):
        return OutputValidationResult(
            False,
            "El chapter no emitió un veredicto legible "
            "(se esperaba APROBADO / APROBADO CON OBSERVACIONES / RECHAZADO)",
            "OUT-02",
            "out_chapter",
        )

    # ── OUT-03: los devs produjeron archivos extraíbles ──────────
    # Al menos uno de los dos debe haber corrido, según project_type.
    devs = {
        "out_backend":  state.get("out_backend", "") or "",
        "out_frontend": state.get("out_frontend", "") or "",
    }
    con_contenido = {k: v for k, v in devs.items() if v.strip()}

    if not con_contenido:
        return OutputValidationResult(
            False,
            "Ningún developer produjo salida: no hay cambios para el PR",
            "OUT-03",
            "out_backend/out_frontend",
        )

    for campo, contenido in con_contenido.items():
        if not PATRON_ARCHIVO.search(contenido):
            return OutputValidationResult(
                False,
                f"'{campo}' no contiene bloques '### ruta/archivo.ext'. "
                "Sin ese formato no se puede extraer ningún archivo y el PR "
                "quedaría vacío.",
                "OUT-03",
                campo,
            )

    # ── OUT-04: sin secretos ─────────────────────────────────────
    todo = " ".join(
        (state.get(k) or "")
        for k in ("out_analysis", "out_plan", "out_data", "out_backend",
                  "out_frontend", "out_testing", "out_security", "out_chapter")
    ).lower()
    for termino in TERMINOS_BLOQUEADOS:
        if termino.lower() in todo:
            return OutputValidationResult(
                False,
                f"Dato sensible detectado en el output: '{termino}'",
                "OUT-04",
                "all",
            )

    rondas = state.get("review_round", 0)
    return OutputValidationResult(
        True,
        f"OK · {len(con_contenido)} área(s) con cambios, {rondas} ronda(s) de review",
        "",
        "",
    )


def validate_chapter_verdict(chapter_output: str) -> OutputValidationResult:
    """
    Valida sólo el veredicto del chapter.

    Conservador, igual que `graph.nodes.parse_verdict()`: "aprobado con
    observaciones" NO cuenta como aprobación — cierra con otra ronda.
    """
    t = (chapter_output or "").lower()

    if "rechazado" in t or "rejected" in t:
        return OutputValidationResult(
            False, "Veredicto RECHAZADO por el chapter", "OUT-05", "out_chapter")

    if "aprobado con observaciones" in t or "approved with comments" in t:
        return OutputValidationResult(
            False, "APROBADO CON OBSERVACIONES: el ciclo debe continuar",
            "OUT-05", "out_chapter")

    if "aprobado" in t or "approved" in t:
        return OutputValidationResult(True, "Veredicto APROBADO confirmado", "", "")

    return OutputValidationResult(
        False,
        "El chapter no emitió un veredicto claro "
        "(falta APROBADO / APROBADO CON OBSERVACIONES / RECHAZADO)",
        "OUT-06",
        "out_chapter",
    )


# Nota: S4 NO valida arquitectura hexagonal. El repositorio que refactoriza
# es código ajeno y no tiene por qué seguir ese patrón; exigirlo daría falsos
# negativos en cada corrida. La validación hexagonal vive en S2, que genera
# el código desde cero y por tanto sí puede imponer la estructura.
