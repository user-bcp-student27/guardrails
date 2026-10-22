"""
Guardrail de entrada — S4.

Filosofía: **fallar antes de gastar tokens**. Si lo que va a entrar al graph
no sirve, cortamos aquí y no pagamos una llamada al LLM para descubrirlo.

Dos puertas distintas:

  · `validate_snapshot()` — la que usa el pipeline real. Valida el código
    leído del repositorio clonado antes del primer nodo: que se haya podido
    leer algo, que no sea desmesurado, y que no lleve secretos al prompt.

  · `validate_input()` — valida un texto libre. Queda para quien quiera
    pasar un brief escrito a mano; el pipeline no la usa, porque su entrada
    es el snapshot del repo, no un brief.
"""
import re
from typing import NamedTuple


class ValidationResult(NamedTuple):
    valid:  bool
    reason: str


# ── Reglas sobre el snapshot del repositorio ──────────────────────
MIN_SNAPSHOT_CHARS = 200        # menos que esto: no se leyó código de verdad
MAX_SNAPSHOT_CHARS = 200_000    # más que esto: no entra en ningún contexto

# Secretos que no deben viajar al prompt. Si el repo los trae, el problema
# es del repo — pero mandarlos al LLM lo empeora.
SECRET_PATTERNS: list[tuple[str, str]] = [
    (r"sk-[A-Za-z0-9]{20,}",                  "API key de OpenAI"),
    (r"ghp_[A-Za-z0-9]{20,}",                 "token de GitHub"),
    (r"AKIA[0-9A-Z]{16}",                     "access key de AWS"),
    (r"-----BEGIN (?:RSA |EC )?PRIVATE KEY",  "clave privada"),
]

# PII que no debe salir del repositorio hacia el modelo.
PII_PATTERNS: list[tuple[str, str]] = [
    (r"\b4[0-9]{12}(?:[0-9]{3})?\b", "número de tarjeta Visa"),
    (r"\b5[1-5][0-9]{14}\b",         "número de tarjeta Mastercard"),
]


def validate_snapshot(snapshot: str) -> ValidationResult:
    """
    Valida el snapshot del repo antes de arrancar el graph.

    Args:
        snapshot: código leído del repositorio clonado.

    Returns:
        ValidationResult(valid=True, reason="OK · N chars") si pasa todo.
        ValidationResult(valid=False, reason="<motivo>") en el primer fallo.

    Ejemplos:
        >>> validate_snapshot("").valid
        False
        >>> validate_snapshot("### app.py\\n" + "x = 1\\n" * 100).valid
        True
    """
    if not snapshot or not snapshot.strip():
        return ValidationResult(False, "El snapshot está vacío: no se pudo leer el repo")

    n = len(snapshot)

    if n < MIN_SNAPSHOT_CHARS:
        return ValidationResult(
            False,
            f"Snapshot demasiado chico ({n} chars, mínimo {MIN_SNAPSHOT_CHARS}). "
            "¿El repo está vacío o el filtro de extensiones dejó todo afuera?"
        )

    if n > MAX_SNAPSHOT_CHARS:
        return ValidationResult(
            False,
            f"Snapshot demasiado grande ({n} chars, máximo {MAX_SNAPSHOT_CHARS}). "
            "Acota el repositorio o sube el límite de _snapshot()."
        )

    for patron, descripcion in SECRET_PATTERNS:
        if re.search(patron, snapshot):
            return ValidationResult(
                False,
                f"El snapshot contiene un secreto ({descripcion}). "
                "No se envía al modelo: sácalo del repositorio primero."
            )

    for patron, descripcion in PII_PATTERNS:
        if re.search(patron, snapshot):
            return ValidationResult(
                False,
                f"El snapshot contiene datos sensibles ({descripcion})."
            )

    return ValidationResult(True, f"OK · {n} chars listos para el pipeline")


# ── Validación de texto libre (no la usa el pipeline) ─────────────
MIN_LENGTH = 10
MAX_LENGTH = 4000

BLOCKED_KEYWORDS: list[str] = [
    "DROP TABLE", "DELETE FROM", "TRUNCATE",   # SQL injection
    "rm -rf", "sudo", "chmod",                  # Shell injection
    "SELECT * FROM", "INSERT INTO",             # SQL directo
    "exec(", "eval(", "__import__",             # Python injection
]


def validate_input(text: str) -> ValidationResult:
    """
    Valida un texto libre antes de mandarlo a un modelo.

    El pipeline de S4 no la invoca —su entrada es el snapshot del repo—,
    pero queda disponible para extensiones que reciban un brief escrito.
    """
    if not text or not text.strip():
        return ValidationResult(False, "El input no puede estar vacío")

    stripped = text.strip()

    if len(stripped) < MIN_LENGTH:
        return ValidationResult(
            False,
            f"Input demasiado corto (mínimo {MIN_LENGTH} caracteres, "
            f"recibido {len(stripped)})"
        )

    if len(stripped) > MAX_LENGTH:
        return ValidationResult(
            False,
            f"Input demasiado largo (máximo {MAX_LENGTH} caracteres, "
            f"recibido {len(stripped)})"
        )

    bajo = stripped.lower()
    for keyword in BLOCKED_KEYWORDS:
        if keyword.lower() in bajo:
            return ValidationResult(False, f"Keyword bloqueada detectada: '{keyword}'")

    for patron, descripcion in SECRET_PATTERNS + PII_PATTERNS:
        if re.search(patron, stripped):
            return ValidationResult(False, f"Datos sensibles en el input: {descripcion}")

    return ValidationResult(True, "OK")
