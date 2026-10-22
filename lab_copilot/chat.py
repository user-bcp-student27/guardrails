"""The invoke interface used by LangGraph nodes in S2–S4."""

import os
from langchain_core.messages import AIMessage
from lab_copilot.runtime import complete_sync


class CopilotChat:
    def __init__(self, model: str | None = None):
        self.model = model or os.getenv("COPILOT_MODEL", "gpt-6-luna")

    def invoke(self, messages):
        return AIMessage(content=complete_sync(messages, self.model))
