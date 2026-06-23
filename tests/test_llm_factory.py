"""Tests for the LLM provider seam (M4 live wiring point).

The seam's contract: offline → None (gateway falls back to keyword planner);
any live provider name → a provider object whose complete() raises
NotImplementedError (it must refuse to fabricate output, never fake success).
"""
from __future__ import annotations

import pytest

from taiyi.config import TaiyiConfig
from taiyi.llm import make_provider
from taiyi.llm.base import DEFAULT_LIVE_MODEL, LLMMessage


def _cfg(**kw) -> TaiyiConfig:
    base = TaiyiConfig()
    return base.__class__(**{**base.__dict__, **kw})


def test_offline_returns_none():
    cfg = _cfg(provider="offline")
    assert make_provider(cfg) is None


def test_default_config_is_offline():
    # A fresh config with no explicit provider must be offline.
    assert make_provider(TaiyiConfig()) is None


@pytest.mark.parametrize("name", ["anthropic", "openai_compat", "ollama"])
def test_live_slot_raises_not_implemented(name):
    cfg = _cfg(provider=name, api_key_env="DUMMY_KEY", model=None)
    prov = make_provider(cfg)
    assert prov is not None
    assert prov.name == f"live:{name}"
    # The slot must refuse to fabricate a response — faking success would break
    # the governance invariant (a no-op model still driving fake tool calls).
    with pytest.raises(NotImplementedError):
        prov.complete([LLMMessage("user", "hi")])


def test_live_slot_uses_default_model_when_none():
    cfg = _cfg(provider="anthropic", model=None)
    prov = make_provider(cfg)
    assert prov is not None
    # The slot stores the default model name; complete() still raises, but the
    # wiring point is proven to resolve a None model to the documented default.
    assert prov._model == DEFAULT_LIVE_MODEL


def test_live_slot_respects_explicit_model():
    cfg = _cfg(provider="ollama", model="llama3:8b")
    prov = make_provider(cfg)
    assert prov is not None
    assert prov._model == "llama3:8b"
