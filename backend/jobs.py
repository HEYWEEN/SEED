from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from time import monotonic


class JobError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class Jobs:
    """Short-lived local jobs. One expensive request of each kind can run at a time."""

    def __init__(self):
        self._lock = Lock()
        self._jobs = {}
        self._executors = {
            kind: ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"seed-{kind}")
            for kind in ("analysis", "summary")
        }

    def _prune(self):
        now = monotonic()
        for job_id, job in list(self._jobs.items()):
            if job.get("expiry", now + 1) <= now:
                del self._jobs[job_id]

    def submit(self, kind, thread, job_id, run):
        with self._lock:
            self._prune()
            existing = self._jobs.get(job_id)
            if existing is not None and existing["status"] != "failed":
                return job_id
            if len(self._jobs) >= 48:
                oldest = next((key for key, job in self._jobs.items() if "expiry" in job), None)
                if oldest is not None:
                    del self._jobs[oldest]
            if sum(
                job["kind"] == kind and "expiry" not in job for job in self._jobs.values()
            ) >= 3:
                raise JobError("busy")
            self._jobs[job_id] = {"kind": kind, "thread": thread, "status": "queued"}
            self._executors[kind].submit(self._run, job_id, run)
        return job_id

    def _run(self, job_id, run):
        def report(stage):
            with self._lock:
                self._jobs[job_id]["stage"] = stage

        with self._lock:
            self._jobs[job_id]["status"] = "running"

        try:
            result = run(report)
            outcome = {"status": "completed", "result": result}
        except JobError as error:
            outcome = {"status": "failed", "code": error.code}
        except Exception:
            outcome = {"status": "failed", "code": "backend_error"}

        with self._lock:
            self._jobs[job_id].update(outcome, expiry=monotonic() + 30 * 60)

    def get(self, job_id, kind, thread):
        with self._lock:
            self._prune()
            job = self._jobs.get(job_id)
            if job is None or job["kind"] != kind or job["thread"] != thread:
                return None
            return {key: value for key, value in job.items() if key not in ("kind", "thread", "expiry")}

    def close(self):
        for executor in self._executors.values():
            executor.shutdown(wait=False, cancel_futures=True)
