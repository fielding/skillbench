"""Load ``skillbench.toml``: skill roots, the model set, run defaults, per-skill grants."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_FILENAME = "skillbench.toml"
ABLATIONS = ("none", "with-without")

# Tools `claude plugin eval` strips from the session unless the operator grants them.
GATED_TOOLS = ("Write", "Edit", "Bash", "WebFetch", "WebSearch", "NotebookEdit")


@dataclass(frozen=True)
class SkillOverrides:
    allow_tools: tuple[str, ...] = ()
    scaffold: bool = False
    runs: int | None = None
    ablation: str | None = None
    max_cost_usd: float | None = None
    skip: bool = False


@dataclass(frozen=True)
class Provider:
    """An OpenAI-compatible upstream reached through the run-scoped proxy (see proxy.py)."""

    name: str
    base_url: str
    api_key: str  # secret reference, see secrets.py
    judge_model: str | None = None
    builtin: bool = (
        False  # known without configuration; a [providers.<name>] block overrides fields
    )


# Providers skillbench knows without any configuration. A `[providers.<name>]` block with the
# same name overrides any field. An `env:` key means a colleague only has to export the
# variable to run `<name>/<model>` ids.
BUILTIN_PROVIDERS: dict[str, dict[str, str]] = {
    "venice": {
        "base_url": "https://api.venice.ai/api/v1",
        "api_key": "env:VENICE_API_KEY",
        # Venice serves Claude, so the judge can stay on the provider with no second key.
        "judge_model": "venice/claude-sonnet-4-5",
    },
}


@dataclass(frozen=True)
class ProxySettings:
    binary: str = "cliproxyapi"
    judge_api_key: str | None = (
        None  # secret reference for an Anthropic key; keeps the judge pinned
    )
    op_account: str | None = None


@dataclass(frozen=True)
class Config:
    root: Path
    roots: tuple[Path, ...] = ()
    models: tuple[str, ...] = ()
    judge_model: str = "claude-haiku-4-5"
    runs: int = 3
    ablation: str = "with-without"
    max_cost_usd: float | None = 10.0
    concurrency: int = 2
    unset_env: tuple[str, ...] = ("ANTHROPIC_API_KEY",)
    claude_bin: str = "claude"
    skills: dict[str, SkillOverrides] = field(default_factory=dict)
    providers: dict[str, Provider] = field(default_factory=dict)
    proxy: ProxySettings = field(default_factory=ProxySettings)

    def judge_for(self, model: str) -> str | None:
        """The judge a run of ``model`` uses today: the provider's own judge for a proxied
        model (unless a Claude judge is routed with ``[proxy] judge_api_key``), else the
        configured judge. Staleness compares stored runs against this."""
        prefix = model.split("/", 1)[0] if "/" in model else None
        provider = self.providers.get(prefix) if prefix else None
        if provider is None:
            return self.judge_model
        if provider.judge_model:
            return provider.judge_model
        return self.judge_model if self.proxy.judge_api_key else None

    @property
    def results_dir(self) -> Path:
        return self.root / "results"

    @property
    def suites_dir(self) -> Path:
        return self.root / "suites"

    @property
    def build_dir(self) -> Path:
        return self.root / "build"

    def overrides(self, skill: str) -> SkillOverrides:
        return self.skills.get(skill, SkillOverrides())


class ConfigError(ValueError):
    pass


def find_config(start: Path) -> Path | None:
    for directory in (start, *start.parents):
        candidate = directory / CONFIG_FILENAME
        if candidate.is_file():
            return candidate
    return None


def _path(value: str, root: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else root / path


def config_from_dict(data: dict[str, Any], root: Path) -> Config:
    defaults = data.get("defaults", {})
    ablation = defaults.get("ablation", "with-without")
    if ablation not in ABLATIONS:
        raise ConfigError(f"defaults.ablation must be one of {ABLATIONS}, got {ablation!r}")
    concurrency = int(defaults.get("concurrency", 2))
    if not 1 <= concurrency <= 8:
        raise ConfigError("defaults.concurrency must be between 1 and 8")

    skills: dict[str, SkillOverrides] = {}
    for name, raw in data.get("skills", {}).items():
        skill_ablation = raw.get("ablation")
        if skill_ablation is not None and skill_ablation not in ABLATIONS:
            raise ConfigError(f"skills.{name}.ablation must be one of {ABLATIONS}")
        skills[name] = SkillOverrides(
            allow_tools=tuple(raw.get("allow_tools", ())),
            scaffold=bool(raw.get("scaffold", False)),
            runs=raw.get("runs"),
            ablation=skill_ablation,
            max_cost_usd=raw.get("max_cost_usd"),
            skip=bool(raw.get("skip", False)),
        )

    specs = {name: dict(spec) for name, spec in BUILTIN_PROVIDERS.items()}
    for name, raw in data.get("providers", {}).items():
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise ConfigError(
                f"provider name {name!r} must be alphanumeric (it prefixes model ids)"
            )
        merged = {**specs.get(name, {}), **raw}
        if not merged.get("base_url") or not merged.get("api_key"):
            raise ConfigError(f"providers.{name} needs base_url and api_key")
        specs[name] = merged
    providers = {
        name: Provider(
            name=name,
            base_url=str(spec["base_url"]).rstrip("/"),
            api_key=str(spec["api_key"]),
            judge_model=spec.get("judge_model"),
            builtin=name in BUILTIN_PROVIDERS and name not in data.get("providers", {}),
        )
        for name, spec in specs.items()
    }
    raw_proxy = data.get("proxy", {})
    proxy = ProxySettings(
        binary=raw_proxy.get("binary", "cliproxyapi"),
        judge_api_key=raw_proxy.get("judge_api_key"),
        op_account=raw_proxy.get("op_account"),
    )

    max_cost = defaults.get("max_cost_usd", 10.0)
    return Config(
        root=root,
        roots=tuple(_path(p, root) for p in data.get("roots", ())),
        models=tuple(data.get("models", ())),
        judge_model=defaults.get("judge_model", "claude-haiku-4-5"),
        runs=int(defaults.get("runs", 3)),
        ablation=ablation,
        max_cost_usd=None if max_cost is None else float(max_cost),
        concurrency=concurrency,
        unset_env=tuple(defaults.get("unset_env", ("ANTHROPIC_API_KEY",))),
        claude_bin=defaults.get("claude_bin", "claude"),
        skills=skills,
        providers=providers,
        proxy=proxy,
    )


def load_config(path: Path | None = None, *, start: Path | None = None) -> Config:
    """Load the nearest config; with none found, return an empty config rooted at ``start``."""
    start = (start or Path.cwd()).resolve()
    if path is None:
        path = find_config(start)
    if path is None:
        return config_from_dict({}, root=start)
    path = path.resolve()
    with path.open("rb") as handle:
        try:
            data = tomllib.load(handle)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{path}: {exc}") from exc
    return config_from_dict(data, root=path.parent)
