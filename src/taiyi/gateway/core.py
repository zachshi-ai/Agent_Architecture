"""The Gateway façade — the single entry point that wires the whole stack.

`build_gateway` assembles governance → scheduler → runtime, with memory, validation,
scenarios, and the gated skill catalog, all sharing one audit chain. `submit`
matches a scenario when none is given and runs the task. Channels (CLI, HTTP) sit
on top of this; they translate transport, they do not contain logic.

Defaults are safe and offline: the keyword planner and the side-effect-free mock
executor. Swap in the live LLM planner (M4) or the sandbox executor (M5) per
deployment without changing the gateway.
"""
from __future__ import annotations

from pathlib import Path

from taiyi.core.audit import AuditLog
from taiyi.governance import GovernanceEngine, LocalPermitClient
from taiyi.approvals import ApprovalStore
from taiyi.iteration import IterationEngine
from taiyi.llm import LLMProvider, make_provider
from taiyi.memory import MemoryEngine
from taiyi.multi_agent import ExpertCommittee
from taiyi.observability import Observability
from taiyi.agent import AgentRuntime
from taiyi.runtime import TaskContext, TaskRuntime
from taiyi.runtime.executor import Executor
from taiyi.scenarios import DEFAULT_SCENARIOS_DIR, ScenarioMatcher, ScenarioRegistry
from taiyi.scheduler import SchedulerEngine
from taiyi.skills import DEFAULT_SKILLS_DIR, SkillRegistry
from taiyi.validation import ValidationEngine
from taiyi.value_stream import ValueStreamEngine


class Gateway:
    def __init__(
        self,
        *,
        runtime: TaskRuntime,
        scenario_matcher: ScenarioMatcher,
        skills: SkillRegistry,
        memory: MemoryEngine,
        observability: Observability | None = None,
        iteration: IterationEngine | None = None,
        committee: ExpertCommittee | None = None,
        approvals: ApprovalStore | None = None,
        base_dir: str | None = None,
    ):
        self.runtime = runtime
        self.matcher = scenario_matcher
        self.skills = skills
        self.memory = memory
        self.obs = observability
        self.iteration = iteration
        self.committee = committee
        self.approvals = approvals
        # base_dir is where OODA-approved rules/skills land (rules/auto, skills/auto);
        # kept here so the review endpoints can resolve suggestions without re-deriving it.
        self.base_dir = base_dir

    def submit(
        self,
        prompt: str,
        *,
        scenario: str | None = None,
        user_id: str = "u1",
        session_id: str = "s1",
    ) -> TaskContext:
        scenario = scenario or self.matcher.match(prompt)
        return self.runtime.run(prompt, scenario, user_id=user_id, session_id=session_id)

    def resume(self, approval_id: str, *, approve: bool) -> TaskContext:
        """Resume a task suspended for human review.

        Delegates to the runtime (TaskRuntime or AgentRuntime both expose
        ``resume``). The gateway itself never executes and never grants
        clearance — it only forwards the human's decision.
        """
        return self.runtime.resume(approval_id, approve=approve)

    def resolve_review(self, suggestion_id: int, *, approve: bool):
        """Resolve an OODA suggestion: approve lands it in the auto dirs, reject drops it.

        This is the Act gate of the outer loop, exposed for human review. Approve
        writes the rule/skill YAML under base_dir's rules/auto (skills/auto), which
        governance loads read-only on the next start — the live set is never
        mutated at runtime. Returns the written path (approve) or None (reject).
        """
        if self.iteration is None:
            raise RuntimeError("iteration engine not configured")
        if not approve:
            self.iteration.reject(suggestion_id)
            return None
        if not self.base_dir:
            raise RuntimeError("no base_dir configured — cannot persist approved suggestions")
        from pathlib import Path
        base = Path(self.base_dir)
        return self.iteration.approve(
            suggestion_id, rules_dir=base / "rules", skills_dir=base / "skills"
        )


