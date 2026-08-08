"""Frontend Support Knowledge Agent with Hybrid RAG + Incident RAG."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from src.agents.base import BaseAgent
from src.knowledge.incident_rag import IncidentRAG
from src.knowledge.retriever import HybridRetriever
from src.llm.client import get_llm

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a senior Frontend Support specialist helping the IT Frontend Support team.

Your goals:
- Give clear, practical, step-by-step guidance
- Prefer the organisation's own knowledge and *similar past incidents* over generic advice
- Cite sources / incident numbers when you use retrieved context
- If the knowledge base does not contain enough information, say so honestly and suggest next steps or escalation
- Keep answers concise and actionable for technicians in the field

When past incident context is provided, highlight patterns that may help resolve the current issue faster.
"""


class FrontendSupportAgent(BaseAgent):
    """Answers support questions using knowledge RAG + incident RAG + local LLM."""

    name = "frontend_support"

    def __init__(self):
        self.retriever = HybridRetriever()
        self.incident_rag = IncidentRAG()
        self.llm = get_llm()

    async def handle(self, message: str, context: dict[str, Any]) -> str:
        # 1. General knowledge retrieval
        result = self.retriever.retrieve(message, k=5, graph_depth=1)
        knowledge_context = result.to_context_string()

        # 2. Similar past incidents
        incident_context = ""
        try:
            incident_context = self.incident_rag.build_context(message, k=5)
        except Exception:
            logger.exception("Incident RAG retrieval failed")

        # 3. Build prompt
        user_content_parts = []
        if incident_context:
            user_content_parts.append(incident_context)
            user_content_parts.append("---")
        if knowledge_context:
            user_content_parts.append("Knowledge articles & notes:\n" + knowledge_context)
            user_content_parts.append("---")
        user_content_parts.append("User question:\n" + message)

        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content="\n\n".join(user_content_parts)),
        ]

        # 4. Generate answer with local LLM
        try:
            response = await self.llm.ainvoke(messages)
            answer = response.content if hasattr(response, "content") else str(response)
        except Exception as e:
            return (
                "I retrieved some context but failed to generate a full answer.\n"
                f"Error: `{type(e).__name__}: {e}`\n\n"
                "Raw context (for debugging):\n"
                f"{(incident_context or knowledge_context)[:1500]}"
            )

        # 5. Source hints
        sources = {
            doc.metadata.get("source")
            for doc in result.documents
            if doc.metadata.get("source")
        }
        try:
            for doc in self.incident_rag.similar_incidents(message, k=3):
                num = (doc.metadata or {}).get("number")
                if num:
                    sources.add(f"incident:{num}")
        except Exception:
            pass

        if sources:
            answer = answer.rstrip() + "\n\n_Sources: " + ", ".join(sorted(s for s in sources if s)) + "_"

        return answer
