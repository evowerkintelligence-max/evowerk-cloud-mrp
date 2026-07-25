from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    ai_insights,
    alerts,
    automl,
    auth,
    bi,
    boms,
    cbm,
    computer_vision,
    data_chat,
    datasets,
    device_tokens,
    downtime,
    downtime_tracking,
    forecasts,
    integrations,
    inventory,
    inventory_analytics,
    items,
    kpi_dashboards,
    kpis,
    locations,
    lots,
    machines,
    mfg_forecast,
    oauth,
    oee,
    orgs,
    pace_tracking,
    plant_summary,
    predictive,
    process_mining,
    purchase_orders,
    quality,
    rca_cases,
    realtime,
    reports,
    routings,
    scrap,
    shifts,
    simulation,
    spc,
    suppliers,
    throughput,
    work_centers,
    workflows,
)
from app.core.config import get_settings
from app.middleware.telemetry import TelemetryMiddleware
from app.services import automl_realtime as automl_realtime_service
from app.services import realtime as realtime_service
from app.services import realtime_cache
from app.services import sensor_consumer

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Re-adopt any realtime stream that was running before this process
    # started (redeploy/restart/crash) - see app.services.realtime.
    await realtime_service.resume_all_streams()
    # Start this replica's realtime_cache consumer - see its module
    # docstring for why every replica needs its own consumer group.
    await realtime_cache.start_cache_consumer()
    # Same re-adoption for live AutoML scoring/drift bindings - see
    # app.services.automl_realtime.
    await automl_realtime_service.resume_all_bindings()
    await sensor_consumer.start_sensor_consumer()
    yield
    automl_realtime_service.stop_all_binding_schedulers()
    await sensor_consumer.stop_sensor_consumer()
    await realtime_cache.stop_cache_consumer()
    realtime_service.stop_all_runners()


app = FastAPI(
    title="EvoWerk Cloud MRP",
    docs_url="/docs" if settings.env != "production" else None,
    redoc_url=None,
    lifespan=lifespan,
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
if settings.google_oauth_client_id or settings.microsoft_oauth_client_id:
    app.include_router(oauth.router)
app.include_router(orgs.router)
app.include_router(items.router)
app.include_router(boms.router)
app.include_router(suppliers.router)
app.include_router(purchase_orders.router)
app.include_router(lots.router)
app.include_router(locations.router)
app.include_router(machines.router)
# Must be registered before inventory.router: /inventory/analytics is a
# static path that would otherwise be shadowed by inventory.router's
# /inventory/{item_id} (Starlette matches path shape before UUID validation).
app.include_router(inventory_analytics.router)
app.include_router(inventory.router)
app.include_router(forecasts.router)
app.include_router(forecasts.mrp_router)
app.include_router(datasets.router)
app.include_router(oee.router)
app.include_router(pace_tracking.router)
app.include_router(plant_summary.router)
app.include_router(downtime.router)
app.include_router(downtime_tracking.router)
app.include_router(throughput.router)
app.include_router(scrap.router)
app.include_router(shifts.router)
app.include_router(simulation.router)
app.include_router(spc.router)
app.include_router(quality.router)
app.include_router(process_mining.router)
app.include_router(ai_insights.router)
app.include_router(reports.router)
app.include_router(routings.router)
app.include_router(bi.router)
app.include_router(workflows.router)
app.include_router(automl.router)
app.include_router(kpis.router)
app.include_router(kpi_dashboards.router)
app.include_router(integrations.router)
app.include_router(realtime.router)
app.include_router(rca_cases.router)
app.include_router(predictive.router)
app.include_router(mfg_forecast.router)
app.include_router(work_centers.router)
app.include_router(computer_vision.router)
app.include_router(data_chat.router)
app.include_router(alerts.router)
app.include_router(device_tokens.router)
app.include_router(cbm.router)


@app.get("/health")
def health():
    return {"ok": True}
