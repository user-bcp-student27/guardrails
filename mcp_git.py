"""
Cliente HTTP para el MCP de git — mcp-git
Ramas, commits y PRs via MCP Streamable HTTP.

Variables de entorno:
    GIT_MCP_URL       URL base (default: http://localhost:8082)
    MCP_GIT_URL       Alias de GIT_MCP_URL
    MCP_TIMEOUT       Timeout en segundos (default: 60)
    GITHUB_OWNER      Org / usuario GitHub
    GITHUB_REPO       Repo destino de PRs
    GIT_HEAD_BRANCH   Rama feature (opcional, si está fija por equipo)
    GIT_BASE_BRANCH   Rama base (default: main)
"""
import os, re, json, base64
from pathlib import Path
from typing import Any

try:
    import httpx
    _HTTPX_OK = True
except ImportError:
    _HTTPX_OK = False

_BASE         = os.getenv("GIT_MCP_URL", os.getenv("MCP_GIT_URL", "http://localhost:8082"))
_TIMEOUT      = int(os.getenv("MCP_TIMEOUT", "60"))
GITHUB_OWNER  = os.getenv("GITHUB_OWNER",  "")          # sin default hardcodeado
GITHUB_REPO   = os.getenv("GITHUB_REPO",   os.getenv("API_REPO", ""))
FRONTEND_REPO = os.getenv("FRONTEND_REPO", "")
BACKEND_REPO  = os.getenv("BACKEND_REPO",  "")
API_REPO      = os.getenv("API_REPO",      "")
BASE_BRANCH   = os.getenv("GIT_BASE_BRANCH", "main")
HEAD_BRANCH   = os.getenv("GIT_HEAD_BRANCH", "")

_SESSION = {
    "id":           None,
    "ready":        False,
    "real_tools":   None,   # lista de nombres de tools reales
    "tool_actions": {},     # tool_name → [action, ...]
}
_HEADERS = {
    "Content-Type": "application/json",
    "Accept":       "application/json, text/event-stream",
}

# ── Verbose / logging ──────────────────────────────────────────────────
_VERBOSE = False

def set_verbose(flag: bool) -> None:
    """Activa o desactiva los logs internos del cliente MCP Git.
    Llamar desde main.py: import mcp_git; mcp_git.set_verbose(True)
    """
    global _VERBOSE
    _VERBOSE = flag

def _log(*args) -> None:
    if _VERBOSE:
        print(*args)

# Errores que indican "esta tool/action no existe o no aplica" → seguir probando
_NOT_FOUND = (
    "not found", "unknown", "no tool", "invalid action",
    "unsupported", "not support", "ninguna tool",
    "method not found", "tool not found",
    "invalid arguments",    # "Input validation error: Invalid arguments for tool X"
    "input validation",     # idem
    "allowed values",       # "data/action must be equal to one of the allowed values"
    "enum",                 # variante del mismo error de schema
)


# ── Transport ──────────────────────────────────────────────────────────

def _post(payload: dict, ignore_resp: bool = False):
    if not _HTTPX_OK:
        raise RuntimeError("httpx no instalado — pip install httpx")
    h = dict(_HEADERS)
    if _SESSION["id"]:
        h["Mcp-Session-Id"] = _SESSION["id"]
    try:
        r = httpx.post(f"{_BASE}/mcp", json=payload, headers=h, timeout=_TIMEOUT)
    except httpx.ConnectError:
        raise RuntimeError(f"No se pudo conectar a {_BASE} — ¿docker compose up?")
    if ignore_resp or r.status_code == 204:
        return None
    r.raise_for_status()
    return r


def _parse(r) -> dict:
    if r is None:
        return {}
    ct = r.headers.get("content-type", "")
    if "text/event-stream" in ct:
        for line in r.text.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                chunk = line[5:].strip()
                if chunk and chunk != "[DONE]":
                    try:
                        return json.loads(chunk)
                    except json.JSONDecodeError:
                        pass
        return {}
    return r.json()


def _init():
    if _SESSION["ready"]:
        return
    try:
        r = _post({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                   "params": {"protocolVersion": "2024-11-05",
                               "capabilities": {"tools": {}},
                               "clientInfo": {"name": "reqflow", "version": "1.0"}}})
        if r:
            sid = r.headers.get("mcp-session-id", "")
            if sid:
                _SESSION["id"] = sid
        _post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
              ignore_resp=True)
    except Exception as e:
        _log(f"  [mcp-git] ⚠️  Initialize: {e} — continuando igual")
    _SESSION["ready"] = True


