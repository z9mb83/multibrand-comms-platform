from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import auth, brands, campaigns, contacts, imports, inbox, internal, mailboxes, reports, senders, webhooks, workflow
from app.core.config import get_settings
from app.services.common import DomainError


def create_app() -> FastAPI:
    s = get_settings()
    if s.app_env == "production":
        from app.core.config import LOCAL_INTERNAL, LOCAL_JWT
        if s.jwt_secret == LOCAL_JWT or s.internal_token == LOCAL_INTERNAL:
            raise RuntimeError("Refusing to start: set JWT_SECRET and INTERNAL_TOKEN from Secret Manager")
    app = FastAPI(title="Multi-Brand Communication Platform", version="0.1.0")
    app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in s.cors_origins.split(",") if o.strip()],
                       allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

    @app.exception_handler(DomainError)
    async def domain_error(_: Request, exc: DomainError):
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    @app.get("/health", tags=["meta"])
    def health():
        return {"status": "ok", "env": s.app_env, "provider_mode": s.provider_mode}

    for r in (auth.router, auth.users, brands.router, mailboxes.router, senders.router, contacts.router, imports.router,
              campaigns.router, campaigns.global_router, inbox.router, workflow.router, workflow.tasks_global,
              reports.router, reports.analytics, webhooks.router, internal.router):
        app.include_router(r, prefix="/api" if r not in (webhooks.router, internal.router) else "")
    return app


app = create_app()
