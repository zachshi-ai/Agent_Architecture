"""Live LLM provider seam — the opt-in wiring point for a real model.

This module is **intentionally a skeleton, not a working client.** It exists so
that the rest of the system (config, gateway, AgentRuntime) can be built against
a stable provider-construction seam today, and a live adapter can be dropped in
later *without* touching any caller.

Design contract (do not violate):

* ``make_provider(cfg)`` returns an ``LLMProvider`` for a live ``provider``, or
  ``None`` when the deployment is offline. ``None`` is the signal to the gateway
  to fall back to the keyword planner + offline providers.
* Each live adapter raises ``NotImplementedError`` until it is genuinely wired —
  it must *never* pretend to work or return a fabricated response. A provider
  that fakes success is worse than one that refuses to start, because it would
  break the governance invariant (a model that appears to think while actually
  doing nothing still drives the permit flow with fake tool calls).
* API keys are read by the adapter from the environment variable named by
  ``cfg.api_key_env`` — the config never holds a key value, only the *name* of
  the env var. This keeps secrets out of config files and git history.

To wire a real provider later: implement the matching ``Provider`` class's
``complete()`` (using ``anthropic``/``openai``/``httpx``/etc.), add the SDK to
``pyproject.toml``'s ``[live]`` optional-dependency group, and remove the
``NotImplementedError``. Nothing else changes.
"""
from __future__ import annotations

import os
from typing import Protocol

from taiyi.llm.base import DEFAULT_LIVE_MODEL, LLMMessage, LLMProvider, LLMResponse


def _key_from_env(cfg) -> str | None:
    """Resolve the API key from the env var named in the config, if any.

    The config stores the *name* of the env var (e.g. ``ANTHROPIC_API_KEY``),
    never the key itself. Returns None when no env var is named or it is unset.
    """
    name = getattr(cfg, "api_key_env", None)
    if not name:
        return None
    return os.environ.get(name)


class _NotWiredProvider(LLMProvider):
    """A provider slot that has been selected but not yet implemented.

    It raises on ``complete()`` so a misconfigured live deployment fails loudly
    at the first model call rather than silently emitting fake responses.
    """

    def __init__(self, provider_name: str, model: str):
        self.name = f"live:{provider_name}"
        self._provider_name = provider_name
        self._model = model

    def complete(
        self, messages: list[LLMMessage], *, tools: list[str] | None = None
    ) -> LLMResponse:
        raise NotImplementedError(
            f"live provider {self._provider_name!r} is not wired yet. "
            f"Set provider=offline, or implement {self._provider_name.title()}Provider.complete() "
            f"(and add its SDK to the [live] optional-dependency group in pyproject.toml)."
        )


def make_provider(cfg) -> LLMProvider | None:
    """Construct the LLM provider selected by ``cfg.provider``.

    Returns ``None`` for ``offline`` (the default) — the gateway then falls back
    to the keyword planner and offline providers, so the whole agent loop still
    runs with zero tokens and zero network.

    For a live provider name, this returns a provider object whose ``complete()``
    raises ``NotImplementedError`` until the adapter is genuinely implemented.
    This is the honest "leave a seam, do not fake it" stance: the wiring point
    exists, but it refuses to fabricate model output.
    """
    provider = (getattr(cfg, "provider", "offline") or "offline").lower()
    if provider == "offline":
        return None

    model = getattr(cfg, "model", None) or DEFAULT_LIVE_MODEL
    # api_key_env is read by the adapter when it is implemented; resolving it
    # here would be premature, but we surface a clear error if a live provider
    # is selected with no key source configured.
    if getattr(cfg, "api_key_env", None) is None:
        # Still construct the slot — the NotImplementedError on complete() will
        # carry the actionable message. We do not hard-fail at construction so
        # that tests can assert the seam shape without setting up keys.
        pass

    return _NotWiredProvider(provider, model)


__all__ = ["make_provider"]
