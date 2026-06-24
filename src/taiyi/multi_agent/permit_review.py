"""Second-opinion review: the expert committee as a second gate on permit.

This is NOT a replacement for governance — governance owns red-line verdicts. The
committee only ever *tightens*: a governance ALLOW can be escalated to
NEEDS_REVIEW by a committee veto, but a committee APPROVE can never loosen a
governance DENY. That one-way rule preserves the governance/scheduling separation
invariant: the expert matrix has no authority to grant clearance governance denied.

Why NEEDS_REVIEW (not DENY) for a committee veto: governance is the sole authority
that can hard-deny. A committee veto means "an expert flagged this — a human should
look", which is exactly NEEDS_REVIEW. This keeps the two gates from competing over
who gets to issue a DENY.
"""
from __future__ import annotations

from taiyi.core.types import PermitResponse, Verdict
from taiyi.multi_agent.arbitration import ArbitrationResult, Decision


def reconsider_permit(
    permit: PermitResponse,
    arb: ArbitrationResult,
    *,
    approval_id: str | None = None,
) -> PermitResponse:
    """Apply the committee's verdict to a governance ALLOW, one-way tightening.

    Only meaningful when ``permit`` is ALLOW — a DENY or NEEDS_REVIEW is already
    at least as strict as anything the committee could add, so it is returned
    unchanged (the committee never loosens). On a committee APPROVE the ALLOW
    stands. On a committee VETO/NEEDS_HUMAN the ALLOW is escalated to
    NEEDS_REVIEW so a human decides — the committee does not DENY.
    """
    if not permit.allowed:
        return permit  # governance already gated it; committee has no loosening power

    if arb.decision is Decision.APPROVED:
        # Committee concurs. Attach any non-binding advisories for the audit trail.
        if arb.advisories:
            return PermitResponse(
                verdict=permit.verdict,
                reason=permit.reason,
                evidence=permit.evidence,
                matched_rule_id=permit.matched_rule_id,
                precedence=permit.precedence,
                advisories=[*permit.advisories, *arb.advisories],
                approval_id=permit.approval_id,
            )
        return permit

    # Committee vetoed or flagged a hard conflict → escalate to human review.
    # The committee never issues a DENY; governance owns that authority.
    winning = arb.winning
    who = winning.domain if winning else "committee"
    reason = f"expert committee ({who}) flagged this step: {arb.notes}"
    return PermitResponse(
        verdict=Verdict.NEEDS_REVIEW,
        reason=reason,
        evidence=permit.evidence,
        matched_rule_id=permit.matched_rule_id,
        precedence=permit.precedence,
        advisories=[*permit.advisories, *arb.advisories],
        approval_id=approval_id,
    )


__all__ = ["reconsider_permit"]
