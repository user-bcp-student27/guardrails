"""Seis o siete nodos, pausas humanas y ciclo de correcciones."""

import os
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from graph.state import WorkflowState
from graph.nodes import (
    architect_node,
    data_node,
    backend_node,
    frontend_node,
    testing_node,
    security_node,
    chapter_node,
)

MAX_REVIEW_ROUNDS = int(os.getenv("MAX_REVIEW_ROUNDS", "3"))
NODE_FUNCTIONS = {
    "architect": architect_node,
    "backend": backend_node,
    "frontend": frontend_node,
    "testing": testing_node,
    "security": security_node,
    "chapter": chapter_node,
}
NODES = list(NODE_FUNCTIONS)


DATA_NODE_FUNCTIONS = {"architect": architect_node, "data": data_node,
                       **{key: value for key, value in NODE_FUNCTIONS.items() if key != "architect"}}


def include_data() -> bool:
    value = os.getenv("GUARDRAILS_INCLUDE_DATA", "false").strip().lower()
    if value not in ("true", "false"):
        raise ValueError("GUARDRAILS_INCLUDE_DATA debe ser true o false")
    return value == "true"


def should_retry(state: WorkflowState) -> str:
    if (
        state.get("last_verdict", "observaciones") != "aprobado"
        and state.get("review_round", 0) < MAX_REVIEW_ROUNDS
    ):
        return "data" if state.get("include_data") else "backend"
    return END


def build_graph(with_data=False):
    builder = StateGraph(WorkflowState)
    functions = DATA_NODE_FUNCTIONS if with_data else NODE_FUNCTIONS
    node_names = list(functions)
    for name, node in functions.items():
        builder.add_node(name, node)
    builder.set_entry_point("architect")
    for current, following in zip(node_names, node_names[1:]):
        builder.add_edge(current, following)
    retry_target = "data" if with_data else "backend"
    builder.add_conditional_edges("chapter", should_retry, {retry_target: retry_target, END: END})
    return builder.compile(checkpointer=MemorySaver(), interrupt_before=node_names)


graph = build_graph()
