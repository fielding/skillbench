"""Compose and execute one `claude plugin eval` invocation."""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class RunSpec:
    skill: str
    model: str
    judge_model: str
    runs: int
    ablation: str
    concurrency: int
    max_cost_usd: float | None
    allow_tools: tuple[str, ...] = ()
    scaffold: bool = False
    case_glob: str | None = None
    tags: tuple[str, ...] = ()


def build_argv(
    spec: RunSpec,
    plugin_dir: Path,
    json_path: Path,
    output_dir: Path,
    claude_bin: str = "claude",
) -> list[str]:
    argv = [
        claude_bin,
        "plugin",
        "eval",
        str(plugin_dir),
        "--model",
        spec.model,
        "--judge-model",
        spec.judge_model,
        "--runs",
        str(spec.runs),
        "--ablation",
        spec.ablation,
        "--concurrency",
        str(spec.concurrency),
        # Scores are skillbench's to interpret; with the threshold at zero, a child
        # exit of 1 can only mean a load or launch failure.
        "--threshold",
        "0",
        "--no-publish",
        "--trust-plugin",
        "--json",
        str(json_path),
        "--output-dir",
        str(output_dir),
    ]
    if spec.max_cost_usd is not None:
        argv += ["--max-cost-usd", f"{spec.max_cost_usd:g}"]
    if spec.scaffold:
        argv.append("--scaffold")
    if spec.case_glob:
        argv += ["--case", spec.case_glob]
    for tag in spec.tags:
        argv += ["--tag", tag]
    if spec.allow_tools:
        # Variadic: it swallows every following argument, so it must come last.
        argv += ["--allow-tools", *spec.allow_tools]
    return argv


def child_env(unset: Iterable[str]) -> dict[str, str]:
    env = dict(os.environ)
    for key in unset:
        env.pop(key, None)
    return env


@dataclass
class RunOutcome:
    argv: list[str]
    returncode: int
    started_at: str
    duration_seconds: float
    log_path: Path | None = None
    log_tail: str = field(default="", repr=False)

    @property
    def partial(self) -> bool:
        return self.returncode == 2


def execute(argv: list[str], env: dict[str, str], *, log_path: Path | None = None) -> RunOutcome:
    started = datetime.now(UTC)
    clock = time.monotonic()
    if log_path is not None:
        with log_path.open("w") as log:
            proc = subprocess.run(argv, env=env, stdout=log, stderr=subprocess.STDOUT, check=False)
        tail = "\n".join(log_path.read_text(errors="replace").splitlines()[-20:])
    else:
        proc = subprocess.run(argv, env=env, capture_output=True, text=True, check=False)
        tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-20:])
    return RunOutcome(
        argv=argv,
        returncode=proc.returncode,
        started_at=started.isoformat(timespec="seconds"),
        duration_seconds=round(time.monotonic() - clock, 1),
        log_path=log_path,
        log_tail=tail,
    )
