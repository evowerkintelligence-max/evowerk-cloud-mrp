# PRD: EvoWerk Cloud MRP

**Status:** retroactive — documents the system as it exists in the codebase today, not a pre-build spec. Written 2026-07-21, refreshed 2026-07-22.

## 1. Summary

EvoWerk Cloud MRP is a multi-tenant, cloud-hosted manufacturing system covering BOM/recipe management, MRP planning, procurement, inventory and lot tracking, demand forecasting, shop-floor analytics, quality/RCA, predictive maintenance, and BI. The core is industry-agnostic; industry-specific customization (pharma, food, auto, etc.) is designed to layer on later, config-driven, per customer.

It is a separate product/codebase from EvoWerk's desktop app (NovaEdgeElectron) — no code is shared beyond common pip packages for core math (data cleaning, OEE calc, forecasting).

## 2. Problem

Production planners and plant operations teams reconcile MRP plans, inventory, and shop-floor data by hand across spreadsheets and disconnected tools. Decisions about what to build and what to buy lag behind what's actually happening on the floor.

## 3. Goal

One system that turns live shop-floor, inventory, and demand data directly into an accurate MRP plan and the purchase orders it generates, so planners aren't reconciling spreadsheets and disconnected tools to get there. Success is measured qualitatively (for now — see §8): faster, more accurate planning decisions, less manual reconciliation, more confidence in the plan and the POs it produces.

## 3a. Go-to-market phasing (added 2026-07-23)

**Phase 1 — manufacturing startups.** First customers are newer manufacturing operations, not legacy plants — machines young enough to speak OPC-UA/MQTT natively, which is what makes the premium tier below deliverable on day one instead of blocked on a hardware-connectivity gap. Sold in two tiers:
- **Core**: OEE, downtime, MRP, report builder, AI data chat — the wedge, priced to undercut sensor-hardware-vendor incumbents (e.g. TeepTrak) on a like-for-like OEE/downtime feature set.
- **Premium add-on**: AutoML + predictive maintenance (CBM/RUL). Depends on live sensor ingestion, already supported for OPC-UA/MQTT-capable equipment via the connectors in §6 ("Data integrations") and the CBM/RUL services (`backend/app/services/cbm.py`, `backend/app/services/rul.py`). Remaining friction is per-customer PLC/controller onboarding, not a missing capability.

**Core pricing.** Flat per-org base covering up to 5 machines, plus per-machine overage beyond that:
- **Founding cohort (first 10 customers):** $100-150/month base, +$30/machine beyond 5.
- **Standard (customer 11+):** $150-200/month base, +$30/machine beyond 5.

Undercuts the realistic alternative a Core buyer is otherwise paying for — a standalone OEE/downtime tool (Evocon ~$149/mo, Mingo ~$80/machine/mo) stacked with a standalone MRP tool (MRPeasy ~$245-345/mo for a 5-person shop) — while bundling both into one subscription.

**Phase 2 — horizontal expansion beyond manufacturing.** Once Phase 1 has traction, extend the existing inventory + demand-forecasting engine into a standalone offering for industries with no shop floor at all (distribution, wholesale, retail) — sold on inventory analysis and sales/demand trend forecasting, without OEE/downtime/MRP, carrying over the report builder and AI data chat from Phase 1 (already industry-agnostic BI, not manufacturing-specific). This is distinct from the industry-specific customization layers in §10 (pharma/food/auto), which extend the manufacturing core for manufacturing customers; Phase 2 is a different buyer with a different core product. Not yet scoped in detail — sequenced after Phase 1 traction, not concurrent with it.

