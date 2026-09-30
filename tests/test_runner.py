from pathlib import Path

from skillbench.runner import RunSpec, build_argv, child_env


def spec(**kw) -> RunSpec:
    base = dict(
        skill="tutor",
        model="claude-opus-5",
        judge_model="claude-haiku-4-5",
        runs=3,
        ablation="with-without",
        concurrency=2,
        max_cost_usd=10.0,
    )
    base.update(kw)
    return RunSpec(**base)


def test_argv_shape():
    argv = build_argv(spec(), Path("build/tutor"), Path("r/result.json"), Path("r"), "claude")
    assert argv[:4] == ["claude", "plugin", "eval", "build/tutor"]
    assert argv[argv.index("--model") + 1] == "claude-opus-5"
    assert argv[argv.index("--judge-model") + 1] == "claude-haiku-4-5"
    assert argv[argv.index("--threshold") + 1] == "0"
    assert "--no-publish" in argv and "--trust-plugin" in argv
    assert argv[argv.index("--max-cost-usd") + 1] == "10"
    assert "--scaffold" not in argv and "--allow-tools" not in argv


def test_allow_tools_is_last_and_optional_flags():
    argv = build_argv(
        spec(
            allow_tools=("Write", "Edit"),
            scaffold=True,
            case_glob="auth*",
            tags=("smoke",),
            max_cost_usd=None,
        ),
        Path("p"),
        Path("j"),
        Path("o"),
    )
    assert argv[-3:] == ["--allow-tools", "Write", "Edit"]
    assert "--scaffold" in argv
    assert argv[argv.index("--case") + 1] == "auth*"
    assert argv[argv.index("--tag") + 1] == "smoke"
    assert "--max-cost-usd" not in argv


def test_child_env_unsets(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("KEEP_ME", "1")
    env = child_env(["ANTHROPIC_API_KEY", "NOT_SET"])
    assert "ANTHROPIC_API_KEY" not in env
    assert env["KEEP_ME"] == "1"


def test_cost_ceiling_precedence_and_proxied_runs():
    from skillbench.cli import _max_cost

    assert _max_cost(None, None, 10.0) == 10.0
    assert _max_cost(None, 4.0, 10.0) == 4.0
    assert _max_cost(2.5, 4.0, 10.0) == 2.5
    assert _max_cost(0, 4.0, 10.0) is None  # zero on the CLI disables it
    # Proxied runs are unpriced, so no ceiling unless the CLI asks for one.
    assert _max_cost(None, 4.0, 10.0, proxied=True) is None
    assert _max_cost(3.0, 4.0, 10.0, proxied=True) == 3.0
