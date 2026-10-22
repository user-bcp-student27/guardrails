"""
Evals de contrato HTTP — Caso ReqFlow (AC-01..AC-06)

Ejecuta tests HTTP reales contra los servicios del portal levantados localmente
y verifica que los seis contratos funcionales del MVP se cumplan.

Precondición: los tres servicios deben estar corriendo:
    - BACKEND_BASE_URL=http://localhost:8080   (workshop-auth-backend Quarkus)
    - API_BASE_URL=http://localhost:8000       (workshop-requirements-api FastAPI)

Variables de entorno:
    BACKEND_BASE_URL   URL del backend Quarkus
    API_BASE_URL       URL de la API FastAPI
    TEST_USER          usuario de prueba (default: student, el del Día 1)
    TEST_USER_PASSWORD password del usuario de prueba
    LOGIN_USER_FIELD   nombre del campo de usuario en el request (default: username)

La forma esperada de la respuesta de login es la de docs/ENDPOINTS.md del
participante (Día 1). Se aceptan las dos variantes vistas en el programa:
{token, expiresIn, user} y {token, expires_in, role}.

Uso:
    from evals.contract_evals import run_contract_evals
    results = run_contract_evals()
"""
import os
import time
from dataclasses import dataclass, field
from typing import Optional

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

BACKEND_BASE = os.getenv("BACKEND_BASE_URL", "http://localhost:8080")
API_BASE     = os.getenv("API_BASE_URL",     "http://localhost:8000")
TEST_USER    = os.getenv("TEST_USER",          "student")
TEST_PASS    = os.getenv("TEST_USER_PASSWORD", "Workshop123!")
USER_FIELD   = os.getenv("LOGIN_USER_FIELD",   "username")
TIMEOUT      = int(os.getenv("HTTP_TIMEOUT", "10"))


@dataclass
class ContractResult:
    ac_id:       str
    description: str
    passed:      bool
    status_code: Optional[int] = None
    error:       Optional[str] = None
    response_ms: Optional[int] = None
    details:     dict = field(default_factory=dict)


def _get(url: str, token: str = "") -> tuple:
    """GET → (status_code, json_body, elapsed_ms, error)"""
    if not REQUESTS_AVAILABLE:
        return None, {}, 0, "requests no instalado"
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        t0 = time.monotonic()
        r = requests.get(url, headers=headers, timeout=TIMEOUT)
        ms = int((time.monotonic() - t0) * 1000)
        try:
            body = r.json()
        except Exception:
            body = {"raw": r.text[:200]}
        return r.status_code, body, ms, None
    except Exception as e:
        return None, {}, 0, str(e)


def _post(url: str, payload: dict, token: str = "") -> tuple:
    """POST → (status_code, json_body, elapsed_ms, error)"""
    if not REQUESTS_AVAILABLE:
        return None, {}, 0, "requests no instalado"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        t0 = time.monotonic()
        r = requests.post(url, json=payload, headers=headers, timeout=TIMEOUT)
        ms = int((time.monotonic() - t0) * 1000)
        try:
            body = r.json()
        except Exception:
            body = {"raw": r.text[:200]}
        return r.status_code, body, ms, None
    except Exception as e:
        return None, {}, 0, str(e)


def _has_expiry(body: dict) -> bool:
    return "expiresIn" in body or "expires_in" in body


def _has_identity(body: dict) -> bool:
    return "user" in body or "role" in body


# ── AC-01: POST /auth/login ───────────────────────────────────────
def eval_ac01() -> ContractResult:
    """AC-01: POST /auth/login → 200 + {token, expiresIn|expires_in, user|role}"""
    url = f"{BACKEND_BASE}/auth/login"
    status, body, ms, err = _post(url, {USER_FIELD: TEST_USER, "password": TEST_PASS})

    if err:
        return ContractResult("AC-01", "POST /auth/login", False, error=err)

    passed = (
        status == 200
        and "token" in body
        and _has_expiry(body)
        and _has_identity(body)
    )
    return ContractResult(
        ac_id="AC-01",
        description="POST /auth/login → 200 + {token, expiresIn, user}",
        passed=passed,
        status_code=status,
        response_ms=ms,
        details={
            "has_token":      "token" in body,
            "has_expiry":     _has_expiry(body),
            "has_identity":   _has_identity(body),
        },
    )


# ── AC-02: GET /api/tasks (con token) ────────────────────────────
def eval_ac02(token: str) -> ContractResult:
    """AC-02: GET /api/tasks → 200 + lista de tareas"""
    url = f"{API_BASE}/api/tasks"
    status, body, ms, err = _get(url, token)

    if err:
        return ContractResult("AC-02", "GET /api/tasks", False, error=err)

    passed = status == 200 and isinstance(body, list)
    return ContractResult(
        ac_id="AC-02",
        description="GET /api/tasks → 200 + lista de tareas",
        passed=passed,
        status_code=status,
        response_ms=ms,
        details={"is_list": isinstance(body), "items": len(body) if isinstance(body, list) else 0},
    )


# ── AC-03: POST /api/tasks (con token) ───────────────────────────
def eval_ac03(token: str) -> ContractResult:
    """AC-03: POST /api/tasks → 201 + tarea creada"""
    url = f"{API_BASE}/api/tasks"
    payload = {
        "title":       "Tarea de prueba ReqFlow",
        "description": "Creada por eval AC-03",
        "priority":    "HIGH",
    }
    status, body, ms, err = _post(url, payload, token)

    if err:
        return ContractResult("AC-03", "POST /api/tasks", False, error=err)

    passed = status == 201 and "id" in body
    return ContractResult(
        ac_id="AC-03",
        description="POST /api/tasks → 201 + tarea creada",
        passed=passed,
        status_code=status,
        response_ms=ms,
        details={"has_id": "id" in body, "task_id": body.get("id")},
    )


