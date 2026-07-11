from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    ai_insights,
    automl,
    auth,
    bi,
    boms,
    datasets,
    downtime,
    forecasts,
    inventory,
    inventory_analytics,
    items,
    locations,
    lots,
    oee,
    orgs,
    process_mining,
    purchase_orders,
    quality,
    reports,
    scrap,
    shifts,
    spc,
    suppliers,
    throughput,
    workflows,
)
from app.core.config import get_settings
from app.middleware.telemetry import TelemetryMiddleware

settings = get_settings()

app = FastAPI(
    title="EvoWerk Cloud MRP",
    docs_url="/docs" if settings.env != "production" else None,
    redoc_url=None,
)

app.add_middleware(TelemetryMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(orgs.router)
app.include_router(items.router)
app.include_router(boms.router)
app.include_router(suppliers.router)
app.include_router(purchase_orders.router)
app.include_router(lots.router)
app.include_router(locations.router)
# Must be registered before inventory.router: /inventory/analytics is a
# static path that would otherwise be shadowed by inventory.router's
# /inventory/{item_id} (Starlette matches path shape before UUID validation).
app.include_router(inventory_analytics.router)
app.include_router(inventory.router)
app.include_router(forecasts.router)
app.include_router(forecasts.mrp_router)
app.include_router(datasets.router)
app.include_router(oee.router)
app.include_router(downtime.router)
app.include_router(throughput.router)
app.include_router(scrap.router)
app.include_router(shifts.router)
app.include_router(spc.router)
app.include_router(quality.router)
app.include_router(process_mining.router)
app.include_router(ai_insights.router)
app.include_router(reports.router)
app.include_router(bi.router)
app.include_router(workflows.router)
app.include_router(automl.router)


@app.get("/health")
def health():
    return {"ok": True}
