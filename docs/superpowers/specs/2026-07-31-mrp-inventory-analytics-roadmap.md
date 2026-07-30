# MRP / Inventory Analytics: multi-phase roadmap

Date: 2026-07-31
Status: order agreed, Phase 1 design approved below; phases 1.5+ still need their own design pass before implementation.

## Context

The MRP overhaul (Planning Grid → Sales Orders as committed demand → Work
Orders) shipped and follows a "one screen, hide the engine" philosophy the
user committed to on 2026-07-26 (see
[[2026-07-26-planning-grid-design]] and the `mrp-overhaul-direction` memory).

Separately, a code investigation surfaced that `backend/app/services/inventory_analytics.py`
(ABC/XYZ classification, safety stock, reorder point, RCA chi-square,
scenario compare) is a real, tested engine with almost no frontend surface
— only the morning-brief digest is wired up. The same investigation
surfaced a long list of feature gaps against a broader "inventory analysis
app" bar: Indian GST/HSN/E-Way/E-Invoice compliance, multi-UOM selling,
barcode/QR scanning, cold-chain logging, materialized/cached analytics,
and food-freshness handling (shelf-life-aware alerts, markdown flagging,
recall genealogy).

The user asked to fold all of this (minus demand forecasting, which is
explicitly deferred to its own future track) into "one incredible,
cohesive MRP/Inventory Analytics feature." That request spans multiple
independent subsystems, so per the brainstorming process it was decomposed
into phases with an explicit build order instead of one design doc.

## What's already built (no phase needed)

- FEFO auto-routing on sales orders (`consume_lot_fefo`, oldest-expiry-first)
- Batch/lot tracking with expiry dates and hold status
- Append-only double-entry-style inventory ledger (`InventoryTransaction`)
- Flat multi-warehouse/location model with transfers
- Mature versioned BOM engine
- Planning Grid / Sales Orders / Work Orders (the existing MRP overhaul)

## Phase order and rationale

| # | Phase | What it does | Depends on | Why here |
|---|---|---|---|---|
| 1 | **Inventory Analytics UI** | Surfaces ABC/XYZ, RCA, and scenario-compare on the existing Inventory page | Nothing — pure UI wiring against tested endpoints | Lowest risk, zero new backend logic, continues the Planning Grid pattern already committed to. **Design finalized — see [[2026-07-31-inventory-analytics-ui-design]]** |
| 1.5 | **Lot upgrade: freshness, recall & cold-chain** | Per-item/category shelf-life config (replaces the fixed 14-day `LOT_EXPIRY_WARNING_DAYS` global cutoff), markdown/sell-first flag on near-expiry lots, recall genealogy (which sales orders/customers received units from a given lot), storage-condition fields (temperature range, climate zone) | Phase 1 (same alerting/analytics surface) | Touches the same code area as Phase 1 while it's fresh; food/pharma freshness and recall traceability are high-stakes and currently missing entirely |
| 2 | **Valuation, aging & turnover reporting** | $ value by status/class, aging buckets, turnover ratio — new backend calculations | Phase 1 (same screen, same "plain-language headline, expand for detail" pattern) | Natural extension of the page just built; no new subsystem |
| 3 | **Multi-UOM selling engine** | Extends `ItemUnit`/`factor_to_base` (today BOM-line-only) into sales orders, purchase orders, and inventory transactions | Nothing structurally, but blocks Phase 4 | Indian tax invoices must report quantity/value in the UOM actually sold — GST can't be built correctly on a single-UOM order flow. Also a prerequisite for meaningful barcode scan quantities (Phase 5) |
| 4 | **GST & Indian compliance** | HSN codes, CGST/SGST/IGST calculation, E-Way Bill + E-Invoice API integration | Phase 3 (correct per-line UOM), existing Sales/Purchase Orders (already built) | Highest-stakes, highest-effort piece — a government-integration project. Sequenced after its prerequisite so it isn't built on a shaky foundation |
| 5 | **Barcode & QR scanning** | Camera-based scan-to-count, scan-to-pick, scan-to-lookup on mobile | Phase 3 (a scanned pick needs to know what UOM was scanned) | Independent capability, sequenced after Multi-UOM so a scan resolves to a real quantity |
| 7 | **Materialized/cached analytics** | Precomputed tables for stock aging, ABC, turnover at scale | Phases 1–2 (needs something worth caching first) | Deliberately last — the engine computes live specifically to avoid stale-cache bugs; only worth the complexity once real data volume makes live compute slow |

Explicitly out of this roadmap: demand forecasting improvements (existing
moving-average/exponential-smoothing engine stays as-is; any AI/ML
forecasting work is its own future track per the user's instruction).

## Process going forward

Each phase gets its own design → implementation-plan → build cycle before
the next phase starts. This document is the sequencing decision, not a
substitute for designing phases 1.5 onward when their turn comes.
