"""
Minimal background job runner.

A job runs a Python callable in a thread and accumulates a log the frontend
polls. The callable receives the Job and can stream subprocess output into it
with job.run_cmd().
"""

import subprocess
import threading
import time
import itertools

MAX_LOG_LINES = 4000

_counter = itertools.count(1)
_jobs = {}
_lock = threading.Lock()


class JobError(Exception):
    pass


class Job:
    def __init__(self, job_id: int, name: str):
        self.id = job_id
        self.name = name
        self.status = "running"   # running | done | error
        self.log = []
        self.error = None
        self.started = time.time()
        self.ended = None

    def log_line(self, text: str):
        for line in str(text).splitlines() or [""]:
            self.log.append(line)
        if len(self.log) > MAX_LOG_LINES:
            del self.log[: len(self.log) - MAX_LOG_LINES]

    def run_cmd(self, argv, cwd=None):
        """Run a subprocess, streaming combined output into the job log."""
        self.log_line("$ " + " ".join(str(a) for a in argv))
        proc = subprocess.Popen(
            [str(a) for a in argv],
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\n")
            if line.strip():
                self.log_line(line)
        proc.wait()
        if proc.returncode != 0:
            raise JobError(f"command failed (exit {proc.returncode}): {argv[0]}")

    def to_dict(self, tail: int = 0):
        log = self.log[-tail:] if tail else self.log
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "log": log,
            "logLength": len(self.log),
            "error": self.error,
            "started": self.started,
            "ended": self.ended,
        }


def start(name: str, fn) -> Job:
    with _lock:
        job = Job(next(_counter), name)
        _jobs[job.id] = job

    def runner():
        try:
            fn(job)
            job.status = "done"
        except Exception as exc:  # surfaced to the UI, not swallowed
            job.status = "error"
            job.error = str(exc)
            job.log_line(f"ERROR: {exc}")
        finally:
            job.ended = time.time()

    threading.Thread(target=runner, daemon=True).start()
    return job


def get(job_id: int):
    return _jobs.get(job_id)


def all_jobs():
    return sorted(_jobs.values(), key=lambda j: j.id)


def any_running() -> bool:
    return any(j.status == "running" for j in _jobs.values())