def _call(tool: str, args: dict = {}) -> Any:
    _init()
    r = _post({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": tool, "arguments": args}})
    data = _parse(r)
    if "error" in data:
        raise RuntimeError(f"MCP [{tool}]: {data['error']}")
    for block in data.get("result", {}).get("content", []):
        if block.get("type") == "text":
            try:
                return json.loads(block["text"])
            except json.JSONDecodeError:
                return block["text"]
    return data.get("result", {})


# ── Discover ───────────────────────────────────────────────────────────

def _discover() -> list:
    """
    Descubre el mapa real tool_name → actions del servidor MCP.

    Estrategia:
      1. tools/list  → obtiene nombres + inputSchema (busca enum del campo 'action')
      2. Si solo hay discover_tools (o ninguna), la llama para obtener el mapa completo.

    Carga _SESSION["real_tools"] y _SESSION["tool_actions"].
    """
    if _SESSION["real_tools"] is not None:
        return _SESSION["real_tools"]

    _init()
    names: list        = []
    tool_actions: dict = {}

    # ── Paso A: tools/list — extrae nombres Y enums del campo action ──
    try:
        r    = _post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        data = _parse(r)
        tl   = data.get("result", {}).get("tools", [])
        for t in tl:
            tn = t.get("name")
            if not tn:
                continue
            names.append(tn)
            # Extraer enum de action desde el inputSchema del tool
            schema      = t.get("inputSchema", {})
            props       = schema.get("properties", {})
            action_prop = props.get("action", {})
            action_enum = action_prop.get("enum", [])
            if not action_enum:
                for variant in action_prop.get("anyOf", []) + action_prop.get("oneOf", []):
                    action_enum.extend(variant.get("enum", []))
            if action_enum:
                tool_actions[tn] = action_enum
                _log(f"  [mcp-git] {tn} actions (schema): {action_enum}")
            else:
                _log(f"  [mcp-git] {tn} (sin enum de action en schema)")
        _log(f"  [mcp-git] tools/list → {names}")
    except Exception as e:
        _log(f"  [mcp-git] ⚠️  tools/list: {e}")

    # ── Paso B: si solo hay discover_tools (o nada), llamarla ─────────
    if not names or names == ["discover_tools"]:
        try:
            r    = _post({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                           "params": {"name": "discover_tools", "arguments": {}}})
            data = _parse(r)
            result = None
            for block in data.get("result", {}).get("content", []):
                if block.get("type") == "text":
                    try:
                        result = json.loads(block["text"])
                    except json.JSONDecodeError:
                        result = block["text"]
                    break
            if result is None:
                result = data.get("result", {})

            if isinstance(result, list):
                for item in result:
                    if isinstance(item, dict):
                        n  = item.get("name") or item.get("tool") or str(item)
                        ac = item.get("actions", [])
                        if n not in names:
                            names.append(n)
                        if ac:
                            tool_actions[n] = ac
                    else:
                        n = str(item)
                        if n not in names:
                            names.append(n)

            elif isinstance(result, dict):
                # Estructura: {group_name: {"tool": tool_name, "actions": [...]}}
                for grp_name, grp_data in result.items():
                    if isinstance(grp_data, dict) and "tool" in grp_data:
                        tn = grp_data["tool"]
                        ac = grp_data.get("actions", [])
                        if tn not in names:
                            names.append(tn)
                        if ac:
                            tool_actions[tn] = ac
                    elif isinstance(grp_data, list):
                        for i in grp_data:
                            n = (i.get("name") or i.get("tool") or str(i)) if isinstance(i, dict) else str(i)
                            if n not in names:
                                names.append(n)
                    elif isinstance(grp_data, str) and grp_data not in names:
                        names.append(grp_data)

            elif isinstance(result, str) and result.strip():
                names = [ln.strip() for ln in result.splitlines() if ln.strip()]

            _log(f"  [mcp-git] discover_tools → {names}")
            _log(f"  [mcp-git] actions map    → {tool_actions}")
        except Exception as e:
            _log(f"  [mcp-git] ⚠️  discover_tools: {e}")

    _SESSION["real_tools"]   = names
    _SESSION["tool_actions"] = tool_actions
    return names


def _action_for(tool: str, preferred: list) -> str | None:
    """
    Retorna la primera acción disponible de la lista preferred para el tool dado.
    Si ninguna coincide, retorna la primera disponible o None.
    (Igual que mcp_projects._action_for)
    """
    available = _SESSION.get("tool_actions", {}).get(tool, [])
    for a in preferred:
        if a in available:
            return a
    return available[0] if available else None


# ── Genérico con fallback exhaustivo ──────────────────────────────────

def _try_tools(direct_names: list, action_variants: list, args: dict) -> Any:
    """
    1. Intenta cada tool en direct_names directamente (sin action).
    2. Descubre tools reales y prueba cada combinación tool × action.
       Para cada tool, usa primero sus actions reales (del schema/discover),
       luego los action_variants genéricos que se le pasan.
    3. Si nada funciona → RuntimeError con diagnóstico completo.
    """
    tried_log: list = []

    # Paso 1 — tools directas conocidas
    for name in direct_names:
        try:
            return _call(name, args)
        except RuntimeError as e:
            err = str(e).lower()
            tried_log.append(f"{name}: {str(e)[:80]}")
            if "already exists" in err:
                raise
            if any(k in err for k in _NOT_FOUND):
                continue
            raise

    # Paso 2 — discover + probar con action
    real = _discover()
    for tool in real:
        discovered_actions = _SESSION.get("tool_actions", {}).get(tool, [])
        # Primero actions declaradas por el servidor, luego variants genéricas
        all_actions = list(dict.fromkeys(discovered_actions + action_variants))
        for action in all_actions:
            try:
                return _call(tool, {**args, "action": action})
            except RuntimeError as e:
                err = str(e).lower()
                tried_log.append(f"{tool}+action={action}: {str(e)[:80]}")
                if "already exists" in err:
                    raise
                if any(k in err for k in _NOT_FOUND):
                    continue
                raise

    diag_lines = "\n  ".join(tried_log) or "(nada intentado)"
    raise RuntimeError(
        f"No se encontró tool válida. Resumen de intentos:\n  {diag_lines}\n"
        f"Tools reales del servidor : {real}\n"
        f"Actions map               : {_SESSION.get('tool_actions', {})}"
    )


# ── API pública ────────────────────────────────────────────────────────

def list_tools() -> list:
    if not _HTTPX_OK:
        _log("  ⚠️  httpx no instalado"); return []
    try:
        tools = _discover()
        _log(f"  [mcp-git] {len(tools)} tools disponibles: {tools}")
        return tools
    except Exception as e:
        _log(f"  ⚠️  [mcp-git] {e}"); return []


def ensure_branches(feature_title: str) -> tuple:
    if not GITHUB_OWNER:
        raise RuntimeError("GITHUB_OWNER no está configurado en el .env")
    fixed = os.getenv("GIT_HEAD_BRANCH", "").strip()
    head  = fixed if fixed else f"feature/workshop/{_slugify(feature_title)}"
    base  = os.getenv("GIT_BASE_BRANCH", "main")
    try:
        _try_tools(
            direct_names    = ["create_branch", "github_create_branch", "create_ref"],
            action_variants = ["create_branch", "create_ref", "create"],
            args = {"owner": GITHUB_OWNER, "repo": GITHUB_REPO,
                    "branch": head, "from_branch": base},
        )
        _log(f"  [mcp-git] Rama '{head}' creada desde '{base}'")
    except RuntimeError as e:
        if "already exists" in str(e).lower():
            _log(f"  [mcp-git] Rama '{head}' ya existe — ok")
        else:
            _log(f"  [mcp-git] ⚠️  {e}")
    return head, base


def commit_and_push(output_dir: str, head: str, base: str, message: str) -> None:
    if not GITHUB_OWNER:
        raise RuntimeError("GITHUB_OWNER no está configurado en el .env")
    out    = Path(output_dir)
    pushed = skipped = 0
    for f in sorted(out.rglob("*")):
        if not f.is_file():
            continue
        rel  = f.relative_to(out).as_posix()
        text = f.read_text(encoding="utf-8", errors="ignore")
        b64  = base64.b64encode(text.encode()).decode()
        try:
            _try_tools(
                direct_names    = ["create_or_update_file", "push_files",
                                   "github_create_or_update_file", "push_file"],
                action_variants = ["create_or_update_file", "push_file",
                                   "commit_file", "create"],
                args = {"owner": GITHUB_OWNER, "repo": GITHUB_REPO, "branch": head,
                        "path": rel, "content": text, "content_base64": b64,
                        "message": message},
            )
            pushed += 1
            _log(f"  [mcp-git] ✅ {rel}")
        except Exception as e:
            skipped += 1
            _log(f"  [mcp-git] ⚠️  {rel}: {e}")
    _log(f"  [mcp-git] {pushed} subido(s), {skipped} saltado(s) → '{head}'")


def create_pr(title: str, body: str, head: str, base: str) -> str:
    if not GITHUB_OWNER:
        raise RuntimeError("GITHUB_OWNER no está configurado en el .env")
    _discover()  # asegura que tool_actions esté cargado

    # Errores del servidor MCP que indican bug GraphQL (user vs org)
    _GH_GRAPHQL_BUG = (
        "could not resolve to a user",
        "graphql",
        "github api error",
        "request failed due to following response errors",
    )

    # ── Paso 1: intentar con tools directas (sin manage_project) ──────
    args = {
        "owner": GITHUB_OWNER, "repo": GITHUB_REPO,
        "title": title, "body": body, "head": head, "base": base, "draft": False,
    }
    try:
        result = _try_tools(
            direct_names    = ["create_pull_request", "github_create_pull_request",
                               "create_pr", "open_pull_request"],
            action_variants = ["create_pull_request", "create_pr", "pull_request",
                               "pr", "open_pr", "open_pull_request", "create"],
            args = args,
        )
        return _extract_pr_url(result, head, base)
    except RuntimeError as e:
        err = str(e).lower()
        if "already exists" in err:
            return f"PR ya existe para '{head}' → '{base}'"
        # Si es bug GraphQL del MCP (org vs user) → fallback a gh CLI
        if any(k in err for k in _GH_GRAPHQL_BUG):
            _log(f"  [mcp-git] ⚠️  MCP falla por bug GraphQL (org vs user) — usando gh CLI")
        else:
            _log(f"  [mcp-git] ⚠️  MCP sin tool de PR — intentando gh CLI. Error: {str(e)[:120]}")

    # ── Paso 2: fallback a gh CLI ──────────────────────────────────────
    return _gh_cli_create_pr(title, body, head, base)


_GH_API_ERRORS = (
    "github api error",
    "could not resolve",
    "request failed due to following response errors",
    "graphql",
    "not found",
    "bad credentials",
    "must be a member",
    "repository not found",
)

def _gh_cli_create_pr(title: str, body: str, head: str, base: str) -> str:
    """
    Fallback: crea el PR usando GitHub CLI (gh pr create).
    Evita el bug del servidor MCP que usa user() en vez de repositoryOwner()
    en GraphQL, lo que falla para organizaciones.
    """
    import subprocess
    cmd = [
        "gh", "pr", "create",
        "--title", title,
        "--body",  body,
        "--head",  head,
        "--base",  base,
        "--repo",  f"{GITHUB_OWNER}/{GITHUB_REPO}",
    ]
    _log(f"  [mcp-git] 🔀 Fallback gh CLI: {' '.join(cmd[:5])} ...")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        stderr = r.stderr.strip()
        if "already exists" in stderr.lower() or "a pull request for branch" in stderr.lower():
            # gh imprime la URL del PR existente en stdout
            existing = r.stdout.strip() or stderr
            return existing
        raise RuntimeError(f"gh pr create falló: {stderr}")
    return r.stdout.strip()   # devuelve la URL del PR


def _extract_pr_url(result: Any, head: str, base: str) -> str:
    """Extrae la URL del PR del resultado. Lanza RuntimeError si la respuesta
    contiene un error de la GitHub API embebido en un result exitoso del MCP."""
    result_str = str(result)
    low = result_str.lower()
    if any(k in low for k in _GH_API_ERRORS):
        raise RuntimeError(f"GitHub API error en respuesta MCP: {result_str}")
    if isinstance(result, dict):
        url = (result.get("url") or result.get("html_url")
               or result.get("pr_url") or result.get("number"))
        return str(url) if url else str(result)
    return str(result)


def _slugify(text: str) -> str:
    return re.sub(r"[\s_]+", "-", re.sub(r"[^\w\s-]", "", text.lower()).strip())[:50]
