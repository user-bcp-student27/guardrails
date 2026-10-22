"""Guardrails: seis nodos editables, harness y aprobación humana.

architect → backend → frontend → testing → security → chapter
El Chapter devuelve observaciones al backend hasta MAX_REVIEW_ROUNDS.
Configura .env y ejecuta python main.py. El flujo clona el repo de producto,
escribe los cambios aprobados y abre un PR mediante GitHub CLI.
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# Cargar .env ANTES de importar MCPs y LLM
load_dotenv(override=True)

import mcp_git

# ── Configuración ────────────────────────────────────────────────────
_MCP_VERBOSE = os.getenv("MCP_VERBOSE", "false").lower() == "true"
mcp_git.set_verbose(_MCP_VERBOSE)

GITHUB_OWNER  = os.getenv("GITHUB_OWNER", "")
GITHUB_REPO   = os.getenv("GITHUB_REPO",  "")
GITHUB_TOKEN  = os.getenv("GITHUB_TOKEN", "")
HEAD_BRANCH   = os.getenv("GIT_HEAD_BRANCH", "")
BASE_BRANCH   = os.getenv("GIT_BASE_BRANCH", "main")
_ISSUE        = os.getenv("GITHUB_ISSUE", "").strip()  # issue del Project ReqFlow en alcance

TMP_DIR    = Path(__file__).parent / ".tmp"
OUTPUT_DIR = Path(__file__).parent / "output"
TMP_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
CHECKPOINT_FILE = OUTPUT_DIR / ".checkpoint.json"

# El pipeline de agentes vive en el graph; el harness es su única puerta de
# entrada. main.py no instancia modelos ni arma prompts.
from harness.harness import (
    run_with_harness,
    resume_with_harness,
    next_node,
    step,
    retry_node,
    check_security_compliance,
    state as harness_state,
)
from graph.graph import MAX_REVIEW_ROUNDS as _MAX_REVIEW_ROUNDS, include_data

# Guardrails: uno antes de gastar tokens, otro antes de abrir el PR.
from guardrails.input_check import validate_snapshot
from guardrails.output_check import validate_output

# Identificador de la sesión del graph. El estado vive en el MemorySaver
# del harness indexado por este thread_id.
THREAD_ID = "s4-refactor-01"

# Etiquetas y destinos de cada nodo, para el HITL y la persistencia.
NODE_LABELS = {
    "architect": "Arquitecto: análisis y plan",
    "data": "Datos y persistencia",
    "backend": "Desarrollador backend",
    "frontend": "Desarrollador frontend",
    "testing": "Ingeniero de pruebas",
    "security": "Revisor de seguridad",
    "chapter": "Chapter: revisión final",
}
OUTPUT_FILES = {
    "architect": ("architecture_report.md", "Análisis y plan"),
    "backend": ("backend_refactor.md", "Implementación backend"),
    "frontend": ("frontend_refactor.md", "Implementación frontend"),
    "testing": ("testing_report.md", "Pruebas"),
    "security": ("security_report.md", "Seguridad"),
    "chapter": ("chapter_report.md", "Revisión final"),
}
DATA_OUTPUT_FILES = {**OUTPUT_FILES, "data": ("data_report.md", "Datos y persistencia")}
CKPT_FIELD = {
    "architect": "architect_out",
    "data": "data_out",
    "backend": "backend_out",
    "frontend": "frontend_out",
    "testing": "testing_out",
    "security": "security_out",
    "chapter": "chapter_out",
}
from harness.harness import STATE_FIELD


# ── Utilidades generales ──────────────────────────────────────────────

def _ctx(text: str, max_chars: int = 4000) -> str:
    """Trunca conservando inicio y fin. Lo usa el cuerpo del PR."""
    if not text or len(text) <= max_chars:
        return text
    half = max_chars // 2
    return text[:half] + f"\n\n[... +{len(text) - max_chars} chars omitidos ...]\n\n" + text[-half:]


# ── Checkpoint ────────────────────────────────────────────────────────

def save_checkpoint(state: dict, next_step: str) -> None:
    data = {
        "include_data": state.get("include_data", include_data()),
        "data_out": state.get("data_out", ""),
        "project_type":   state.get("project_type", ""),
        "type_confirmed": state.get("type_confirmed", False),
        "architect_out": state.get("architect_out", ""),
        "analysis":       state.get("analysis", ""),
        "plan":           state.get("plan", ""),
        "backend_out":    state.get("backend_out", ""),
        "frontend_out":   state.get("frontend_out", ""),
        "testing_out": state.get("testing_out", ""),
        "security_out": state.get("security_out", ""),
        "last_verdict": state.get("last_verdict", ""),
        "chapter_out":   state.get("chapter_out", ""),
        "review_round":   state.get("review_round", 0),
        "review_feedback":state.get("review_feedback", ""),
        "head_branch":    state.get("head_branch", ""),
        "base_branch":    state.get("base_branch", ""),
        "commit_type":    state.get("commit_type", ""),
        "commit_msg":     state.get("commit_msg", ""),
        "next_step":      next_step,
    }
    CHECKPOINT_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  💾 Checkpoint guardado (next_step={next_step})")


def load_checkpoint() -> dict | None:
    if CHECKPOINT_FILE.exists():
        try:
            return json.loads(CHECKPOINT_FILE.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def clear_checkpoint() -> None:
    if CHECKPOINT_FILE.exists():
        CHECKPOINT_FILE.unlink()
        print("  🗑️  Checkpoint eliminado.")


# ── Clone / snapshot del repo ─────────────────────────────────────────

def _clone_or_pull(repo_name: str) -> Path:
    """Clona o actualiza el repo en .tmp/<repo_name>."""
    token = GITHUB_TOKEN
    url   = (f"https://{token}@github.com/{GITHUB_OWNER}/{repo_name}.git"
             if token else f"https://github.com/{GITHUB_OWNER}/{repo_name}.git")
    dest  = TMP_DIR / repo_name
    if dest.exists():
        print(f"  [GIT] Actualizando {repo_name}...")
        r = subprocess.run(["git", "-C", str(dest), "pull", "--ff-only"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(f"  ⚠️  Pull error: {r.stderr[:200]} — continuando con la versión local")
    else:
        print(f"  [GIT] Clonando {repo_name}...")
        r = subprocess.run(["git", "clone", url, str(dest)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"Clone error: {r.stderr[:300]}")
    return dest


_SKIP_DIRS  = {".git", "node_modules", "__pycache__", ".venv", "venv",
               "dist", "build", ".next", "target", ".gradle"}
_CODE_EXTS  = {".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go",
               ".cs", ".cpp", ".c", ".h", ".rb", ".php", ".swift",
               ".kt", ".rs", ".vue", ".html", ".css", ".scss",
               ".yaml", ".yml", ".json", ".toml", ".xml", ".md",
               ".sh", ".sql", ".env.example", ".properties"}

def _snapshot(repo_dir: Path, max_chars: int = 80_000) -> tuple[str, int]:
    """Lee todos los archivos de código del repo. Devuelve (texto, n_archivos)."""
    parts = []
    count = 0
    total = 0
    for f in sorted(repo_dir.rglob("*")):
        if any(p in f.parts for p in _SKIP_DIRS):
            continue
        if not f.is_file():
            continue
        if f.suffix.lower() not in _CODE_EXTS and f.name not in {
            "Dockerfile", "Makefile", "requirements.txt", "package.json",
            "pom.xml", "build.gradle", "go.mod", "Cargo.toml",
        }:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        rel  = f.relative_to(repo_dir).as_posix()
        chunk = f"### {rel}\n```\n{text[:3000]}\n```\n"
        if total + len(chunk) > max_chars:
            parts.append(f"### [truncado — {rel} y más archivos omitidos]\n")
            break
        parts.append(chunk)
        total += len(chunk)
        count += 1
    return "\n".join(parts), count


# ── Detección de tipo de proyecto ─────────────────────────────────────

_BACKEND_EXTS    = {".py", ".java", ".go", ".cs", ".cpp", ".c", ".rb", ".php",
                    ".swift", ".kt", ".rs"}
_BACKEND_FILES   = {"requirements.txt", "pom.xml", "build.gradle", "go.mod",
                    "Cargo.toml", "Gemfile", "composer.json"}
_FRONTEND_EXTS   = {".jsx", ".tsx", ".vue"}
_FRONTEND_FILES  = {"package.json", "angular.json", "vite.config.js",
                    "vite.config.ts", "next.config.js", "nuxt.config.js"}

def _detect_type(repo_dir: Path) -> str:
    """Retorna 'backend', 'frontend' o 'ambos'."""
    has_backend  = False
    has_frontend = False
    for f in repo_dir.rglob("*"):
        if any(p in f.parts for p in _SKIP_DIRS):
            continue
        if not f.is_file():
            continue
        if f.suffix.lower() in _BACKEND_EXTS or f.name in _BACKEND_FILES:
            has_backend = True
        if f.suffix.lower() in _FRONTEND_EXTS or f.name in _FRONTEND_FILES:
            has_frontend = True
        if has_backend and has_frontend:
            break
    if has_backend and has_frontend:
        return "ambos"
    if has_frontend:
        return "frontend"
    return "backend"


def _confirm_type(detected: str) -> str:
    """Pregunta al usuario si el tipo detectado es correcto."""
    opts = ["backend", "frontend", "ambos"]
    while True:
        raw = input(
            f"\n  Tipo de proyecto detectado: {detected.upper()}\n"
            f"  ¿Confirmar? [{'/'.join(opts)}] (Enter = {detected}): "
        ).strip().lower()
        if not raw:
            return detected
        if raw in opts:
            return raw
        print(f"  ⚠️  Opción inválida. Escribe: {', '.join(opts)}")


# El veredicto lo interpreta graph/nodes.parse_verdict() y queda en
# state['last_verdict']. main.py solo lo lee.

# ── Extracción de archivos del output del agente ───────────────────────

def _extract_files(agent_output: str, repo_dir: Path) -> int:
    """Extrae bloques ```path\ncontenido``` y los escribe en repo_dir y output/."""
    pattern = re.compile(
        r"```(?:[\w./-]*)?\n"
        r"(?:#\s*(?:file|path|archivo):\s*(?P<path1>[^\n]+)\n)?"
        r"(?P<body>.*?)"
        r"```",
        re.DOTALL | re.IGNORECASE,
    )
    header_pattern = re.compile(
        r"(?:###?\s+|file:\s*|path:\s*|archivo:\s*)([^\s`\n][^\n]*\.[a-zA-Z0-9]+)"
    )
    count = 0
    for m in pattern.finditer(agent_output):
        body  = m.group("body").strip()
        fpath = m.group("path1")
        if not fpath:
            before = agent_output[max(0, m.start() - 200):m.start()]
            hm = header_pattern.search(before)
            fpath = hm.group(1).strip() if hm else None
        if not fpath or not body:
            continue
        fpath = fpath.strip().lstrip("/")
        # escribir en output/ para registro
        out_f = OUTPUT_DIR / fpath
        out_f.parent.mkdir(parents=True, exist_ok=True)
        out_f.write_text(body, encoding="utf-8")
        # escribir en repo_dir para commitear
        repo_f = repo_dir / fpath
        repo_f.parent.mkdir(parents=True, exist_ok=True)
        repo_f.write_text(body, encoding="utf-8")
        print(f"  💾 {fpath}")
        count += 1
    return count


# ── HITL sobre el graph ───────────────────────────────────

def _hitl_node(nodo: str, estado: dict) -> str:
    """
    Human-in-the-Loop sobre un nodo ya ejecutado por el graph.

    El nodo corrio una vez (via harness.step). Aca mostramos su salida y
    pedimos [s/r/n]; con [r] el harness lo reejecuta y reinyecta el
    resultado con update_state(as_node=...).

    Ojo: esto NO decide si hay otra ronda de review. Eso lo resuelve
    should_retry() dentro del graph, segun el veredicto del chapter.
    """
    label = NODE_LABELS.get(nodo, nodo)
    campo = STATE_FIELD[nodo]
    while True:
        salida = estado.get(campo, "") or ""
        if not salida:
            print(f"  ⏩ {label}: sin salida (nodo omitido por project_type).")
            return ""

        print(f"\n{'─'*60}")
        print(f"[OUTPUT] {label}:")
        print(salida[:1200] + ("..." if len(salida) > 1200 else ""))
        print(f"\n[Output completo: {len(salida)} caracteres]")

        opt = input(
            "\n¿Continuar? [s]iguiente / [r]eejecutar / [n]o: "
        ).strip().lower()
        if opt == "s":
            return salida
        elif opt == "r":
            print(f"\n[RETRY] Reejecutando {label}...")
            estado = retry_node(THREAD_ID, nodo)
        elif opt == "n":
            print(f"\n[CANCELADO] en paso '{label}'.")
            sys.exit(0)
        else:
            print("  ✗ Opcion invalida. Ingresa s, r o n.")


# ── Git helpers ───────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    return re.sub(r"[\s_]+", "-",
                  re.sub(r"[^\w\s-]", "", text.lower()).strip())[:50]


def _derive_commit_type(plan: str, chapter_out: str) -> str:
    text = (plan + " " + chapter_out).lower()
    if any(w in text for w in ("nueva funcionalidad", "new feature", "agregar", "añadir", "add ")):
        return "feat"
    if any(w in text for w in ("bug", "error crítico", "vulnerabilidad", "seguridad", "falla")):
        return "fix"
    if any(w in text for w in ("dependencia", "dependency", "upgrade", "version", "librería")):
        return "chore"
    return "refactor"


def _branch_name(repo_name: str, commit_type: str, plan: str) -> str:
    repo_slug = re.sub(r"[^\w-]", "-", repo_name.lower()).strip("-")[:30]
    desc_match = re.search(r"##\s+(.+)", plan)
    if desc_match:
        desc = _slugify(desc_match.group(1))[:25]
    else:
        first_line = plan.strip().splitlines()[0] if plan.strip() else "guardrails"
        desc = _slugify(first_line)[:25]
    date_str = datetime.now().strftime("%Y%m%d")
    return f"{commit_type}/{repo_slug}/{desc}-{date_str}"


def _commit_message(commit_type: str, repo_name: str, analysis: str) -> str:
    scope = re.sub(r"[^\w-]", "-", repo_name.lower())[:20].strip("-")
    findings = []
    for line in analysis.splitlines():
        line = line.strip()
        if line.startswith(("- ", "* ", "•")) and len(line) > 10:
            findings.append(line.lstrip("-*• ").strip())
        if len(findings) >= 1:
            break
    summary = findings[0][:72] if findings else "aplicar guardrails de calidad"
    return f"{commit_type}({scope}): {summary}"


def _git_branch_push(repo_dir: Path, branch: str, commit_msg: str) -> str:
    """
    Crea rama, commitea y pushea usando gh CLI.
    Ojo: S3 hace el push distinto — git plano con el token embebido
    en la URL del remote, sin gh.
    gh maneja autenticación y proxy automáticamente con las credenciales
    configuradas por `gh auth login`.
    """
    def run(cmd):
        return subprocess.run(cmd, cwd=str(repo_dir), capture_output=True, text=True)

    # Configurar identidad git
    run(["git", "config", "user.email", "guardrails@pipeline.local"])
    run(["git", "config", "user.name",  "Guardrails Pipeline"])

    # Delegar credenciales a gh para que git use su token automáticamente
    r = run(["gh", "auth", "setup-git"])
    if r.returncode != 0:
        print(f"  ⚠️  gh auth setup-git: {r.stderr.strip()[:200]}")
    else:
        print("  🔑 Credenciales git configuradas via gh")

    # Crear o reutilizar rama
    r = run(["git", "checkout", "-b", branch])
    if r.returncode != 0:
        if "already exists" in r.stderr:
            run(["git", "checkout", branch])
            print(f"  ℹ️  Rama '{branch}' ya existía — reutilizando.")
        else:
            print(f"  ⚠️  checkout -b: {r.stderr[:200]}")
    else:
        print(f"  ✅ Rama creada: {branch}")

    # Stage + commit
    run(["git", "add", "-A"])
    r = run(["git", "commit", "-m", commit_msg])
    if r.returncode != 0:
        stderr = r.stderr.strip()
        if "nothing to commit" in stderr or "nothing added" in stderr:
            print("  ℹ️  Sin cambios para commitear.")
        else:
            print(f"  ⚠️  git commit: {stderr[:300]}")
    else:
        print(f"  ✅ Commit: {commit_msg}")

    # Push: gh auth setup-git ya configuró las credenciales, git push las usa
    r = run(["git", "push", "--set-upstream", "origin", branch])
    if r.returncode == 0:
        print(f"  ✅ Push → origin/{branch}")
        return branch

    stderr = r.stderr.strip()
    raise RuntimeError(f"gh/git push falló: {stderr[:400]}")


def _gh_create_pr(title: str, body: str, head: str, base: str) -> str:
    """
    Crea el PR usando gh CLI directamente (igual que mcp_git._gh_cli_create_pr).
    Escribe el body en un archivo temporal para evitar límites de argumentos.
    """
    body_file = OUTPUT_DIR / ".pr_body.md"
    body_file.write_text(body, encoding="utf-8")
    cmd = [
        "gh", "pr", "create",
        "--title", title,
        "--body-file", str(body_file),
        "--head",  head,
        "--base",  base,
        "--repo",  f"{GITHUB_OWNER}/{GITHUB_REPO}",
    ]
    print(f"  [gh] {' '.join(cmd[:6])} ...")
    r = subprocess.run(cmd, capture_output=True, text=True)
    body_file.unlink(missing_ok=True)
    if r.returncode != 0:
        stderr = r.stderr.strip()
        if "already exists" in stderr.lower() or "a pull request for branch" in stderr.lower():
            return r.stdout.strip() or stderr
        raise RuntimeError(f"gh pr create falló: {stderr}")
    return r.stdout.strip()


# ── Pipeline principal ────────────────────────────────────────────────

def _step_label() -> str:
    return f"[{datetime.now().strftime('%H:%M:%S')}]"


def main():
    if not GITHUB_OWNER or not GITHUB_REPO:
        print("✗ GITHUB_OWNER y GITHUB_REPO deben estar configurados en el .env.")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("S4 Guardrails — Code Analysis & Refactoring Pipeline")
    print(f"Repo: {GITHUB_OWNER}/{GITHUB_REPO}")
    print("=" * 60 + "\n")

    # ── Checkpoint: retomar sesión anterior ─────────────────────────
    _ckpt  = load_checkpoint()
    _state: dict = {}
    _next_step   = "clone"

    with_data = include_data()
    if _ckpt and _ckpt.get("include_data", False) != with_data:
        print("El checkpoint pertenece a otro flujo. Conserva output/ con otro nombre antes de cambiar GUARDRAILS_INCLUDE_DATA.")
        raise SystemExit(1)
    _state["include_data"] = with_data
    if _ckpt:
        _next_step = _ckpt.get("next_step", "clone")
        _state.update({k: v for k, v in _ckpt.items() if k != "next_step"})
        print(f"[CHECKPOINT] Sesión anterior encontrada.")
        print(f"  Repo  : {GITHUB_OWNER}/{GITHUB_REPO}")
        print(f"  Tipo  : {_state.get('project_type','?').upper()}")
        print(f"  Ronda : {_state.get('review_round', 0)}")
        print(f"  Paso  : {_next_step}")
        opt = input("\n  ¿Retomar desde el checkpoint? [s/n]: ").strip().lower()
        if opt == "s":
            print(f"  → Retomando en: {_next_step}\n")
        else:
            clear_checkpoint()
            _state     = {"include_data": with_data}
            _next_step = "clone"
            print()

    # ── Paso 0: Clone ────────────────────────────────────────────────
    if _next_step == "clone":
        print(f"{_step_label()} Clonando {GITHUB_OWNER}/{GITHUB_REPO}...")
        repo_dir = _clone_or_pull(GITHUB_REPO)
        _state["repo_dir"] = str(repo_dir)
        save_checkpoint(_state, "architect")
        _next_step = "architect"
    else:
        _state["repo_dir"] = str(TMP_DIR / GITHUB_REPO)

    repo_dir = Path(_state["repo_dir"])

    # ── Paso 1: Snapshot + análisis ──────────────────────────────────
    # ── Leer el snapshot del repo clonado ────────────────────────────
    print(f"\n{_step_label()} Leyendo código del proyecto...")
    snapshot, file_count = _snapshot(repo_dir)
    if not snapshot:
        print("✗ No se pudo leer el código del repo.")
        sys.exit(1)
    print(f"  Snapshot: {file_count} archivos leídos.\n")

    # ── Guardrail de entrada: falla antes de gastar tokens ───────────
    _gate = validate_snapshot(snapshot)
    if not _gate.valid:
        print(f"✗ Guardrail de entrada: {_gate.reason}")
        sys.exit(1)
    print(f"  ✅ Guardrail de entrada: {_gate.reason}")

    # ── Tipo de proyecto: determina qué nodos de dev corren ──────────
    if _state.get("type_confirmed"):
        project_type = _state.get("project_type", _detect_type(repo_dir))
        print(f"  → Tipo de proyecto (confirmado): {project_type.upper()}")
    else:
        project_type = _confirm_type(_detect_type(repo_dir))
        _state["project_type"]   = project_type
        _state["type_confirmed"] = True
        save_checkpoint(_state, _next_step)

    # ── Arrancar el pipeline en el graph, vía harness ────────────────
    _repo_full = f"{GITHUB_OWNER}/{GITHUB_REPO}"

    if _next_step in ("push", "pr"):
        # El checkpoint ya pasó el loop de agentes: no hay nada que correr.
        print("  ⏩ Agentes: completados en una sesión anterior.")
    else:
        _ya_hechos = {
            "include_data": with_data,
            "resume_after": _next_step,
            "out_architect": _state.get("architect_out", ""),
            "out_analysis":    _state.get("analysis", ""),
            "out_plan":        _state.get("plan", ""),
            "out_data": _state.get("data_out", ""),
            "out_backend":     _state.get("backend_out", ""),
            "out_frontend":    _state.get("frontend_out", ""),
            "out_testing": _state.get("testing_out", ""),
            "out_security": _state.get("security_out", ""),
            "last_verdict": _state.get("last_verdict", ""),
            "out_chapter":    _state.get("chapter_out", ""),
            "review_round":    _state.get("review_round", 0),
            "review_feedback": _state.get("review_feedback", ""),
        }
        if any(_ya_hechos[k] for k in ("out_analysis", "out_plan", "out_backend")):
            # El MemorySaver es en-proceso: se perdió al reiniciar. Resembramos
            # y adelantamos con los artefactos del checkpoint, incluido el
            # estado del review loop.
            resume_with_harness(_repo_full, snapshot, project_type, THREAD_ID, _ya_hechos,
                                repo_dir=repo_dir)
            _pend = next_node(THREAD_ID)
            print(f"  → Retomando en: {NODE_LABELS.get(_pend, _pend or 'fin')}\n")
        else:
            run_with_harness(_repo_full, snapshot, project_type, THREAD_ID, repo_dir=repo_dir)

        # ── Bucle HITL: un nodo por vuelta ───────────────────────────
        # step() ejecuta exactamente un nodo gracias a interrupt_before.
        # Quien decide si hay otra ronda de review es should_retry() dentro
        # del graph: aquí el humano sólo confirma cada salida.
        while next_node(THREAD_ID) is not None:
            _nodo = next_node(THREAD_ID)
            _ronda = harness_state(THREAD_ID).get("review_round", 0) + 1
            _sufijo_lbl = (f" [Ronda {_ronda}/{_MAX_REVIEW_ROUNDS}]"
                           if _nodo in ("data", "backend", "frontend", "testing", "security", "chapter") else "")

            print(f"\n{_step_label()} {NODE_LABELS.get(_nodo, _nodo)}{_sufijo_lbl}...")
            _nodo_corrido, _estado = step(THREAD_ID)

            _salida = _hitl_node(_nodo_corrido, _estado)
            _estado = harness_state(THREAD_ID)
            if not _salida:
                continue                      # nodo omitido por project_type

            # Persistir el artefacto. Las rondas extra no pisan la primera.
            _fname, _titulo = (DATA_OUTPUT_FILES if with_data else OUTPUT_FILES)[_nodo_corrido]
            if _nodo_corrido in ("data", "backend", "frontend", "testing", "security", "chapter") and _ronda > 1:
                _fname = f"{Path(_fname).stem}_r{_ronda}.md"
            (OUTPUT_DIR / _fname).write_text(
                f"# {_titulo} — {GITHUB_REPO}\n\n{_salida}", encoding="utf-8")

            if _nodo_corrido in ("data", "backend", "frontend", "testing"):
                _n = _extract_files(_salida, repo_dir)
                print(f"  ✅ output/{_fname} ({_n} archivos extraídos)")
            else:
                print(f"  ✅ output/{_fname}")

            _state[CKPT_FIELD[_nodo_corrido]] = _salida
            if _nodo_corrido == "architect":
                _state["analysis"] = _estado["out_analysis"]
                _state["plan"] = _estado["out_plan"]
            _estado = harness_state(THREAD_ID)
            _state["last_verdict"] = _estado.get("last_verdict", "")
            _state["review_round"]    = _estado.get("review_round", 0)
            _state["review_feedback"] = _estado.get("review_feedback", "")
            save_checkpoint(_state, _nodo_corrido)

        _next_step = "push"

    # ── Recuperar los outputs finales del estado del graph ───────────
    _final       = harness_state(THREAD_ID)
    analysis     = _final.get("out_analysis", "") or _state.get("analysis", "")
    plan         = _final.get("out_plan", "")     or _state.get("plan", "")
    backend_out  = _final.get("out_backend", "")  or _state.get("backend_out", "")
    frontend_out = _final.get("out_frontend", "") or _state.get("frontend_out", "")
    chapter_out = _final.get("out_chapter", "") or _state.get("chapter_out", "")
    review_round = _final.get("review_round", 0)  or _state.get("review_round", 0)

    _veredicto = _final.get("last_verdict", _state.get("last_verdict", "observaciones"))
    print(f"\n[GRAPH] Pipeline completado. Veredicto: {_veredicto.upper()} "
          f"tras {review_round} ronda(s) de review.")

    # ── Guardrail de salida: no se abre PR con un output inválido ────
    if not _final:
        _final = {STATE_FIELD[node]: _state.get(CKPT_FIELD[node], "") for node in STATE_FIELD}
        _final.update(include_data=with_data, out_analysis=_state.get("analysis", ""), out_plan=_state.get("plan", ""),
                      review_round=review_round, last_verdict=_state.get("last_verdict", "observaciones"))
    _out_gate = validate_output(_final)
    if _out_gate.valid:
        print(f"  ✅ Guardrail de salida: {_out_gate.reason}")
    else:
        print(f"  ⚠️  Guardrail de salida [{_out_gate.rule_code}] "
              f"en '{_out_gate.field}': {_out_gate.reason}")
        if input("¿Continuar igual? [s/N]: ").strip().lower() != "s":
            print("Cancelado por el guardrail de salida.")
            sys.exit(1)

    # ── Compliance con la constitución ───────────────────────────────
    if _final.get("context"):
        print("\n[HARNESS] Verificando compliance con la constitución...")
        _compliance = check_security_compliance(
            " ".join([_final.get("out_data", ""), backend_out, frontend_out, chapter_out]))
        _passed, _total = sum(_compliance.values()), len(_compliance)
        print(f"[HARNESS] Score: {_passed}/{_total}")
        for _rule, _ok in _compliance.items():
            _marca = "✅" if _ok else "❌"
            print(f"  {_marca} {_rule}")
        _ev = Path(__file__).parent / "evidence"
        _ev.mkdir(exist_ok=True)
        (_ev / "compliance_report.json").write_text(
            json.dumps({"repo": GITHUB_REPO, "score": f"{_passed}/{_total}",
                        "review_rounds": review_round,
                        "compliance": _compliance},
                       indent=2, ensure_ascii=False),
            encoding="utf-8")
        print("  ✅ evidence/compliance_report.json")
        _state["compliance_score"] = f"{_passed}/{_total}"

    save_checkpoint(_state, "push")
    _next_step = "push"

    # ── Paso 5: Git commit + push ─────────────────────────────────────
    if _next_step == "push":
        print(f"\n{_step_label()} Preparando commit y push...")

        commit_type = _derive_commit_type(
            _state.get("plan", ""), _state.get("chapter_out", ""))
        branch = HEAD_BRANCH or _branch_name(GITHUB_REPO, commit_type,
                                             _state.get("plan", ""))
        msg    = _commit_message(commit_type, GITHUB_REPO, _state.get("analysis", ""))

        rounds_done = _state.get("review_round", 1)
        print(f"[GIT] Tipo de commit    : {commit_type}")
        print(f"[GIT] Rondas de review  : {rounds_done}")
        print(f"[GIT] Veredicto final   : {_state.get('last_verdict', '?').upper()}")
        print(f"[GIT] Rama              : {branch}")
        print(f"[GIT] Mensaje           : {msg}")

        _state["head_branch"]  = branch
        _state["base_branch"]  = BASE_BRANCH
        _state["commit_type"]  = commit_type
        _state["commit_msg"]   = msg

        _git_branch_push(repo_dir, branch, msg)
        save_checkpoint(_state, "pr")
        _next_step = "pr"

    # ── Paso 6: Crear PR ─────────────────────────────────────────────
    if _next_step in ("push", "pr"):
        _head = _state.get("head_branch", "")
        _base = _state.get("base_branch", BASE_BRANCH)
        _msg  = _state.get("commit_msg", "Refactorización guardrails")
        _type = _state.get("commit_type", "refactor")
        _rnds = _state.get("review_round", 1)
        _verd = _state.get("last_verdict", "aprobado").upper()

        if not _head:
            print("  ⚠️  No se encontró el branch en el checkpoint — no se puede crear el PR.")
        else:
            print(f"\n[gh] Creando Pull Request...")
            pr_title = f"{_type}({GITHUB_REPO[:20]}): guardrails refactoring"
            pr_body  = (
                f"## Guardrails Refactoring — {GITHUB_REPO}\n\n"
                f"**Tipo:** {_type}  \n"
                f"**Commit:** {_msg}  \n"
                f"**Rondas de review:** {_rnds}  \n"
                f"**Veredicto final:** {_verd}\n\n"
                f"### Análisis\n{_ctx(_state.get('analysis', ''), 500)}\n\n"
                f"### Plan\n{_ctx(_state.get('plan', ''), 500)}\n\n"
                f"### Datos y persistencia\n{_ctx(_state.get('data_out', ''), 600)}\n\n"
                f"### Pruebas\n{_ctx(_state.get('testing_out', ''), 600)}\n\n"
                f"### Seguridad\n{_ctx(_state.get('security_out', ''), 600)}\n\n"
                f"### Veredicto del Chapter\n{_ctx(_state.get('chapter_out', ''), 400)}\n\n"
                + (f"Closes #{_ISSUE}\n\n" if _ISSUE else "")
                + f"---\n_Generado automáticamente por s4_guardrails pipeline_"
            )
            try:
                pr_url = _gh_create_pr(pr_title, pr_body, _head, _base)
                _ev = Path(__file__).parent / "evidence"
                _ev.mkdir(exist_ok=True)
                (_ev / "pr_url.txt").write_text(pr_url, encoding="utf-8")  # Gate G2: Git y PR
                print(f"\n✅ PR creado: {pr_url}")
                clear_checkpoint()
            except RuntimeError as e:
                print(f"\n⚠️  Error al crear PR: {e}")
                save_checkpoint(_state, "pr")
                print("  El checkpoint fue guardado. Corrige el error y vuelve a ejecutar.")
                sys.exit(1)

    print("\n" + "=" * 60)
    print("✅ Pipeline completado.")
    print("=" * 60)


if __name__ == "__main__":
    main()
