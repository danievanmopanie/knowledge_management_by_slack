"""Builds the Work Management LangGraph graph.

Six nodes, matching BRS FR-30:
    orchestrator -> {work_approver | planner | work_executioner | END}
    planner -> scheduler -> resource_coordinator -> END
    work_approver -> END
    work_executioner -> END

The Routine Keeper is deliberately NOT a node here - it runs outside the
Slack message path entirely, on systemd timers (see
src/work_management/routine_keeper.py and BRS §8). It reads the same
WorkManagementStore but is never invoked through this graph.
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from src.agents.work_management.nodes import (
    make_orchestrator_node,
    make_planner_node,
    make_resource_coordinator_node,
    make_scheduler_node,
    make_work_approver_node,
    make_work_executioner_node,
)
from src.agents.work_management.state import WorkManagementState
from src.work_management.store import WorkManagementStore

_ORCHESTRATOR_ROUTES = {
    "approve": "work_approver",
    "reject": "work_approver",
    "lock_plan": "work_approver",
    "plan_item": "planner",
    "start_item": "work_executioner",
    "done_item": "work_executioner",
    "blocked_item": "work_executioner",
}


def _route_from_orchestrator(state: WorkManagementState) -> str:
    return _ORCHESTRATOR_ROUTES.get(state.get("intent", ""), "end")


def build_graph(store: WorkManagementStore, llm=None):
    """Compile the graph once per store/llm pair. Cheap enough to call
    per-agent-instance; do not rebuild per-message."""
    graph = StateGraph(WorkManagementState)

    graph.add_node("orchestrator", make_orchestrator_node(store))
    graph.add_node("work_approver", make_work_approver_node(store))
    graph.add_node("planner", make_planner_node(store, llm=llm))
    graph.add_node("scheduler", make_scheduler_node(store))
    graph.add_node("resource_coordinator", make_resource_coordinator_node(store))
    graph.add_node("work_executioner", make_work_executioner_node(store))

    graph.set_entry_point("orchestrator")
    graph.add_conditional_edges(
        "orchestrator",
        _route_from_orchestrator,
        {
            "work_approver": "work_approver",
            "planner": "planner",
            "work_executioner": "work_executioner",
            "end": END,
        },
    )
    graph.add_edge("planner", "scheduler")
    graph.add_edge("scheduler", "resource_coordinator")
    graph.add_edge("resource_coordinator", END)
    graph.add_edge("work_approver", END)
    graph.add_edge("work_executioner", END)

    return graph.compile()
