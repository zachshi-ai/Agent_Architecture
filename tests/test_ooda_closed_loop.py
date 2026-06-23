"""Pillar 2: the OODA loop is a real closed loop, not a passive recorder.

Three things were broken before and are fixed here:
1. Persistence — trajectories survive a process restart (SQLite under base/).
2. Auto-trigger — record() runs Orient/Decide and files hits into a review
   queue every task, not just when a human asks.
3. Act — a human approves a queued suggestion, which lands in rules/auto (or
   skills/auto); a *new* governance engine loading that dir then enforces it.
   Nothing mutates the live set; the loop only adds, read-only on next start.
"""
from __future__ import annotations

from taiyi.core.types import PermitRequest, Verdict
from taiyi.governance import GovernanceEngine
from taiyi.iteration import IterationEngine


def _ctx(state, scenario, tools, *, task_id, fail_reason=None):
    """A minimal duck-typed context that record() can read."""

    class _Step:
        def __init__(self, tool):
            self.step = type("S", (), {"tool": tool, "args": []})()
            self.verdict = "ALLOW" if state == "COMPLETED" else "DENY"
            self.output = "ok" if state == "COMPLETED" else None
            self.matched_rule_id = None
            self.executed = state == "COMPLETED"

    class _Ctx:
        pass

    c = _Ctx()
    c.task_id = task_id
    c.scenario = scenario
    c.plan = None
    c.state = type("St", (), {"value": state})()
    c.prompt = "do thing"
    c.step_results = [_Step(t) for t in tools]
    c.final_output = "done" if state == "COMPLETED" else fail_reason
    c.error = fail_reason
    c.validation_summary = fail_reason
    c.value_contribution = None
    return c


# --- 1. Persistence: trajectories survive a restart --------------------------

def test_trajectories_persist_across_engine_instances(tmp_path):
    eng = IterationEngine(tmp_path)
    eng.record(_ctx("FAILED", "ops.x", ("tool:risky",), task_id="t1", fail_reason="boom"))
    assert len(eng.store.records) == 1

    # A brand-new engine pointing at the same base sees the history.
    eng2 = IterationEngine(tmp_path)
    assert len(eng2.store.records) == 1
    assert eng2.store.records[0].scenario == "ops.x"
    # The signal-rich step trail survived too.
    assert eng2.store.records[0].steps[0].tool == "tool:risky"


# --- 2. Auto-trigger: record() files suggestions without a human asking ------

def test_record_auto_files_rule_suggestion_into_review_queue(tmp_path):
    eng = IterationEngine(tmp_path)
    # Three failures of the same (scenario, tool) shape cross the threshold.
    for i in range(3):
        eng.record(_ctx("FAILED", "ops.x", ("tool:risky",), task_id=f"t{i}", fail_reason="boom"))

    pending = eng.list_pending()
    assert len(pending) == 1
    assert pending[0].kind == "rule"
    assert pending[0].status == "pending"
    assert pending[0].summary()["tool"] == "tool:risky"


def test_record_dedups_pending_suggestions(tmp_path):
    eng = IterationEngine(tmp_path)
    for i in range(5):  # well past threshold
        eng.record(_ctx("FAILED", "ops.x", ("tool:risky",), task_id=f"t{i}", fail_reason="boom"))
    # Still just one pending review for the same shape — no queue spam.
    assert len(eng.list_pending()) == 1


def test_below_threshold_no_pending(tmp_path):
    eng = IterationEngine(tmp_path)
    eng.record(_ctx("FAILED", "ops.x", ("tool:risky",), task_id="t0", fail_reason="boom"))
    assert eng.list_pending() == []


# --- 3. Act: human approve → rule lands in auto dir → new governance enforces

def test_approve_lands_rule_that_governance_then_enforces(tmp_path):
    eng = IterationEngine(tmp_path)
    for i in range(3):
        eng.record(_ctx("FAILED", "ops.x", ("tool:risky",), task_id=f"t{i}", fail_reason="boom"))
    pending = eng.list_pending()
    assert len(pending) == 1

    rules_dir = tmp_path / "rules"
    skills_dir = tmp_path / "skills"
    path = eng.approve(pending[0].id, rules_dir=rules_dir, skills_dir=skills_dir)
    assert path is not None
    assert path.exists()
    # The auto subdir is where it landed.
    assert "auto" in str(path)

    # A fresh governance engine loading that rules dir now enforces the new check.
    gov = GovernanceEngine(rules_dir=rules_dir)
    verdict = gov.issue_permit(
        PermitRequest(tool="tool:risky", args=[], scenario="ops.x", task_id="t")
    ).verdict
    assert verdict is Verdict.NEEDS_REVIEW

    # The suggestion is no longer pending.
    assert all(p.status != "pending" for p in eng.list_pending()) or eng.list_pending() == []


def test_reject_keeps_rule_unenforced(tmp_path):
    eng = IterationEngine(tmp_path)
    for i in range(3):
        eng.record(_ctx("FAILED", "ops.x", ("tool:risky",), task_id=f"t{i}", fail_reason="boom"))
    pending = eng.list_pending()
    sid = pending[0].id
    eng.reject(sid)

    # The rejected suggestion is no longer pending.
    assert all(p.id != sid for p in eng.list_pending())
    # No rule file was ever written — the auto dir does not exist.
    assert not (tmp_path / "rules" / "auto").exists()


# --- 4. End-to-end: the same governance that ran the task picks up the new rule

def test_skill_suggestion_also_round_trips(tmp_path):
    eng = IterationEngine(tmp_path)
    # Three COMPLETED skill-less repeats of the same tool shape → skill candidate.
    for i in range(3):
        eng.record(_ctx("COMPLETED", "research.x", ("http:get", "file:write"), task_id=f"t{i}"))
    pending = eng.list_pending()
    skill_pending = [p for p in pending if p.kind == "skill"]
    assert len(skill_pending) == 1

    skills_dir = tmp_path / "skills"
    path = eng.approve(skill_pending[0].id, rules_dir=tmp_path / "rules", skills_dir=skills_dir)
    assert path is not None
    assert (path / "SKILL.md").exists()
    assert (path / "quality_gate.md").exists()
