"""Local live view: serve the dashboard, expose results as JSON, launch runs.

Binds to localhost only. A launched run is a `skillbench run` child process whose
stdout is captured to ``build/jobs/<id>.log`` so the page can tail it. One run at a
time: every eval run is a stack of `claude -p` sessions on the same credential, so
two in parallel just split one rate limit.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import uuid
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import Config
from .dashboard import build_payload, render
from .discover import discover

MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$")
ArgvFactory = Callable[[str, str, int], list[str]]


def default_argv(config: Config) -> ArgvFactory:
    def factory(model: str, skill: str, runs: int) -> list[str]:
        argv = [sys.executable, "-m", "skillbench"]
        config_file = config.root / "skillbench.toml"
        if config_file.is_file():
            # --config is a global option: it must precede the subcommand.
            argv += ["--config", str(config_file)]
        return argv + ["run", "-m", model, "-s", skill, "--runs", str(runs)]

    return factory


@dataclass
class Job:
    id: str
    model: str
    skill: str
    runs: int
    started_at: str
    log_path: str
    returncode: int | None = None
    proc: subprocess.Popen | None = field(default=None, repr=False, compare=False)

    def poll(self) -> None:
        if self.proc is not None and self.returncode is None:
            self.returncode = self.proc.poll()

    @property
    def done(self) -> bool:
        self.poll()
        return self.returncode is not None

    def public(self) -> dict:
        self.poll()
        return {
            "id": self.id,
            "model": self.model,
            "skill": self.skill,
            "runs": self.runs,
            "started_at": self.started_at,
            "log_path": self.log_path,
            "returncode": self.returncode,
            "done": self.returncode is not None,
        }


class JobManager:
    def __init__(self, jobs_dir: Path, argv_for: ArgvFactory, cwd: Path) -> None:
        self.jobs_dir = jobs_dir
        self.argv_for = argv_for
        self.cwd = cwd
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()

    def running(self) -> Job | None:
        return next((j for j in self.jobs.values() if not j.done), None)

    def start(self, model: str, skill: str, runs: int) -> Job:
        with self.lock:
            if (active := self.running()) is not None:
                raise RuntimeError(
                    f"job {active.id} ({active.skill} @ {active.model}) is still running"
                )
            self.jobs_dir.mkdir(parents=True, exist_ok=True)
            job_id = uuid.uuid4().hex[:8]
            log_path = self.jobs_dir / f"{job_id}.log"
            log = log_path.open("w")
            proc = subprocess.Popen(
                self.argv_for(model, skill, runs),
                cwd=self.cwd,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            job = Job(
                id=job_id,
                model=model,
                skill=skill,
                runs=runs,
                started_at=datetime.now(UTC).isoformat(timespec="seconds"),
                log_path=str(log_path),
                proc=proc,
            )
            self.jobs[job_id] = job
            return job

    def log_chunk(self, job: Job, offset: int) -> tuple[str, int]:
        path = Path(job.log_path)
        if not path.is_file():
            return "", offset
        data = path.read_bytes()
        offset = max(0, min(offset, len(data)))
        return data[offset:].decode("utf-8", errors="replace"), len(data)


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], config: Config, jobs: JobManager) -> None:
        super().__init__(address, Handler)
        self.config = config
        self.jobs = jobs
        self.skills = {s.name for s in discover(config)}


class Handler(BaseHTTPRequestHandler):
    server: DashboardServer

    def log_message(self, fmt: str, *args: object) -> None:
        # Job-log polling is chatty; everything else is worth a line.
        if "/api/jobs/" not in str(args[0] if args else ""):
            sys.stderr.write(f"{self.address_string()} - {fmt % args}\n")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: object) -> None:
        self._send(status, json.dumps(payload).encode(), "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802 (http.server naming)
        url = urlparse(self.path)
        parts = [p for p in url.path.split("/") if p]
        if not parts:
            html = render(build_payload(self.server.config), live=True)
            return self._send(200, html.encode(), "text/html; charset=utf-8")
        if parts == ["api", "data"]:
            return self._json(200, build_payload(self.server.config))
        if parts == ["api", "jobs"]:
            jobs = [j.public() for j in self.server.jobs.jobs.values()]
            return self._json(200, {"jobs": jobs, "skills": sorted(self.server.skills)})
        if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "log":
            job = self.server.jobs.jobs.get(parts[2])
            if job is None:
                return self._json(404, {"error": "no such job"})
            offset = int(parse_qs(url.query).get("offset", ["0"])[0] or 0)
            chunk, new_offset = self.server.jobs.log_chunk(job, offset)
            return self._json(
                200,
                {
                    "chunk": chunk,
                    "offset": new_offset,
                    "done": job.done,
                    "returncode": job.returncode,
                },
            )
        if parts[0] == "results":
            return self._serve_result_file(parts[1:])
        return self._json(404, {"error": "not found"})

    def _serve_result_file(self, parts: list[str]) -> None:
        # Only the eval command's own report.html is exposed, and only from inside results/.
        if not parts or parts[-1] != "report.html" or any(p in ("..", "") for p in parts):
            return self._json(404, {"error": "not found"})
        path = self.server.config.results_dir.joinpath(*parts)
        if not path.is_file():
            return self._json(404, {"error": "not found"})
        return self._send(200, path.read_bytes(), "text/html; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/api/run":
            return self._json(404, {"error": "not found"})
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": "body must be JSON"})
        model = str(body.get("model", "")).strip()
        skill = str(body.get("skill", "")).strip()
        runs = int(body.get("runs") or 1)
        if not MODEL_RE.match(model):
            return self._json(400, {"error": "model id looks wrong"})
        if skill not in self.server.skills:
            return self._json(400, {"error": f"unknown skill {skill!r}"})
        if not 1 <= runs <= 50:
            return self._json(400, {"error": "runs must be 1..50"})
        try:
            job = self.server.jobs.start(model, skill, runs)
        except RuntimeError as exc:
            return self._json(409, {"error": str(exc)})
        return self._json(201, job.public())


def make_server(
    config: Config,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    argv_for: ArgvFactory | None = None,
) -> DashboardServer:
    jobs = JobManager(config.build_dir / "jobs", argv_for or default_argv(config), cwd=config.root)
    return DashboardServer((host, port), config, jobs)


def serve(config: Config, *, port: int = 8765, open_browser: bool = False) -> int:
    try:
        server = make_server(config, port=port)
    except OSError as exc:
        print(
            f"skillbench serve: cannot bind 127.0.0.1:{port} ({exc.strerror}); try --port",
            file=sys.stderr,
        )
        return 1
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"skillbench serve: {url}  (Ctrl-C to stop)")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
