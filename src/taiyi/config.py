"""Configuration for a self-operated Taiyi deployment.

One place to declare how an instance runs: persistence, network, auth, the
executor, validation rounds, and any custom rule/scenario/skill directories that
merge with the built-ins. Loaded from a YAML file and/or ``TAIYI_*`` environment
variables (env overrides the file), so the same image runs anywhere.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, fields, replace
from pathlib import Path

import yaml

_LIST_FIELDS = {"auth_tokens", "rules_dirs", "scenarios_dirs", "skills_dirs"}


@dataclass
class TaiyiConfig:
    base_dir: str | None = None          # persistence root (audit/memory/markdown)
    host: str = "127.0.0.1"
    port: int = 8080
    auth_tokens: tuple[str, ...] = ()    # if non-empty, Bearer auth is required
    executor: str = "mock"               # mock | sandbox
    sandbox_dir: str | None = None       # working dir for the sandbox executor
    max_rounds: int = 1                  # PDCA correction rounds
    rules_dirs: tuple[str, ...] = ()     # extra rule dirs, merged with built-ins
    scenarios_dirs: tuple[str, ...] = ()
    skills_dirs: tuple[str, ...] = ()
    log_level: str = "info"
    # --- runtime shape -------------------------------------------------------
    mode: str = "agent"                  # agent (ReAct, default) | workflow (plan-once)
    # --- LLM provider seam (opt-in; offline until a live adapter is wired) ---
    provider: str = "offline"            # offline | anthropic | openai_compat | ollama
    model: str | None = None             # model id; None → provider default
    api_key_env: str | None = None       # name of env var holding the key (never the key itself)


def load_config(path: str | Path | None = None) -> TaiyiConfig:
    data: dict = {}
    if path:
        loaded = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"config {path} must be a mapping")
        data = loaded

    valid = {f.name for f in fields(TaiyiConfig)}
    kwargs = {}
    for k, v in data.items():
        if k not in valid:
            continue
        kwargs[k] = tuple(v) if k in _LIST_FIELDS and v is not None else v

    return _apply_env(TaiyiConfig(**kwargs))


def _apply_env(cfg: TaiyiConfig) -> TaiyiConfig:
    env = os.environ
    over: dict = {}
    if env.get("TAIYI_BASE_DIR"):
        over["base_dir"] = env["TAIYI_BASE_DIR"]
    if env.get("TAIYI_HOST"):
        over["host"] = env["TAIYI_HOST"]
    if env.get("TAIYI_PORT"):
        over["port"] = int(env["TAIYI_PORT"])
    if env.get("TAIYI_EXECUTOR"):
        over["executor"] = env["TAIYI_EXECUTOR"]
    if env.get("TAIYI_SANDBOX_DIR"):
        over["sandbox_dir"] = env["TAIYI_SANDBOX_DIR"]
    if env.get("TAIYI_MAX_ROUNDS"):
        over["max_rounds"] = int(env["TAIYI_MAX_ROUNDS"])
    if env.get("TAIYI_AUTH_TOKENS"):
        over["auth_tokens"] = tuple(t for t in env["TAIYI_AUTH_TOKENS"].split(",") if t)
    if env.get("TAIYI_MODE"):
        over["mode"] = env["TAIYI_MODE"]
    if env.get("TAIYI_PROVIDER"):
        over["provider"] = env["TAIYI_PROVIDER"]
    if env.get("TAIYI_MODEL"):
        over["model"] = env["TAIYI_MODEL"]
    if env.get("TAIYI_API_KEY_ENV"):
        over["api_key_env"] = env["TAIYI_API_KEY_ENV"]
    return replace(cfg, **over)