**Differentiation.** Dedicated inventory tools (Cin7, Fishbowl, Zoho Inventory) mostly stop at tracking and reorder points; demand forecasting and waste/loss-trend analysis are usually a separate BI tool or spreadsheet the customer bolts on themselves. Phase 2 sells the bundle as one product instead of three — inventory tracking, demand/loss-trend forecasting, and report builder + AI data chat on top, where competitors ship at most the first. Vocabulary translates per vertical — the manufacturing "scrap" concept (loss/waste trend against a baseline) becomes shrinkage (retail), spoilage (food/perishables), or return-rate (distribution) — same underlying analysis, different label per customer segment.

## 4. Users

Three primary personas, each working a different lens over the same underlying data — none is secondary:

- **Production planners / MRP schedulers** — run BOM, procurement, and planning workflows.
- **Plant and shop-floor operations staff** — work from OEE, downtime, and quality views.
- **Manufacturing execs / ops managers** — review KPI dashboards and plant-summary rollups.

## 5. Positioning & brand

Fast, sharp, technical — built for expert users who live in this daily, not first-time visitors who need hand-holding. Precision reads as trustworthy: exact numbers, unambiguous states, no decorative softening.

**Anti-references:** not legacy industrial/ERP software (no dated on-prem SCADA/SAP-era chrome); not a generic SaaS admin template (no cookie-cutter stat cards, gradient text, eyebrow labels); not consumer-app playful (no casual/gamified/social interaction patterns).

Full design system detail lives in [DESIGN.md](DESIGN.md); product register in [PRODUCT.md](PRODUCT.md).

## 6. Scope — what's built

Organized by feature area. Each maps to a backend router (`backend/app/api/routes/`) and, where applicable, a frontend page (`frontend/src/pages/`).

### Security & platform foundation
- Multi-tenant data model — `org_id` scoping on every table from day one.
- Auth: OAuth2 password grant + JWT + TOTP MFA, account lockout, encrypted TOTP secrets at rest, server-side password policy, Redis-backed per-IP rate limiting on register/login/refresh/mfa-verify. Google and Microsoft social login (verified-email gating, MFA-aware) as an alternative to password login; the frontend only shows the buttons for providers a given deployment actually has client credentials for.
- Email flows: password reset and email verification, provider-agnostic interface with an SES backend — a deployment with no `ses_from_email` configured gets reset/verification links logged instead of emailed (fine for test/eval use, not for production without SES set up).
- Append-only audit log — enforced at the Postgres trigger level, not just app discipline.

### MRP core
- BOMs/recipes with revisioning, unit conversion on BOM lines.
- Netted-vs-gross MRP explosion.
- Build calculator.

### Procurement
- Purchase orders, suppliers, preferred-supplier selection, MOQ rounding.
- Auto-generated POs off the MRP plan.
- Supplier relationship depth (contacts, addresses, terms, tags, scorecards).

### Inventory
- On-hand tracking, lot/FEFO consumption, multi-location.
- Concurrency-safe stock adjustments with full audit trail.
- Inventory adjust approval workflow + cycle counting.

### Demand forecasting
- Moving average / exponential smoothing, feeding the MRP plan and PO generation.
- Manufacturing-specific forecast variant (`mfg_forecast`).

### Shop-floor analytics
- OEE, SPC control charts, scrap, downtime (Pareto/MTTR), throughput, shift comparison.
- Rolled-up plant summary (single-screen OEE + scrap combination).
- Manual/assembly-line pace tracking; live downtime tracking.

### Quality & RCA
- Defect-rate/DPU analysis.
- Root-cause cases with fishbone-style cause trees and CAPA actions.
- Computer vision: image-based defect/PPE detection via Gemini vision.

### Predictive maintenance
- Machine risk scoring and maintenance scheduling.

### Simulation
- Monte Carlo + sensitivity analysis, scenario comparison against a baseline.

### BI / ad-hoc analytics
- Dataset upload & cleaning, pivot/SQL query (with syntax highlighting + autocomplete), custom dashboards/charts, KPIs (with breakdown drill-down), saved reports with AI narration.
- Natural-language "Ask" chat over uploaded data.
- AutoML: sklearn model bake-off, SHAP explainability, drift detection.