def build_gateway(
    base_dir: str | Path | None = None,
    *,
    executor: Executor | None = None,
    provider: LLMProvider | None = None,
    mode: str = "agent",
    validator: ValidationEngine | None = None,
    max_rounds: int = 1,
    extra_rules_dirs: tuple[str, ...] = (),
    extra_scenarios_dirs: tuple[str, ...] = (),
    extra_skills_dirs: tuple[str, ...] = (),
) -> Gateway:
    base = Path(base_dir) if base_dir else None
    audit = AuditLog(base / "audit.jsonl") if base else AuditLog()

    # OODA outer loop: trajectories + the human-review queue persist under base/.
    # Approved suggestions land in base/rules/auto and base/skills/auto, which we
    # add to the load dirs so a restart picks them up (read-only, as the loader
    # requires) — that is the Act step of the loop, made real.
    auto_rules_dir = str(base / "rules" / "auto") if base else None
    auto_skills_dir = str(base / "skills" / "auto") if base else None
    rules_dirs = list(extra_rules_dirs) + ([auto_rules_dir] if auto_rules_dir else [])
    skills_dirs = list(extra_skills_dirs) + ([auto_skills_dir] if auto_skills_dir else [])

    governance = GovernanceEngine(audit_log=audit, extra_rules_dirs=rules_dirs or None)
    scheduler = SchedulerEngine(LocalPermitClient(governance))
    memory = MemoryEngine(base)
    observability = Observability()
    iteration = IterationEngine(base)
    approvals = ApprovalStore()
    # validator=None means "no check phase" (useful for tests / pure permit-path
    # runs). A falsy-but-not-None default only kicks in when caller omits it.
    val = ValidationEngine() if validator is None else validator
    # Re-read the caller's intent: build_gateway's signature default is None, and
    # that means "give me the standard validator". To request NO validator, pass
    # validator=False explicitly. (None == default-validator; False == none.)
    if validator is False:
        val = None

    # The runtime shape is decided here: an agent runtime (ReAct, the default
    # "highest decision-maker") needs a provider; without one it cannot reason.
    # A workflow runtime (plan-once) needs no provider. When mode=agent but no
    # provider is supplied, we fall back to workflow so the system still runs
    # offline rather than failing to start.
    use_agent = mode == "agent" and provider is not None
    if use_agent:
        runtime = AgentRuntime(
            scheduler,
            audit_log=audit,
            provider=provider,
            executor=executor,
            validator=val,
            memory=memory,
            value_stream=ValueStreamEngine(),
            observability=observability,
            iteration=iteration,
            approvals=approvals,
        )
    else:
        runtime = TaskRuntime(
            scheduler,
            audit_log=audit,
            executor=executor,
            validator=val,
            memory=memory,
            value_stream=ValueStreamEngine(),
            observability=observability,
            iteration=iteration,
            approvals=approvals,
            max_rounds=max_rounds,
        )

    if extra_scenarios_dirs:
        scenarios = ScenarioRegistry.load_dirs([DEFAULT_SCENARIOS_DIR, *extra_scenarios_dirs])
    else:
        scenarios = ScenarioRegistry.load_dir()
    if extra_skills_dirs:
        skills = SkillRegistry.load_dirs([DEFAULT_SKILLS_DIR, *extra_skills_dirs])
    else:
        skills = SkillRegistry.load_dir()
    skills.index_into(memory)

    return Gateway(
        runtime=runtime,
        scenario_matcher=ScenarioMatcher(scenarios),
        skills=skills,
        memory=memory,
        observability=observability,
        iteration=iteration,
        committee=ExpertCommittee(),
        approvals=approvals,
        base_dir=str(base) if base else None,
    )


def build_gateway_from_config(config) -> Gateway:
    """Build a Gateway from a TaiyiConfig — the self-operated entry point."""
    executor = None
    if config.executor == "sandbox":
        from taiyi.tools import SandboxExecutor

        sandbox = config.sandbox_dir or (str(Path(config.base_dir or ".") / "sandbox"))
        executor = SandboxExecutor(sandbox)
    provider = make_provider(config)  # None when offline
    return build_gateway(
        base_dir=config.base_dir,
        executor=executor,
        provider=provider,
        mode=config.mode,
        max_rounds=config.max_rounds,
        extra_rules_dirs=tuple(config.rules_dirs),
        extra_scenarios_dirs=tuple(config.scenarios_dirs),
        extra_skills_dirs=tuple(config.skills_dirs),
    )
