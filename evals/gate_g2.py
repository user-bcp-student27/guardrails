"""
Gate G2 — rúbrica ponderada de cierre del Día 4 (umbral 80 %).

Cada criterio vale la fracción de sus checks que pasaron, multiplicada por su
peso. Los checks salen de `eval_basic.run_all_evals()`, del compliance de la
constitución y de la evidencia del PR que deja `main.py`.
"""

THRESHOLD = 80.0

# criterio → (peso %, nombres de checks de eval_basic que lo componen)
CRITERIA = {
    "Constitución ReqFlow (R1-R6)": (30, None),  # usa el compliance, no evals
    "Contrato y trazabilidad":      (25, ("architect_referencia_el_repo_real",
                                          "architect_nombra_archivos_del_repo",
                                          "dev_toca_archivos_existentes")),
    "Calidad del pipeline":         (20, ("architect_produce_hallazgos",
                                          "architect_clasifica_severidad",
                                          "architect_produce_plan",
                                          "dev_emite_bloques_extraibles",
                                          "chapter_emite_veredicto",
                                          "review_loop_registra_rondas")),
    "Guardrails de entrada/salida": (15, ("guardrail_salida_aprueba",)),
    "Integración Git y PR":         (10, None),  # usa pr_opened
}


def gate_g2(tests: list[dict], compliance: dict[str, bool], pr_opened: bool) -> dict:
    """Devuelve {"score", "passed", "criteria": {nombre: {"weight", "ratio", "points"}}}."""
    by_name = {t["test"]: t["passed"] for t in tests}
    criteria = {}
    for name, (weight, checks) in CRITERIA.items():
        if name.startswith("Constitución"):
            ratio = sum(compliance.values()) / len(compliance) if compliance else 0.0
        elif name.startswith("Integración"):
            ratio = 1.0 if pr_opened else 0.0
        else:
            ratio = sum(by_name.get(c, False) for c in checks) / len(checks)
        criteria[name] = {"weight": weight, "ratio": round(ratio, 2),
                          "points": round(weight * ratio, 1)}
    score = round(sum(c["points"] for c in criteria.values()), 1)
    return {"score": score, "passed": score >= THRESHOLD, "criteria": criteria}


if __name__ == "__main__":
    all_ok = [{"test": c, "passed": True}
              for _, checks in CRITERIA.values() if checks for c in checks]
    full = gate_g2(all_ok, {"R1": True, "R2": True}, pr_opened=True)
    assert full["score"] == 100.0 and full["passed"], full

    no_pr_half_rules = gate_g2(all_ok, {"R1": True, "R2": False}, pr_opened=False)
    assert no_pr_half_rules["score"] == 75.0 and not no_pr_half_rules["passed"], no_pr_half_rules
    print("gate_g2 OK")
