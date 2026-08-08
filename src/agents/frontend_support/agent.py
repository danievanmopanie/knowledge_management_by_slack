"""Frontend Support Knowledge Agent with Hybrid RAG."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from src.agents.base import BaseAgent
from src.knowledge.retriever import HybridRetriever
from src.llm.client import get_llm

SYSTEM_PROMPT = """You are a senior Frontend Support specialist helping the IT Frontend Support team.

Your goals:
- Give clear, practical, step-by-step guidance
- Prefer the organisation's own knowledge (provided in context) over generic advice
- Cite sources when you use retrieved knowledge
- If the knowledge base does not contain enough information, say so honestly and suggest next steps or escalation
- Keep answers concise and actionable for technicians in the field

When knowledge context is provided, use it as the primary source of truth.
"""


class FrontendSupportAgent(BaseAgent):
    """Answers support questions using Hybrid RAG (Vector + Graph) + local LLM."""

    name = "frontend_support"

    def __init__(self):
        self.retriever = HybridRetriever()
        self.llm = get_llm()

    async def handle(self, message: str, context: dict[str, Any]) -> str:
        # 1. Hybrid retrieval
        result = self.retriever.retrieve(message, k=5, graph_depth=1)
        knowledge_context = result.to_context_string()

        # 2. Build prompt
        user_content_parts = []
        if knowledge_context:
            user_content_parts.append("Knowledge context:\n" + knowledge_context)
            user_content_parts.append("---")
        user_content_parts.append("User question:\n" + message)

        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content="\n\n".join(user_content_parts)),
        ]

        # 3. Generate answer with local LLM
        try:
            response = await self.llm.ainvoke(messages)
            answer = response.content if hasattr(response, "content") else str(response)
        except Exception as e:
            return (
                "I retrieved some knowledge but failed to generate a full answer.\n"
                f"Error: `{type(e).__name__}: {e}`\n\n"
                "Raw retrieved context (for debugging):\n"
                f"{knowledge_context[:1500]}"
            )

        # 4. Optionally append short source hint
        sources = {
            doc.metadata.get("source")
            for doc in result.documents
            if doc.metadata.get("source")
        }
        if sources:
            answer = answer.rstrip() + "\n\n_Sources: " + ", ".join(sorted(sources)) + "_"

        return answer
