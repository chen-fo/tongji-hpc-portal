import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api import admin, audit, auth, billing, files, jobs, terminal
from app.config import Settings, get_settings
from app.db.session import make_engine, make_session_factory
from app.services.audit import AuditService
from app.services.files import FileService
from app.services.jobs import JobSyncService
from app.services.terminal import TerminalService
from app.ssh.manager import make_ssh_manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("portal")


def create_app(settings: Settings | None = None, credentials=None) -> FastAPI:
    settings = settings or get_settings()
    engine = make_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        jobs_started = False
        try:
            with engine.connect() as conn:
                revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
                if revision != "0008_billing_evidence":
                    raise RuntimeError("数据库版本不匹配，请执行 alembic upgrade head")
            app.state.audit.start()
            jobs_started = app.state.jobs.start()
            logger.info("Portal started; SSH mode=%s", settings.ssh_mode)
            yield
        finally:
            if jobs_started:
                await app.state.jobs.stop()
            await app.state.terminals.stop()
            await app.state.audit.stop()
            engine.dispose()

    app = FastAPI(title="Tongji HPC Portal", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.sessions = make_session_factory(engine)
    app.state.engine = engine
    app.state.ssh = make_ssh_manager(settings, credentials)
    app.state.audit = AuditService(app.state.sessions)
    app.state.files = FileService(settings, app.state.sessions, app.state.ssh, app.state.audit)
    app.state.jobs = JobSyncService(settings, app.state.sessions, app.state.ssh)
    app.state.terminals = TerminalService(
        settings, app.state.sessions, app.state.ssh, app.state.audit
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        if (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            and request.headers.get("origin") not in settings.allowed_origins
        ):
            response = JSONResponse({"detail": "请求来源不受信任"}, status_code=403)
        else:
            response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Pydantic's default errors echo input values, which may contain passwords.
        errors = [
            {"loc": error["loc"], "msg": error["msg"], "type": error["type"]}
            for error in exc.errors()
        ]
        return JSONResponse({"detail": errors}, status_code=422)

    @app.get("/api/health")
    def health():
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok", "ssh_mode": settings.ssh_mode, "phase": "0-4-observe"}

    app.include_router(auth.router)
    app.include_router(admin.router)
    app.include_router(audit.router)
    app.include_router(terminal.router)
    app.include_router(files.router)
    app.include_router(jobs.router)
    app.include_router(billing.router)
    return app


app = create_app()
