import base64
import hashlib
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, SecretStr
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .cache import ResultCache, discussion_fingerprint, fingerprint, result_identity, write_local_text
from .deepseek import cache_signature as summary_signature
from .deepseek import generate_summary
from .discussion import CacheLookup, Discussion, EvaluationSummary, FullSummary
from .jobs import JobError, Jobs
from .sesame import cache_signature as sesame_signature
from .sesame import run_sesame

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = "/v1/settings/deepseek"
logger = logging.getLogger(__name__)


class DeepSeekKey(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: SecretStr


class SettingsStatus(BaseModel):
    configured: bool


def chrome_extension_id():
    manifest_path = PROJECT_ROOT / "extension" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    public_key = base64.b64decode(manifest["key"], validate=True)
    digest = hashlib.sha256(public_key).hexdigest()[:32]
    return digest.translate(str.maketrans("0123456789abcdef", "abcdefghijklmnop"))


def analysis_cache_id(thread, content):
    return result_identity(thread, "analysis", content, sesame_signature())


def summary_cache_id(thread, mode, content):
    return result_identity(thread, "summary", mode, content, summary_signature())


def evidence_rows(evaluations):
    return [
        {
            "source_id": row["source_id"],
            "sentiment": row["sentiment"],
            "aspect": row["aspect"]["text"] if row["aspect"] else None,
            "opinion": row["opinion"]["text"],
            "sentence": row["sentence"],
        }
        for row in evaluations
    ]


def evaluation_summary_content(analysis_id, rows):
    return fingerprint([analysis_id, rows])


def create_app(state_dir: Path | None = None, *, extract=run_sesame, summarize=generate_summary):
    jobs = Jobs()

    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            jobs.close()

    app = FastAPI(title="SEED", lifespan=lifespan)
    extension_id = chrome_extension_id()
    extension_origin = f"chrome-extension://{extension_id}"
    local = state_dir if state_dir is not None else PROJECT_ROOT / ".local"
    key_path = local / "deepseek-api-key"
    cache = ResultCache(local / "results")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[extension_origin],
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["X-Seed-Client", "X-Seed-Thread", "Content-Type"],
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    def require_extension(request: Request):
        origin = request.headers.get("origin")
        # Chrome can omit Origin on a privileged extension request. Websites
        # cannot send the custom header without a successful CORS preflight.
        if request.headers.get("x-seed-client") != extension_id or origin not in (
            None,
            extension_origin,
        ):
            raise HTTPException(status_code=403, detail="Extension origin required.")

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, _error: RequestValidationError):
        return JSONResponse(status_code=422, content={"detail": "Invalid request."})

    def read_key():
        try:
            return key_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return ""

    @app.get(SETTINGS_PATH, dependencies=[Depends(require_extension)])
    def settings_status() -> SettingsStatus:
        return SettingsStatus(configured=bool(read_key()))

    @app.put(SETTINGS_PATH, dependencies=[Depends(require_extension)])
    def save_settings(payload: DeepSeekKey) -> SettingsStatus:
        key = payload.api_key.get_secret_value().strip()
        if not key or len(key) > 512 or any(char.isspace() for char in key):
            raise HTTPException(status_code=422, detail="A single API key is required.")

        write_local_text(key_path, key)
        return SettingsStatus(configured=True)

    @app.delete(SETTINGS_PATH, dependencies=[Depends(require_extension)])
    def clear_settings() -> SettingsStatus:
        key_path.unlink(missing_ok=True)
        return SettingsStatus(configured=False)

    def require_thread(thread: Annotated[
        str, Header(alias="X-Seed-Thread", max_length=320, pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#[1-9][0-9]*$")
    ]):
        return thread

    thread_header = Annotated[str, Depends(require_thread)]
    trusted = [Depends(require_extension)]

    def enqueue(kind, thread, job_id, run):
        def persist(report):
            result = run(report)
            try:
                cache.save(job_id, kind, thread, result)
            except OSError:
                logger.warning("Could not persist the SEED %s result in the local cache", kind)
            return result

        try:
            return jobs.submit(kind, thread, job_id, persist)
        except JobError as error:
            raise HTTPException(429, detail={"code": error.code}) from None

    def find_job(job_id, kind, thread):
        cached = cache.read(job_id, kind, thread)
        if cached:
            return {"status": "completed", "result": cached["data"]}
        return jobs.get(job_id, kind, thread)

    def job_status(job_id, kind, thread):
        job = find_job(job_id, kind, thread)
        if job is None:
            raise HTTPException(404, detail={"code": "job_missing"})
        return job

    @app.post("/v1/cache/lookup", dependencies=trusted)
    def restore_cache(payload: CacheLookup, thread: thread_header):
        analysis = None
        summaries = []
        if payload.analysis_fingerprint:
            analysis_id = analysis_cache_id(thread, payload.analysis_fingerprint)
            analysis = cache.read(analysis_id, "analysis", thread)
            if analysis and analysis["data"]["evaluations"]:
                rows = evidence_rows(analysis["data"]["evaluations"])
                summary_id = summary_cache_id(
                    thread, "evaluations", evaluation_summary_content(analysis_id, rows)
                )
                summaries.append(cache.read(summary_id, "summary", thread))
        if payload.full_fingerprint:
            summary_id = summary_cache_id(thread, "full", payload.full_fingerprint)
            summaries.append(cache.read(summary_id, "summary", thread))
        summary = max(
            (item for item in summaries if item), key=lambda item: item["generated_at"], default=None
        )
        return {"analysis": analysis, "summary": summary}

    @app.post("/v1/analyses", status_code=202, dependencies=trusted)
    def start_analysis(payload: Discussion, thread: thread_header):
        discussion = payload.model_dump()
        analysis_id = analysis_cache_id(thread, discussion_fingerprint(discussion))
        if cache.read(analysis_id, "analysis", thread):
            return {"analysis_id": analysis_id, "cached": True}
        enqueue("analysis", thread, analysis_id, lambda report: extract(discussion, report))
        return {"analysis_id": analysis_id, "cached": False}

    @app.get("/v1/analyses/{analysis_id}", dependencies=trusted)
    def analysis_status(analysis_id: str, thread: thread_header):
        return job_status(analysis_id, "analysis", thread)

    @app.post("/v1/summaries", status_code=202, dependencies=trusted)
    def start_summary(
        payload: Annotated[EvaluationSummary | FullSummary, Body(discriminator="mode")],
        thread: thread_header,
    ):
        if payload.mode == "evaluations":
            analysis = find_job(payload.analysis_id, "analysis", thread)
            if analysis is None or analysis["status"] != "completed":
                raise HTTPException(409, detail={"code": "analysis_required"})
            evaluations = analysis["result"]["evaluations"]
            if not evaluations:
                raise HTTPException(409, detail={"code": "analysis_empty"})
            rows = evidence_rows(evaluations)
            content = evaluation_summary_content(payload.analysis_id, rows)
            title = ""
        else:
            rows = [{"source_id": msg.message_id, "text": msg.text} for msg in payload.messages]
            title = payload.title
            content = discussion_fingerprint(payload.model_dump())

        summary_id = summary_cache_id(thread, payload.mode, content)
        if cache.read(summary_id, "summary", thread):
            return {"summary_id": summary_id, "cached": True}

        key = read_key()
        if not key:
            raise HTTPException(409, detail={"code": "key_missing"})

        def run(report):
            report("summarize")
            return summarize(key, payload.mode, title, rows)

        enqueue("summary", thread, summary_id, run)
        return {"summary_id": summary_id, "cached": False}

    @app.get("/v1/summaries/{summary_id}", dependencies=trusted)
    def summary_status(summary_id: str, thread: thread_header):
        return job_status(summary_id, "summary", thread)

    return app


app = create_app()
