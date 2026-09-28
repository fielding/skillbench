import subprocess

import pytest

from skillbench.secrets import SecretError, describe, resolve


class FakeRunner:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.calls = []
        self.result = subprocess.CompletedProcess([], returncode, stdout, stderr)

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        return self.result


def test_op_reference_uses_op_read_with_account():
    runner = FakeRunner(stdout="sk-venice-123\n")
    assert (
        resolve(
            "op://Vault/Provider/credential", op_account="example.1password.com", runner=runner
        )
        == "sk-venice-123"
    )
    assert runner.calls == [
        ["op", "read", "op://Vault/Provider/credential", "--account", "example.1password.com"]
    ]


def test_keychain_reference():
    runner = FakeRunner(stdout="kc-secret\n")
    assert resolve("keychain:PROVIDER_API_KEY", runner=runner) == "kc-secret"
    assert runner.calls[0][:3] == ["security", "find-generic-password", "-s"]


def test_env_reference(monkeypatch):
    monkeypatch.setenv("PROVIDER_API_KEY", "from-env")
    assert resolve("env:PROVIDER_API_KEY") == "from-env"
    monkeypatch.delenv("PROVIDER_API_KEY")
    with pytest.raises(SecretError):
        resolve("env:PROVIDER_API_KEY")


def test_literal_and_failures():
    assert resolve("literal-key") == "literal-key"
    failing = FakeRunner(returncode=1, stderr="[ERROR] authorization timeout")
    with pytest.raises(SecretError, match="authorization timeout"):
        resolve("op://v/i/f", runner=failing)
    empty = FakeRunner(stdout="\n")
    with pytest.raises(SecretError, match="empty"):
        resolve("keychain:X", runner=empty)


def test_describe_hides_literals():
    assert describe("op://Private/X/credential") == "op://Private/X/credential"
    assert describe("sk-live-abc") == "<literal>"
