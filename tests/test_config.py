from pathlib import Path

import pytest

from skillbench.config import ConfigError, config_from_dict, find_config, load_config


def test_defaults_when_no_config(tmp_path: Path):
    cfg = load_config(start=tmp_path)
    assert cfg.root == tmp_path.resolve()
    assert cfg.roots == ()
    assert cfg.judge_model == "claude-haiku-4-5"
    assert cfg.unset_env == ("ANTHROPIC_API_KEY",)


def test_load_and_expand(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "skillbench.toml").write_text(
        """
roots = ["~/skills", "local"]
models = ["claude-opus-5"]

[defaults]
runs = 1
max_cost_usd = 2.5
unset_env = []

[skills.tutor]
allow_tools = ["Write"]
scaffold = true
runs = 5
"""
    )
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert find_config(nested) == tmp_path / "skillbench.toml"
    cfg = load_config(start=nested)
    assert cfg.roots == (tmp_path / "skills", tmp_path / "local")
    assert cfg.models == ("claude-opus-5",)
    assert cfg.runs == 1
    assert cfg.max_cost_usd == 2.5
    assert cfg.unset_env == ()
    tutor = cfg.overrides("tutor")
    assert tutor.allow_tools == ("Write",)
    assert tutor.scaffold is True
    assert tutor.runs == 5
    assert cfg.overrides("other").allow_tools == ()
    assert cfg.results_dir == tmp_path / "results"


def test_invalid_ablation(tmp_path: Path):
    with pytest.raises(ConfigError):
        config_from_dict({"defaults": {"ablation": "sometimes"}}, tmp_path)
    with pytest.raises(ConfigError):
        config_from_dict({"skills": {"x": {"ablation": "nope"}}}, tmp_path)


def test_concurrency_bounds(tmp_path: Path):
    with pytest.raises(ConfigError):
        config_from_dict({"defaults": {"concurrency": 9}}, tmp_path)


def test_venice_is_known_without_config(tmp_path: Path):
    cfg = load_config(start=tmp_path)
    venice = cfg.providers["venice"]
    assert venice.builtin
    assert venice.base_url == "https://api.venice.ai/api/v1"
    assert venice.api_key == "env:VENICE_API_KEY"
    assert venice.judge_model == "venice/claude-sonnet-4-5"


def test_provider_block_overrides_builtin_fields(tmp_path: Path):
    cfg = config_from_dict(
        {"providers": {"venice": {"api_key": "op://Vault/Venice/credential"}}}, root=tmp_path
    )
    venice = cfg.providers["venice"]
    assert not venice.builtin
    assert venice.api_key == "op://Vault/Venice/credential"
    assert venice.base_url == "https://api.venice.ai/api/v1"  # untouched field keeps the default
    with pytest.raises(ConfigError, match="needs base_url and api_key"):
        config_from_dict({"providers": {"other": {"api_key": "env:X"}}}, root=tmp_path)
