from __future__ import annotations

import argparse
import logging
import multiprocessing
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import urlparse

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import __version__
from app.config.paths import AppPaths
from app.models.errors import ConfigurationError
from app.runtime import AgentRuntime, build_runtime
from app.security.logging_config import configure_logging
from app.web.api import create_api_router, install_web_routes, register_exception_handlers
from app.web.body_limit import RequestBodyLimitMiddleware

logger = logging.getLogger(__name__)


def create_application(
    runtime: AgentRuntime | None = None, data_dir: str | Path | None = None
) -> FastAPI:
    agent_runtime = runtime or build_runtime(data_dir)
    # Configure redaction even when this factory is used by an external ASGI server,
    # not only through main.run().
    configure_logging(agent_runtime.paths.log_dir)
    agent_runtime.ensure_web_admin_token()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await agent_runtime.start()
        try:
            yield
        finally:
            await agent_runtime.stop()

    app = FastAPI(
        title="Rakuten MT4 Remote Login Agent",
        version=__version__,
        description="Localhost-only Web Admin for the auditable MT4 login agent",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )

    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "[::1]"],
    )
    app.add_middleware(RequestBodyLimitMiddleware)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        def apply_headers(response):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
                "style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'"
            )
            return response

        path = request.url.path
        if path.startswith("/api/") and path != "/api/health":
            origin = request.headers.get("origin")
            if origin:
                origin_host = urlparse(origin).hostname
                if origin_host not in {"127.0.0.1", "localhost", "::1"}:
                    return apply_headers(
                        JSONResponse(
                            status_code=403,
                            content={"detail": "Cross-origin Web Admin request rejected"},
                        )
                    )
            if not agent_runtime.web_admin_token_matches(request.headers.get("x-admin-token")):
                return apply_headers(
                    JSONResponse(
                        status_code=401,
                        content={"detail": "Local admin token required"},
                    )
                )
        response = await call_next(request)
        return apply_headers(response)

    app.state.runtime = agent_runtime
    app.include_router(create_api_router(agent_runtime))
    register_exception_handlers(app)
    install_web_routes(app, agent_runtime)
    return app


def _port_is_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:
            with suppress(OSError):
                probe.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def run() -> None:
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser(description="Rakuten MT4 Remote Login Agent V1")
    parser.add_argument("--data-dir", default=None, help="Override the local data directory")
    parser.add_argument("--port", type=int, default=None, help="Override localhost Web Admin port")
    args = parser.parse_args()

    paths = AppPaths.from_value(args.data_dir)
    try:
        runtime = build_runtime(paths.data_dir)
        port = args.port if args.port is not None else runtime.settings.get().web_port
    except (ConfigurationError, ValueError, RuntimeError) as exc:
        print(f"[ERROR] Cannot start Agent: {exc}")
        raise SystemExit(1) from exc
    if not _port_is_available(port):
        print(
            f"[ERROR] Port {port} is already in use. Close the existing process or "
            "change web_port in settings.json."
        )
        raise SystemExit(1)
    admin_token = runtime.ensure_web_admin_token()
    print(f"Web Admin local admin token (keep private): {admin_token}")
    logger.info("Web Admin will listen on http://127.0.0.1:%d", port)
    try:
        uvicorn.run(create_application(runtime), host="127.0.0.1", port=port, log_level="info")
    except OSError as exc:
        print(f"[ERROR] Web Admin could not start: {exc}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    run()
