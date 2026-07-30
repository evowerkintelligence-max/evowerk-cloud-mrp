from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.deps import require_feature
from app.api.routes import (
    ai_insights,
    admin,
    alerts,
    automl,
    auth,
    bi,
    boms,
    cbm,
    customers,
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
    operator_capture,
    operators,
    orgs,
    pace_tracking,
    planning_grid,
    plant_summary,
    predictive,
    process_mining,
    purchase_orders,
    quality,
    rca_cases,
    realtime,
    reports,
    routings,
    sales_orders,
    scrap,
    shifts,
    simulation,
    spc,
    staff_auth,
    suppliers,
    throughput,
    work_centers,
    work_orders,
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
app.include_router(staff_auth.router)
if settings.google_oauth_client_id or settings.microsoft_oauth_client_id:
    app.include_router(oauth.router)
app.include_router(orgs.router)
app.include_router(admin.router)
app.include_router(items.router)
app.include_router(boms.router, dependencies=[Depends(require_feature("boms"))])
app.include_router(suppliers.router)
app.include_router(purchase_orders.router, dependencies=[Depends(require_feature("procurement"))])
app.include_router(customers.router, dependencies=[Depends(require_feature("sales"))])
app.include_router(sales_orders.router, dependencies=[Depends(require_feature("sales"))])
app.include_router(lots.router, dependencies=[Depends(require_feature("lots"))])
app.include_router(locations.router)
app.include_router(machines.router)
app.include_router(operators.router)
app.include_router(operator_capture.router)
# Must be registered before inventory.router: /inventory/analytics is a
# static path that would otherwise be shadowed by inventory.router's
# /inventory/{item_id} (Starlette matches path shape before UUID validation).
app.include_router(inventory_analytics.router, dependencies=[Depends(require_feature("warehouse"))])
app.include_router(inventory.router, dependencies=[Depends(require_feature("warehouse"))])
app.include_router(forecasts.router, dependencies=[Depends(require_feature("planning"))])
app.include_router(forecasts.mrp_router, dependencies=[Depends(require_feature("planning"))])
app.include_router(planning_grid.router, dependencies=[Depends(require_feature("planning"))])
app.include_router(datasets.router)
app.include_router(oee.router, dependencies=[Depends(require_feature("oee"))])
app.include_router(pace_tracking.router, dependencies=[Depends(require_feature("pace_tracking"))])
app.include_router(plant_summary.router, dependencies=[Depends(require_feature("plant_summary"))])
app.include_router(downtime.router, dependencies=[Depends(require_feature("downtime"))])
app.include_router(downtime_tracking.router, dependencies=[Depends(require_feature("downtime_tracking"))])
app.include_router(throughput.router, dependencies=[Depends(require_feature("throughput"))])
app.include_router(scrap.router, dependencies=[Depends(require_feature("scrap"))])
app.include_router(shifts.router, dependencies=[Depends(require_feature("shifts"))])
app.include_router(simulation.router, dependencies=[Depends(require_feature("simulation"))])
app.include_router(spc.router, dependencies=[Depends(require_feature("spc"))])
app.include_router(quality.router)
app.include_router(process_mining.router)
app.include_router(ai_insights.router)
app.include_router(reports.router, dependencies=[Depends(require_feature("reports"))])
app.include_router(routings.router)
app.include_router(bi.router)
app.include_router(workflows.router)
app.include_router(automl.router, dependencies=[Depends(require_feature("automl"))])
app.include_router(kpis.router, dependencies=[Depends(require_feature("kpis"))])
app.include_router(kpi_dashboards.router, dependencies=[Depends(require_feature("kpi_dashboards"))])
app.include_router(integrations.router)
app.include_router(realtime.router)
app.include_router(rca_cases.router, dependencies=[Depends(require_feature("rca_cases"))])
app.include_router(predictive.router, dependencies=[Depends(require_feature("predictive"))])
app.include_router(mfg_forecast.router, dependencies=[Depends(require_feature("mfg_forecast"))])
app.include_router(work_centers.router)
app.include_router(work_orders.router, dependencies=[Depends(require_feature("production"))])
app.include_router(data_chat.router, dependencies=[Depends(require_feature("data_chat"))])
app.include_router(alerts.router)
app.include_router(device_tokens.router)
app.include_router(cbm.router, dependencies=[Depends(require_feature("predictive"))])


@app.get("/health")
def health():
    return {"ok": True}
