"""Transporte al modelo y adaptadores. Los prompts están en agents/."""

import time

from dotenv import load_dotenv
from lab_copilot.chat import CopilotChat

from graph.state import WorkflowState

load_dotenv()

_MAX_RETRIES = 3
_CTX_LIMIT = 4000

_llm = CopilotChat()


def _invoke(prompt: str) -> str:
    """Invoca el LLM con reintentos exponenciales ante errores de red."""
    for attempt in range(_MAX_RETRIES):
        try:
            return _llm.invoke(prompt).content
        except Exception as e:
            if attempt == _MAX_RETRIES - 1:
                raise
            wait = 10 * (attempt + 1)
            print(
                f"  ⚠️  Error (intento {attempt + 1}/{_MAX_RETRIES}): {e} — reintentando en {wait}s"
            )
            time.sleep(wait)


from agents import architect, data, backend, frontend, testing, security, chapter
from agents.chapter import parse_verdict


def architect_node(state: WorkflowState) -> dict:
    return architect.architect_node(state, _invoke)


def backend_node(state: WorkflowState) -> dict:
    return backend.backend_node(state, _invoke)


def frontend_node(state: WorkflowState) -> dict:
    return frontend.frontend_node(state, _invoke)


def testing_node(state: WorkflowState) -> dict:
    return testing.testing_node(state, _invoke)


def security_node(state: WorkflowState) -> dict:
    return security.security_node(state, _invoke)


def chapter_node(state: WorkflowState) -> dict:
    return chapter.chapter_node(state, _invoke)


def data_node(state: WorkflowState) -> dict:
    return data.data_node(state, _invoke)