# ── AC-04: GET /api/summary (con token) ──────────────────────────
def eval_ac04(token: str) -> ContractResult:
    """AC-04: GET /api/summary → 200 + objeto resumen"""
    url = f"{API_BASE}/api/summary"
    status, body, ms, err = _get(url, token)

    if err:
        return ContractResult("AC-04", "GET /api/summary", False, error=err)

    passed = status == 200 and isinstance(body, dict)
    return ContractResult(
        ac_id="AC-04",
        description="GET /api/summary → 200 + objeto resumen",
        passed=passed,
        status_code=status,
        response_ms=ms,
        details={"is_dict": isinstance(body, dict), "keys": list(body.keys())[:5]},
    )


# ── AC-05: POST /api/requirements (con token) ────────────────────
def eval_ac05(token: str) -> ContractResult:
    """AC-05: POST /api/requirements → 201 + requerimiento"""
    url = f"{API_BASE}/api/requirements"
    payload = {
        "title":       "Requerimiento de prueba ReqFlow",
        "description": "Creado por eval AC-05",
        "priority":    "MEDIUM",
        "status":      "PENDING",
    }
    status, body, ms, err = _post(url, payload, token)

    if err:
        return ContractResult("AC-05", "POST /api/requirements", False, error=err)

    passed = status == 201 and "id" in body
    return ContractResult(
        ac_id="AC-05",
        description="POST /api/requirements → 201 + requerimiento",
        passed=passed,
        status_code=status,
        response_ms=ms,
        details={"has_id": "id" in body, "req_id": body.get("id")},
    )


# ── AC-06: Sin token → 401 en rutas protegidas ───────────────────
def eval_ac06() -> ContractResult:
    """AC-06: Rutas protegidas devuelven 401 sin token (integridad del flujo)"""
    errors = []
    for url in [f"{API_BASE}/api/tasks",
                f"{API_BASE}/api/summary",
                f"{API_BASE}/api/requirements"]:
        status, _, _, err = _get(url, "")   # sin token
        if err:
            errors.append(f"{url}: {err}")
        elif status not in (401, 403):
            errors.append(f"{url}: esperaba 401/403, got {status}")

    passed = len(errors) == 0
    return ContractResult(
        ac_id="AC-06",
        description="Rutas protegidas devuelven 401/403 sin token",
        passed=passed,
        status_code=401 if passed else None,
        error="; ".join(errors) if errors else None,
        details={"unauthorized_endpoints_checked": 3},
    )


# ── Runner principal ──────────────────────────────────────────────
def run_contract_evals() -> list:
    """
    Ejecuta los 6 evals de contrato en orden.
    Retorna lista de ContractResult.
    """
    if not REQUESTS_AVAILABLE:
        print("  ⚠️  'requests' no instalado — instala con: pip install requests")
        print("  Los evals de contrato se omiten.")
        return [
            ContractResult(f"AC-0{i}", "Skipped — requests no disponible", False,
                           error="requests not installed")
            for i in range(1, 7)
        ]

    results = []

    # AC-01: login → obtener token
    print("  [AC-01] POST /auth/login ...")
    r01 = eval_ac01()
    results.append(r01)
    token = ""
    if r01.passed:
        # Re-hacer login para obtener el token real
        _, body, _, _ = _post(f"{BACKEND_BASE}/auth/login",
                               {USER_FIELD: TEST_USER, "password": TEST_PASS})
        token = body.get("token", "")
        print(f"    ✅ PASSED ({r01.response_ms}ms)")
    else:
        print(f"    ❌ FAILED — {r01.error or f'HTTP {r01.status_code}'}")
        print("    ⚠️  Sin token, los siguientes evals usarán string vacío")

    # AC-02..AC-05 requieren token
    for eval_fn, label in [
        (lambda: eval_ac02(token), "AC-02: GET /api/tasks"),
        (lambda: eval_ac03(token), "AC-03: POST /api/tasks"),
        (lambda: eval_ac04(token), "AC-04: GET /api/summary"),
        (lambda: eval_ac05(token), "AC-05: POST /api/requirements"),
    ]:
        print(f"  [{label.split(':')[0]}] {label.split(':', 1)[1].strip()} ...")
        r = eval_fn()
        results.append(r)
        if r.passed:
            print(f"    ✅ PASSED ({r.response_ms}ms)")
        else:
            print(f"    ❌ FAILED — {r.error or f'HTTP {r.status_code}'}")

    # AC-06: seguridad sin token
    print("  [AC-06] Rutas protegidas sin token ...")
    r06 = eval_ac06()
    results.append(r06)
    if r06.passed:
        print(f"    ✅ PASSED")
    else:
        print(f"    ❌ FAILED — {r06.error}")

    return results


def results_to_markdown(results: list) -> str:
    """Convierte lista de ContractResult en tabla Markdown."""
    lines = [
        "# Reporte de Contratos HTTP — ReqFlow",
        "",
        "| AC | Descripción | Estado | HTTP | ms | Detalles |",
        "|----|-------------|--------|------|----|----------|",
    ]
    for r in results:
        icon    = "✅" if r.passed else "❌"
        status  = str(r.status_code) if r.status_code else "—"
        ms      = str(r.response_ms) if r.response_ms else "—"
        details = str(r.details)[:80] if r.details else (r.error or "")
        lines.append(f"| {r.ac_id} | {r.description} | {icon} | {status} | {ms} | {details} |")

    passed = sum(1 for r in results if r.passed)
    lines += [
        "",
        f"**Total: {passed}/{len(results)} contratos preservados**",
    ]
    return "\n".join(lines)
