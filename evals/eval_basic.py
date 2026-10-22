"""
Suite de evals deterministas — S4.

Corre el **pipeline real** sobre el repositorio configurado en `.env` y evalúa
el resultado. No hay brief fijo: los hallazgos salen del código clonado, así
que cambiar `GITHUB_REPO` cambia lo que se evalúa.

Los evals son deterministas: no juzgan la semántica del output, sino la
presencia de estructura y de referencias verificables contra el repo real.

Ejecutar:
    python evals/eval_basic.py

Genera `evidence/eval_results.json` con el resultado de cada test.
"""
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv(override=True)

from harness.harness import run_with_harness, run_to_end, check_security_compliance  # noqa: E402
from evals.gate_g2 import gate_g2, THRESHOLD                     # noqa: E402
from guardrails.input_check import validate_snapshot              # noqa: E402
from guardrails.output_check import validate_output               # noqa: E402
from main import _clone_or_pull, _snapshot, _detect_type          # noqa: E402

GITHUB_OWNER = os.getenv("GITHUB_OWNER", "")
GITHUB_REPO  = os.getenv("GITHUB_REPO",  "")

EVIDENCE_DIR = Path(__file__).parent.parent / "evidence"
EVIDENCE_DIR.mkdir(exist_ok=True)


def _rutas_del_repo(snapshot: str) -> set[str]:
    """Nombres de archivo que aparecen en el snapshot, para cruzar con el plan."""
    return {Path(m).name for m in re.findall(r"###\s*(\S+\.\w+)", snapshot)}


def run_all_evals(result: dict, snapshot: str) -> list[dict]:
    """
    Evalúa el estado final del pipeline contra el repositorio real.

    Args:
        result:   WorkflowState final devuelto por el harness.
        snapshot: código del repo, para cruzar referencias.

    Returns:
        Lista de dicts con keys: test, passed, detail
    """
    analysis  = result.get("out_analysis", "") or ""
    plan      = result.get("out_plan", "") or ""
    backend   = result.get("out_backend", "") or ""
    frontend  = result.get("out_frontend", "") or ""
    chapter  = result.get("out_chapter", "") or ""
    dev_total = backend + "\n" + frontend

    archivos_repo     = _rutas_del_repo(snapshot)
    archivos_plan     = {Path(m).name for m in re.findall(r"[\w/\\.-]+\.\w+", plan)}
    archivos_analisis = {Path(m).name for m in re.findall(r"[\w/\\.-]+\.\w+", analysis)}
    archivos_dev      = {Path(m).name for m in re.findall(r"###\s*(\S+\.\w+)", dev_total)}

    tests: list[tuple[str, bool, str]] = [

        # ── Análisis: sale del repo, no de un brief ───────────────
        (
            "architect_produce_hallazgos",
            len(analysis) > 200,
            "El análisis debe superar 200 caracteres",
        ),
        (
            "architect_clasifica_severidad",
            any(s in analysis.lower() for s in ("alta", "media", "baja")),
            "Los hallazgos deben venir con severidad (Alta/Media/Baja)",
        ),
        (
            "architect_referencia_el_repo_real",
            bool(archivos_repo & archivos_analisis),
            "El análisis debe nombrar al menos un archivo que exista en el repo "
            "— si no, no está mirando el código real",
        ),

        # ── Plan ──────────────────────────────────────────────────
        (
            "architect_produce_plan",
            len(plan) > 200,
            "El plan debe superar 200 caracteres",
        ),
        (
            "architect_nombra_archivos_del_repo",
            bool(archivos_repo & archivos_plan),
            "El plan debe nombrar archivos que existan en el repositorio",
        ),

        # ── Developers ────────────────────────────────────────────
        (
            "dev_emite_bloques_extraibles",
            bool(archivos_dev),
            "Los devs deben emitir bloques '### ruta/archivo.ext'; sin eso no "
            "se extrae nada y el PR queda vacío",
        ),
        (
            "dev_toca_archivos_existentes",
            bool(archivos_repo & archivos_dev),
            "Al menos un archivo modificado debe existir en el repo original",
        ),

        # ── Chapter y review loop ────────────────────────────────
        (
            "chapter_emite_veredicto",
            any(v in chapter.lower() for v in ("aprobado", "rechazado")),
            "El chapter debe emitir un veredicto legible",
        ),
        (
            "review_loop_registra_rondas",
            result.get("review_round", 0) >= 1,
            "El review loop debe registrar al menos una ronda",
        ),

        # ── Guardrail de salida ───────────────────────────────────
        (
            "guardrail_salida_aprueba",
            validate_output(result).valid,
            "El estado final debe pasar el guardrail de salida",
        ),
    ]

    return [
        {"test": nombre, "passed": bool(ok), "detail": detalle}
        for nombre, ok, detalle in tests
    ]


