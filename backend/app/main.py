from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import auth, orgs
from app.core.config import get_settings

settings = get_settings()

app = FastAPI(
    title="EvoWerk Cloud MRP",
    docs_url="/docs" if settings.env != "production" else None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(orgs.router)


@app.get("/health")
def health():
    return {"ok": True}
