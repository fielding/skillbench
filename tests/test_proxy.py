from pathlib import Path

import pytest

from skillbench.config import Config, ConfigError, Provider, config_from_dict
from skillbench.proxy import free_port, group_by_provider, render_config, split_model


def cfg(tmp_path: Path) -> Config:
    return config_from_dict(
        {
            "models": ["claude-opus-5", "venice/kimi-k3", "venice/qwen-3-8-max"],
            "providers": {
                "venice": {
                    "base_url": "https://api.venice.ai/api/v1/",
                    "api_key": "op://Vault/Provider/credential",
                    "judge_model": "claude-haiku-4-5",
                }
            },
            "proxy": {
                "judge_api_key": "op://Vault/Anthropic/credential",
                "op_account": "example.1password.com",
            },
        },
        tmp_path,
    )


def test_provider_config_parses(tmp_path: Path):
    c = cfg(tmp_path)
    venice = c.providers["venice"]
    assert venice.base_url == "https://api.venice.ai/api/v1"  # trailing slash stripped
    assert venice.judge_model == "claude-haiku-4-5"
    assert c.proxy.judge_api_key.startswith("op://")
    assert c.proxy.op_account == "example.1password.com"
    assert c.proxy.binary == "cliproxyapi"


def test_provider_config_validation(tmp_path: Path):
    with pytest.raises(ConfigError):
        config_from_dict({"providers": {"bad/name": {"base_url": "x", "api_key": "y"}}}, tmp_path)
    with pytest.raises(ConfigError):
        config_from_dict({"providers": {"other": {"base_url": "x"}}}, tmp_path)
    # A built-in provider only needs the fields you want to change.
    partial = config_from_dict({"providers": {"venice": {"api_key": "env:MY_KEY"}}}, tmp_path)
    assert partial.providers["venice"].api_key == "env:MY_KEY"
    assert partial.providers["venice"].base_url == "https://api.venice.ai/api/v1"


def test_split_and_group(tmp_path: Path):
    c = cfg(tmp_path)
    assert split_model("venice/kimi-k3", c) == ("venice", "kimi-k3")
    assert split_model("claude-opus-5", c) == (None, "claude-opus-5")
    # An unknown prefix is just a model id with a slash in it.
    assert split_model("other/thing", c) == (None, "other/thing")
    groups = group_by_provider(
        ["claude-opus-5", "venice/kimi-k3", "claude-sonnet-5", "venice/qwen-3-8-max"], c
    )
    assert groups == {
        None: ["claude-opus-5", "claude-sonnet-5"],
        "venice": ["venice/kimi-k3", "venice/qwen-3-8-max"],
    }


def test_render_config_shape(tmp_path: Path):
    provider = Provider(name="venice", base_url="https://api.venice.ai/api/v1", api_key="op://x")
    text = render_config(
        port=8318,
        token="tok",
        auth_dir=tmp_path / "auth",
        providers=[(provider, ["kimi-k3", "qwen-3-8-max"], "sk-venice")],
        judge_api_key="sk-ant",
    )
    assert 'host: "127.0.0.1"' in text and "port: 8318" in text
    assert '  - "tok"' in text
    assert 'secret-key: ""' in text and "disable-control-panel: true" in text
    assert 'claude-api-key:\n  - api-key: "sk-ant"' in text
    assert 'prefix: "venice"' in text and 'base-url: "https://api.venice.ai/api/v1"' in text
    assert '- api-key: "sk-venice"' in text
    assert '- name: "kimi-k3"\n        alias: "kimi-k3"' in text
    without_judge = render_config(
        port=1, token="t", auth_dir=tmp_path, providers=[], judge_api_key=None
    )
    assert "claude-api-key" not in without_judge and "openai-compatibility" not in without_judge


def test_free_port_is_bindable():
    import socket

    port = free_port()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", port))


def test_env_points_background_models_at_the_model_under_test(tmp_path: Path):
    from skillbench.proxy import ProxyHandle

    class Dead:
        def poll(self):
            return 0

    handle = ProxyHandle(
        port=1234, token="tok", config_path=tmp_path / "c", log_path=tmp_path / "l", process=Dead()
    )
    assert handle.env() == {
        "ANTHROPIC_BASE_URL": "http://127.0.0.1:1234",
        "ANTHROPIC_AUTH_TOKEN": "tok",
    }
    env = handle.env("venice/kimi-k3")
    assert env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "venice/kimi-k3"
    assert env["ANTHROPIC_SMALL_FAST_MODEL"] == "venice/kimi-k3"


def test_render_config_debug_flag(tmp_path: Path):
    text = render_config(
        port=1, token="t", auth_dir=tmp_path, providers=[], judge_api_key=None, debug=True
    )
    assert "debug: true" in text
