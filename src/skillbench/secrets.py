"""Resolve secret references at run time without writing them anywhere durable.

Forms:
  op://<vault>/<item>/<field>   1Password CLI (`op read`); may prompt in the 1Password app
  keychain:<service>            macOS login keychain (`security find-generic-password -s ... -w`)
  env:<NAME>                    environment variable
  anything else                 taken literally (discouraged; keep secrets out of tracked files)
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable

Runner = Callable[..., subprocess.CompletedProcess]


class SecretError(RuntimeError):
    pass


def resolve(ref: str, *, op_account: str | None = None, runner: Runner = subprocess.run) -> str:
    ref = ref.strip()
    if ref.startswith("op://"):
        argv = ["op", "read", ref]
        if op_account:
            argv += ["--account", op_account]
        return _run(argv, runner, what=ref)
    if ref.startswith("keychain:"):
        service = ref.split(":", 1)[1]
        argv = ["security", "find-generic-password", "-s", service, "-w"]
        return _run(argv, runner, what=ref)
    if ref.startswith("env:"):
        name = ref.split(":", 1)[1]
        value = os.environ.get(name)
        if not value:
            raise SecretError(f"environment variable {name} is not set")
        return value
    return ref


def _run(argv: list[str], runner: Runner, *, what: str) -> str:
    try:
        proc = runner(argv, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise SecretError(f"{argv[0]} is not installed (needed for {what})") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise SecretError(f"could not resolve {what}: {detail[-1] if detail else 'unknown error'}")
    value = proc.stdout.strip()
    if not value:
        raise SecretError(f"{what} resolved to an empty value")
    return value


def describe(ref: str) -> str:
    """A log-safe description of where a secret comes from."""
    if ref.startswith(("op://", "keychain:", "env:")):
        return ref
    return "<literal>"
