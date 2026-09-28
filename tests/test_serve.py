import json
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from skillbench.config import Config
from skillbench.serve import make_server
from skillbench.store import RESULT_FILE, run_dir, write_meta
from tests.conftest import make_skill


@pytest.fixture
def server(tmp_path: Path, two_arm: dict):
    root = tmp_path / "skills"
    make_skill(root, "alpha", cases=("one",))
    cfg = Config(root=tmp_path, roots=(root,), models=("claude-opus-5",))
    d = run_dir(cfg.results_dir, "claude-opus-5", "alpha", "2026-01-01T00-00-00Z")
    d.mkdir(parents=True)
    (d / RESULT_FILE).write_text(json.dumps(two_arm))
    (d / "report.html").write_text("<html>report</html>")
    write_meta(d, {"model": "claude-opus-5"})

    def fake_argv(model: str, skill: str, runs: int) -> list[str]:
        return [sys.executable, "-c", f"print('fake run {skill} @ {model} x{runs}')"]

    srv = make_server(cfg, port=0, argv_for=fake_argv)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    yield base
    srv.shutdown()
    srv.server_close()


def _get(url: str):
    with urllib.request.urlopen(url) as resp:
        return resp.status, resp.headers.get("Content-Type", ""), resp.read()


def _post(url: str, payload: dict):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


def test_pages_and_api(server: str):
    status, ctype, body = _get(server + "/")
    assert status == 200 and ctype.startswith("text/html") and b"const LIVE = true;" in body
    status, _, body = _get(server + "/api/data")
    data = json.loads(body)
    assert status == 200 and data["skills"] == ["alpha"]
    status, ctype, body = _get(
        server + "/results/claude-opus-5/alpha/2026-01-01T00-00-00Z/report.html"
    )
    assert status == 200 and body == b"<html>report</html>"
    with pytest.raises(urllib.error.HTTPError):
        _get(server + "/results/../skillbench.toml")


def test_run_job_lifecycle(server: str):
    assert _post(server + "/api/run", {"model": "bad model", "skill": "alpha"})[0] == 400
    assert _post(server + "/api/run", {"model": "claude-opus-5", "skill": "nope"})[0] == 400
    status, job = _post(
        server + "/api/run", {"model": "claude-opus-5", "skill": "alpha", "runs": 2}
    )
    assert status == 201 and job["skill"] == "alpha"
    deadline = time.time() + 10
    while time.time() < deadline:
        _, _, body = _get(f"{server}/api/jobs/{job['id']}/log?offset=0")
        log = json.loads(body)
        if log["done"]:
            break
        time.sleep(0.1)
    assert log["done"] and log["returncode"] == 0
    assert "fake run alpha @ claude-opus-5 x2" in log["chunk"]
    _, _, body = _get(server + "/api/jobs")
    assert [j["id"] for j in json.loads(body)["jobs"]] == [job["id"]]


def test_default_argv_puts_config_before_subcommand(tmp_path: Path):
    from skillbench.serve import default_argv

    (tmp_path / "skillbench.toml").write_text("models = []\n")
    argv = default_argv(Config(root=tmp_path))("claude-opus-5", "tutor", 2)
    assert argv.index("--config") < argv.index("run")
    assert argv[-6:] == ["run", "-m", "claude-opus-5", "-s", "tutor", "--runs"][:6] or argv[
        -7:-1
    ] == ["run", "-m", "claude-opus-5", "-s", "tutor", "--runs"]
