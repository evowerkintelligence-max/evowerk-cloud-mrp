# E2E Explore Agent

## Purpose

An on-demand, autonomous browser agent that exercises the deployed production
app as a real user would, across the full route surface, and reports what's
broken. Not a scripted regression suite (no Playwright test files to
maintain) — an LLM-driven agent that navigates, adapts to whatever the UI
currently looks like, and writes up findings in plain English.

## Non-goals

- No scheduling/cron, no CI wiring. Triggered manually via `/e2e-explore`.
- No deploy pipeline changes. Tests whatever is already live.
- Not a replacement for the scripted CI suite (pytest, tsc) — this catches
  integration/UX issues those can't see (dead buttons, empty dashboards,
  console errors), not code-level regressions.

## Architecture

A new project skill: `.claude/skills/e2e-explore/SKILL.md`, invoked as
`/e2e-explore`. When run, it:

1. Loads `E2E_APP_URL`, `E2E_EMAIL`, `E2E_PASSWORD` from a local `.env.e2e`
   file (gitignored via the existing `.env` pattern in `.gitignore`; not
   committed, not hardcoded in the skill).
2. Opens the app via the existing `mcp__Claude_Browser__*` tools
   (`preview_start` with the URL, `navigate`, `computer`, `read_page`,
   `get_page_text`, `read_console_messages`, `read_network_requests`).
3. Logs in with email + password (test-org account has MFA disabled — no
   MFA-challenge handling needed).
4. Walks the route checklist below, recording findings as it goes.
5. Writes a markdown report to `docs/e2e-agent-reports/<timestamp>.md` and
   prints a chat summary (route count, finding count, top issues).

No new service, no deployment, no persistent process — a skill definition
that drives tools already available in a Claude Code session.

## Safety: test org isolation

The app is multi-tenant (`app.models.tenant.Org`, every query scoped by
`org_id`). The agent authenticates as a **dedicated test-org account** that
already exists on the production deployment. All creates/edits/deletes the
agent performs land in that org only — the same tenancy boundary the app
enforces everywhere keeps it from touching real customer orgs. The agent is
free to mutate within the test org; no read-only restriction needed.

Credentials live only in the local `.env.e2e` (gitignored). The skill must
never print credential values to chat or the report.

## Test scope

Every authenticated top-level route in `frontend/src/App.tsx`:

Items, BOMs, Procurement, Lots, Warehouse, Planning, `analytics/oee`,
`analytics/pace-tracking`, `analytics/plant-summary`, `analytics/downtime`,
`analytics/downtime-tracking`, `analytics/throughput`, `analytics/scrap`,
`analytics/shifts`, `analytics/spc`, Datasets (+ dataset detail), SQL Editor,
Simulation, Ask, BI Dashboards (+ new chart, + dashboard detail), Workflows
(+ workflow detail), Run History, Report Builder, AutoML (templates home,
advanced, advanced run), KPIs (+ dashboards), Integrations, Realtime, RCA
Cases, Predictive, Mfg Forecast, Computer Vision, Alerts.

For routes with an obvious primary write action (create item, create BOM,
create PO, submit a filter/report, run a template), the agent performs that
action once rather than only loading the page — a page that loads fine but
whose Save button silently no-ops is a real finding a load-only check would
miss. For read-only/dashboard routes, load + verify real content renders is
enough.

## Failure criteria (strict, two buckets)

A route gets flagged 🔴 if **any** of:

- Console error or warning (not just errors)
- Any 4xx or 5xx from the app's own API (no "expected 4xx" carve-out — if it
  happens, it's listed with context, not silently excused)
- 404 on a static asset (JS/CSS/image)
- A visibly blank/crashed area where content or the primary action was
  expected
- The primary write action (where one exists) doesn't produce a visible
  effect (no confirmation, no new row, no navigation)

Otherwise ✅ clean. No "suspicious"/gray-zone bucket — the agent commits to
one of the two.

## Report format

`docs/e2e-agent-reports/<timestamp>.md`:

```markdown
# E2E Explore Report — <timestamp>

Summary: N routes checked, M findings.

## Findings

### 🔴 <route path>
<what happened, console/network evidence, expected vs actual>

## Clean

- <route path> — ✅
- ...
```

Chat output: the summary line plus the finding list (route + one-line cause),
full detail lives in the file.

## Self-check

Since this is a single skill file, not a code module with a test suite: the
implementation plan should include one real end-to-end dry run against the
test org as the acceptance check — the skill is only "done" once a live
`/e2e-explore` run against the actual deployment produces a report, not once
the file merely exists.
