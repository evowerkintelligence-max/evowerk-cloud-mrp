# Product

## Register

product

## Platform

web

## Users

Production planners and MRP schedulers running BOM, procurement, and planning workflows; plant and shop-floor operations staff working from OEE, downtime, and quality views; and manufacturing execs/ops managers reviewing KPI dashboards and plant-summary rollups. All three are primary, each working a different lens over the same underlying data.

## Product Purpose

A multi-tenant, cloud-hosted manufacturing system covering BOM/recipe management, MRP planning, procurement, inventory and lot tracking, demand forecasting, shop-floor analytics, quality/RCA, predictive maintenance, and BI, with an industry-agnostic core that industry-specific customization layers onto later. Success is faster, more accurate planning decisions: less time reconciling data by hand, more confidence in the MRP plan and the POs it generates.

## Roadmap / Phasing

Phase 1 (current): manufacturing startups with modern OPC-UA/MQTT-capable equipment. Core tier (OEE, downtime, MRP, report builder, AI data chat) undercuts sensor-hardware incumbents on price; premium add-on (AutoML, predictive maintenance) monetizes customers who want it. Phase 2 (later, not concurrent): extend the inventory/forecasting engine into a standalone product for non-manufacturing industries (distribution, wholesale, retail) — sold on inventory and demand-trend analysis alone, no OEE/downtime/MRP, but carrying over the report builder and AI data chat from Phase 1 (already industry-agnostic, not manufacturing-specific). Differentiation: dedicated inventory tools (Cin7, Fishbowl, Zoho Inventory) mostly stop at tracking/reorder points, leaving demand forecasting, waste/loss-trend analysis, and reporting to bolted-on BI tools or spreadsheets — Phase 2 sells that whole bundle as one product. Vocabulary translates per vertical: manufacturing "scrap" becomes shrinkage (retail), spoilage (food/perishables), or return-rate (distribution) — same loss-trend analysis, different label. See [PRD.md](PRD.md) §3a for detail.

## Positioning

One system that turns live shop-floor, inventory, and demand data directly into an accurate MRP plan and the purchase orders it generates, so planners aren't reconciling spreadsheets and disconnected tools to get there.

## Brand Personality

Fast, sharp, technical. Built for expert users who live in this daily, not first-time visitors who need hand-holding. Precision reads as trustworthy here: exact numbers, unambiguous states, no decorative softening.

## Anti-references

Not legacy industrial/ERP software — no dated on-prem SCADA/SAP-era chrome. Not a generic SaaS admin template — no cookie-cutter identical stat cards, gradient text, or eyebrow labels. Not consumer-app playful — no casual, gamified, or social-feeling interaction patterns.

## Design Principles

- Precision over decoration: exact figures, tabular alignment, unambiguous status — the interface should read as something a planner can bet a production run on.
- Built for daily experts: favor density and speed over onboarding-style hand-holding.
- Technical, not templated: avoid the visual reflexes of generic SaaS dashboards and legacy ERP alike; this should look like nothing else in either category.
- No flashing or strobing motion: photosensitive-safe by default (see Accessibility).

## Accessibility & Inclusion

WCAG AA baseline. Specific known need: photosensitive epilepsy — no content may flash more than 3 times per second, and strobing/rapid-flicker effects are disallowed outright, not just reduced. `prefers-reduced-motion` is already respected app-wide (`frontend/src/index.css`); keep every new animation (including status pulses and gradient shifts) within that same slow, non-flashing envelope.
