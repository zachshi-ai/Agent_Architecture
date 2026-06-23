"""Pillar 2 end-to-end: the OODA loop through the gateway + HTTP review endpoints.

A task that fails repeatedly → IterationEngine auto-files a rule suggestion →
the review HTTP endpoint approves it → the rule lands in base/rules/auto → a
freshly built gateway loading that base now gates the tool. This is the full
"周行不殆" path: last task's result changes next task's governance, human-gated.
"""
from __future__ import annotations

import json

from taiyi.core.types import PermitRequest, Verdict
from taiyi.gateway import GatewayApp, build_gateway
from taiyi.governance import GovernanceEngine


def _failing_gateway(base):
    """A gateway whose executor always fails a given tool, so it records failures."""
    from taiyi.runtime.executor import Executor
    from taiyi.runtime.context import StepResult  # noqa: F401  (for type clarity)
    from taiyi.scheduler import ExecutionPlan, PlanStep

    class _FailExecutor(Executor):
        def execute(self, step):
            from taiyi.runtime.executor import ExecResult
            return ExecResult(output=f"[fail] {step.tool}", ok=False)

    class _FailPlanner:
        def plan(self, prompt, scenario):
            return ExecutionPlan(
                skill_name=None,
                steps=[PlanStep(tool="tool:risky", args=[])],
                rationale="risky op",
            )

    # Build with the failing executor + planner injected directly.
    from taiyi.core.audit import AuditLog
    from taiyi.approvals import ApprovalStore
    from taiyi.governance import GovernanceEngine, LocalPermitClient
    from taiyi.iteration import IterationEngine
    from taiyi.memory import MemoryEngine
    from taiyi.observability import Observability
    from taiyi.runtime import TaskRuntime
    from taiyi.scheduler import SchedulerEngine
    from taiyi.validation import ValidationEngine
    from taiyi.value_stream import ValueStreamEngine
    from taiyi.scenarios import ScenarioMatcher, ScenarioRegistry
    from taiyi.skills import SkillRegistry
    from taiyi.multi_agent import ExpertCommittee
    from taiyi.gateway.core import Gateway
    from pathlib import Path

    b = Path(base)
    audit = AuditLog(b / "audit.jsonl")
    gov = GovernanceEngine(audit_log=audit, extra_rules_dirs=[str(b / "rules" / "auto")])
    sched = SchedulerEngine(LocalPermitClient(gov))
    sched._planner = _FailPlanner()
    iteration = IterationEngine(b)
    rt = TaskRuntime(
        sched, audit_log=audit, executor=_FailExecutor(),
        validator=None, memory=MemoryEngine(b), value_stream=ValueStreamEngine(),
        observability=Observability(), iteration=iteration, approvals=ApprovalStore(),
    )
    return Gateway(
        runtime=rt, scenario_matcher=ScenarioMatcher(ScenarioRegistry.load_dir()),
        skills=SkillRegistry.load_dir(), memory=MemoryEngine(b),
        observability=Observability(), iteration=iteration, committee=ExpertCommittee(),
        approvals=ApprovalStore(), base_dir=str(b),
    )


def test_ooda_full_loop_through_gateway(tmp_path):
    gw = _failing_gateway(tmp_path)
    app = GatewayApp(gw)

    # Run the same failing task 3 times — each records a trajectory; the 3rd
    # crosses the threshold and the loop auto-files a rule suggestion.
    for _ in range(3):
        app.handle("POST", "/v1/tasks", {}, json.dumps({"prompt": "do risky", "scenario": "ops.x"}))

    # The suggestion is now pending and visible via the review endpoint.
    _, listing = app.handle("GET", "/v1/review/pending", {}, "")
    assert len(listing["pending"]) == 1
    sid = listing["pending"][0]["id"]
    assert listing["pending"][0]["kind"] == "rule"

    # Human approves via the review endpoint.
    status, resolved = app.handle("POST", f"/v1/review/{sid}/approve", {}, "{}")
    assert status == 200
    assert resolved["written_to"] is not None

    # The pending queue is now empty (it was approved).
    _, listing2 = app.handle("GET", "/v1/review/pending", {}, "")
    assert listing2["pending"] == []

    # A freshly built gateway loading the same base now enforces the new rule:
    # tool:risky in ops.x is no longer a bare ALLOW but a NEEDS_REVIEW.
    gov2 = GovernanceEngine(rules_dir=tmp_path / "rules")
    verdict = gov2.issue_permit(
        PermitRequest(tool="tool:risky", args=[], scenario="ops.x", task_id="t")
    ).verdict
    assert verdict is Verdict.NEEDS_REVIEW


def test_review_reject_endpoint(tmp_path):
    gw = _failing_gateway(tmp_path)
    app = GatewayApp(gw)
    for _ in range(3):
        app.handle("POST", "/v1/tasks", {}, json.dumps({"prompt": "do risky", "scenario": "ops.x"}))
    _, listing = app.handle("GET", "/v1/review/pending", {}, "")
    sid = listing["pending"][0]["id"]

    status, resolved = app.handle("POST", f"/v1/review/{sid}/reject", {}, "{}")
    assert status == 200
    # No file written on reject.
    assert resolved["written_to"] is None
    assert not (tmp_path / "rules" / "auto").exists()
