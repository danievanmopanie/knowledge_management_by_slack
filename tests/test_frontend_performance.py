import asyncio
import threading

from langchain_core.documents import Document

from src.agents.frontend_support.agent import FrontendSupportAgent
from src.core.context import RequestContext
from src.knowledge.retrieval_models import RetrievalCandidate
from src.knowledge.support_evidence import (
    CONVERSATIONAL_LIMIT,
    PROMPT_CONTEXT_MAX_CHARS,
    SupportEvidenceService,
)


class GovernedResult:
    candidates = []
    should_answer = False
    confidence_score = 0.0

    def to_context_string(self):
        return ""


class ParallelRetriever:
    def __init__(self, barrier):
        self.barrier = barrier
        self.calls = 0
        self.limits = []

    def search(self, query):
        self.calls += 1
        self.limits.append(query.limit)
        self.barrier.wait(timeout=1)
        return GovernedResult()


class ParallelIncidentRAG:
    def __init__(self, barrier):
        self.barrier = barrier
        self.calls = 0
        self.ks = []
        self.graph_store = object()

    def similar_incidents(self, query, k=5):
        self.calls += 1
        self.ks.append(k)
        self.barrier.wait(timeout=1)
        return [
            Document(
                page_content="Problem match\nResolution match",
                metadata={"number": "INC1", "score": 0.9, "state": "Closed"},
            )
        ]


class EmptyGraph:
    def related(self, entity, depth=2):
        return []


def test_evidence_retrieval_is_parallel_single_search_top3_and_cached():
    barrier = threading.Barrier(2)
    retriever = ParallelRetriever(barrier)
    incidents = ParallelIncidentRAG(barrier)
    service = SupportEvidenceService(
        retriever=retriever,
        incident_rag=incidents,
        support_graph=EmptyGraph(),
    )
    context = RequestContext.from_slack(channel_id="C1", user_id="U1")

    first = service.build("Teams timeout", context, limit=5)
    second = service.build("  teams   timeout ", context, limit=5)

    assert first.incident_context == second.incident_context
    assert retriever.calls == 1
    assert incidents.calls == 1
    assert retriever.limits == [CONVERSATIONAL_LIMIT]
    assert incidents.ks == [CONVERSATIONAL_LIMIT]


def test_prompt_context_is_capped_at_4500():
    from src.knowledge.support_evidence import SupportEvidencePackage

    package = SupportEvidencePackage(
        query="x",
        incident_context="i" * 3000,
        graph_context="g" * 2000,
        governed_context="k" * 3000,
    )
    rendered = package.to_prompt_context()
    assert rendered.startswith("i")
    assert len(rendered) <= PROMPT_CONTEXT_MAX_CHARS + len("\n\n[Support evidence truncated]")


class WeakGovernedResult:
    candidates = [
        RetrievalCandidate(
            evidence_id="babylon-profile",
            content="Babylon employee profile deletion",
            metadata={"source": "babylon"},
            score=0.4,
        )
    ]
    should_answer = True
    confidence_score = 0.40

    def to_context_string(self):
        return "Babylon employee profile deletion"


class WeakGovernedRetriever:
    def search(self, query):
        return WeakGovernedResult()


class MixedIncidentRAG:
    graph_store = object()

    def similar_incidents(self, query, k=5):
        return [
            Document(
                page_content="Unrelated docking station replacement",
                metadata={"number": "INC-WEAK", "score": 0.49, "state": "Closed"},
            ),
            Document(
                page_content="Laptop audio disappeared; Windows Audio service was restarted",
                metadata={"number": "INC-AUDIO", "score": 0.81, "state": "Closed"},
            ),
        ]


def test_frontend_drops_weak_governed_and_incident_evidence_before_prompt():
    service = SupportEvidenceService(
        retriever=WeakGovernedRetriever(),
        incident_rag=MixedIncidentRAG(),
        support_graph=EmptyGraph(),
    )
    context = RequestContext.from_slack(channel_id="C1", user_id="U1")

    package = service.build("My laptop suddenly has no sound", context)

    assert package.governed_should_answer is False
    assert package.governed_candidates == []
    assert package.governed_context == ""
    assert "Babylon" not in package.to_prompt_context()
    assert "INC-WEAK" not in package.incident_context
    assert "INC-AUDIO" in package.incident_context
    assert package.incident_sources == {"incident:INC-AUDIO"}


class Chunk:
    def __init__(self, content):
        self.content = content


class StreamingLLM:
    async def astream(self, messages):
        for text in ("First ", "second ", "third"):
            yield Chunk(text)


async def _stream_case():
    agent = FrontendSupportAgent.__new__(FrontendSupportAgent)
    agent.llm = StreamingLLM()
    seen = []

    async def on_chunk(text):
        seen.append(text)

    answer = await agent._invoke_llm([], on_chunk=on_chunk)
    return answer, seen


def test_llm_streaming_emits_accumulated_answer():
    answer, seen = asyncio.run(_stream_case())
    assert answer == "First second third"
    assert seen == ["First ", "First second ", "First second third"]