def main() -> int:
    if not GITHUB_OWNER or not GITHUB_REPO:
        print("✗ Faltan GITHUB_OWNER / GITHUB_REPO en el .env")
        return 1

    print("=" * 60)
    print("S4 — Suite de evals deterministas")
    print(f"Repo: {GITHUB_OWNER}/{GITHUB_REPO}")
    print("=" * 60)

    # ── Clonar y leer el repo real ────────────────────────────────
    repo_dir = _clone_or_pull(GITHUB_REPO)
    snapshot, n_archivos = _snapshot(repo_dir)
    print(f"  Snapshot: {n_archivos} archivos leídos.")

    gate = validate_snapshot(snapshot)
    if not gate.valid:
        print(f"✗ Guardrail de entrada: {gate.reason}")
        return 1
    print(f"  ✅ Guardrail de entrada: {gate.reason}")

    project_type = _detect_type(repo_dir)
    print(f"  Tipo detectado: {project_type.upper()}\n")

    # ── Correr el pipeline completo, sin HITL ─────────────────────
    inicio = time.time()
    run_with_harness(f"{GITHUB_OWNER}/{GITHUB_REPO}", snapshot,
                     project_type, "eval-suite-01", repo_dir=repo_dir)
    resultado = run_to_end("eval-suite-01")
    elapsed = round(time.time() - inicio, 2)

    # ── Evaluar ───────────────────────────────────────────────────
    tests = run_all_evals(resultado, snapshot)
    passed = sum(1 for t in tests if t["passed"])

    print(f"\n{'─' * 60}")
    for t in tests:
        print(f"  {'✅' if t['passed'] else '❌'} {t['test']}")
        if not t["passed"]:
            print(f"       {t['detail']}")
    print(f"{'─' * 60}")
    print(f"Resultado: {passed}/{len(tests)} tests OK ({elapsed}s)")

    # ── Gate G2: rúbrica ponderada ────────────────────────────────
    compliance = check_security_compliance(" ".join(
        resultado.get(k, "") or "" for k in ("out_backend", "out_frontend", "out_chapter")))
    pr_opened = (EVIDENCE_DIR / "pr_url.txt").is_file()  # lo escribe main.py al abrir el PR
    g2 = gate_g2(tests, compliance, pr_opened)
    print(f"\nGate G2 (umbral {THRESHOLD:.0f}%):")
    for nombre, c in g2["criteria"].items():
        print(f"  {c['points']:>5.1f} / {c['weight']:<3} {nombre}")
    print(f"  {'✅' if g2['passed'] else '❌'} Score ponderado: {g2['score']}%")
    if not pr_opened:
        print("  (sin evidence/pr_url.txt: corre main.py hasta abrir el PR para sumar el 10% de Git y PR)")

    reporte = {
        "timestamp":     datetime.now().strftime("%Y%m%d_%H%M%S"),
        "repo":          f"{GITHUB_OWNER}/{GITHUB_REPO}",
        "project_type":  project_type,
        "elapsed_s":     elapsed,
        "review_rounds": resultado.get("review_round", 0),
        "verdict":       resultado.get("last_verdict", ""),
        "passed":        passed,
        "total":         len(tests),
        "tests":         tests,
        "compliance":    compliance,
        "gate_g2":       g2,
    }
    destino = EVIDENCE_DIR / "eval_results.json"
    destino.write_text(json.dumps(reporte, indent=2, ensure_ascii=False),
                       encoding="utf-8")
    print("  ✅ evidence/eval_results.json")

    return 0 if g2["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