### Data integrations
CSV/Excel, SQL Server, Oracle, OPC-UA, OPC-DA, MQTT, generic REST/SAP OData, and OSIsoft PI historian connectors.

### Workflow automation
- DAG-based workflow builder with run history.
- Realtime streams (Redpanda/Kafka-protocol broker + Flink pipeline) and alert rules (inventory, downtime, quality, production target).

### Process mining
Discovers actual process flow from event logs (discovery + conformance checking).

## 7. Non-functional requirements

- **Multi-tenancy**: every table scoped by `org_id`; cross-org isolation is a tested invariant across the test suite (53+ backend test modules).
- **Deployment model**: customer-hosted, not EvoWerk-managed. EvoWerk does not take on responsibility for customer data or infrastructure. Terraform modules exist for AWS (ECS + ALB + RDS) and Azure (Container Apps + Key Vault) — see [infra/terraform/](infra/terraform/). The AWS module's backend Fargate task defaults to 1024 CPU / 2048MB — raised from the Fargate minimum (256/512) after that OOM-killed under real multi-endpoint traffic, since the backend loads a full ML stack (xgboost, lightgbm, shap, scipy, pandas, duckdb) at startup. Per-deployment rate limits (login/refresh/register/MFA-verify) are Terraform variables layered over the app's own secure defaults, so a given deployment can raise them (e.g. for automated testing against it) without weakening what every other deployment gets.
- **Security gates in CI**: SAST (bandit), dependency scanning (pip-audit, npm audit), secret scanning (detect-secrets), container scanning (Trivy), and a migration check that proves audit-log immutability against a real Postgres instance on every push.
- **Accessibility**: WCAG AA baseline. Photosensitive-safe by default — no content flashes more than 3×/second, strobing effects disallowed outright (not just reduced). `prefers-reduced-motion` respected app-wide.
- **Stack**: FastAPI + async SQLAlchemy 2.0 + Alembic + Postgres backend; React + TypeScript (Vite) + TanStack Query + Zustand frontend. Redis used only for rate limiting — no business data lives there.

## 8. Success metrics

No usage analytics or metrics instrumentation exists in the codebase today. The qualitative goal (§3) — less manual reconciliation, more planning confidence — is not yet backed by measured numbers. Defining and instrumenting concrete metrics (e.g., time-to-generate-a-PO, forecast accuracy vs. actuals, planner-reported confidence) is open work, not a documented decision.

## 9. Known gaps / deferred (as of 2026-07-21)

From the README's "Deferred / not yet built" and [TODO.md](TODO.md):

- **Fine-grained RBAC** — currently a single `role` string per user; no per-feature permission checks.
- **Account deactivation/invite UI** — `is_active` exists and is enforced, but nothing in the UI sets it to `false` yet.
- **Dataset storage persistence in docker-compose** — uploaded datasets/models write to local disk with no volume mount; every restart wipes them (self-hosted dev/test deployments only — the AWS/Azure Terraform modules are unaffected).
- **Frontend dev-mode Dockerfile** — `frontend/Dockerfile` runs the Vite dev server, not a production build; `frontend/Dockerfile.prod` is the correct production artifact and is what the Terraform modules use.
- **Compliance evidence** (SOC2/GDPR/HIPAA/ISO 27001) — not started.
- **DAST** (dynamic scanning) — deferred until a standing staging environment exists.
- **Secrets-manager integration on EvoWerk's side** — intentionally out of scope; the customer's own cloud secrets manager is the integration point, not EvoWerk's.

## 10. Out of scope

- EvoWerk-hosted/managed infrastructure of any kind — this is a customer-hosted product by design.
- Industry-specific customization layers (pharma, food, auto) — planned to sit on top of the industry-agnostic core later, not yet built.
- Code sharing with the EvoWerk desktop app beyond common math packages.
