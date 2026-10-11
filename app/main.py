import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import routes_auth, routes_certificates, routes_jobs, routes_public
from app.config import get_settings
from app.core.errors import error_body, register_exception_handlers
from app.core.logging import configure_logging, request_id_var

log = logging.getLogger("certgen")


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    log.info("certgen api starting")
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Bulk Certificate Generator",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.enable_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.enable_docs else None,
    )
    register_exception_handlers(app)

    if settings.cors_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
        )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("X-Request-ID", "")
        rid = rid if 0 < len(rid) <= 64 and rid.replace("-", "").isalnum() else uuid.uuid4().hex
        token = request_id_var.set(rid)
        try:
            length = request.headers.get("content-length")
            if length and length.isdigit() and int(length) > settings.max_body_bytes:
                response = JSONResponse(error_body("PAYLOAD_TOO_LARGE", "Request body too large"), status_code=413)
            else:
                response = await call_next(request)
            response.headers["X-Request-ID"] = rid
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
            if settings.is_production:
                response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
            return response
        finally:
            request_id_var.reset(token)

    app.include_router(routes_public.router)
    app.include_router(routes_auth.router)
    app.include_router(routes_jobs.router)
    app.include_router(routes_certificates.router)
    return app


app = create_app()
