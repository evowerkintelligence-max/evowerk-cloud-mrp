# E2E Explore Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Task 3 requires real production credentials the user must supply — do not delegate Task 3 to an isolated subagent without first confirming `.env.e2e` exists with working credentials.**

**Goal:** Ship an on-demand Claude Code skill (`/e2e-explore`) that autonomously walks every authenticated route of the production app as a real user, using an isolated test org, and reports what's broken.

**Architecture:** A single markdown skill file (`.claude/skills/e2e-explore/SKILL.md`) instructs the invoking Claude session to read test-org credentials from a local `.env.e2e`, drive the existing `mcp__Claude_Browser__*` Playwright-backed tools to log in and walk the route checklist, and write a markdown findings report. No new service, process, or scheduling — this is prompt/instruction content, not application code.

**Tech Stack:** Claude Code project skill (YAML frontmatter + markdown), `mcp__Claude_Browser__*` tools (already available), no new dependencies.

## Global Constraints

- Credentials (`E2E_APP_URL`, `E2E_EMAIL`, `E2E_PASSWORD`) live only in a local, gitignored `.env.e2e` — never hardcoded, never printed to chat or the report.
- Test-org account has MFA disabled; login is email+password only, no MFA-challenge handling.
- Failure criteria are strict, two-bucket (🔴 finding / ✅ clean): any console error or warning, any 4xx/5xx from the app's own API, any 404 static asset, any visibly blank/crashed area, or a primary write action producing no visible effect — all flagged. No gray "suspicious" zone.
- Report goes to `docs/e2e-agent-reports/<timestamp>.md`; chat gets a summary only.
- This is a single-skill-file deliverable — no scheduler, no CI wiring, no scripted Playwright test suite (that's separate, out of scope per the spec).

Spec: [docs/superpowers/specs/2026-07-21-e2e-explore-agent-design.md](../specs/2026-07-21-e2e-explore-agent-design.md)

---

### Task 1: Env template and gitignore setup

**Files:**
- Create: `.env.e2e.example`
- Create: `docs/e2e-agent-reports/.gitkeep`
- Modify: `.gitignore`

**Interfaces:**
- Produces: the `.env.e2e` file convention (`E2E_APP_URL`, `E2E_EMAIL`, `E2E_PASSWORD` keys) that Task 2's skill reads.
- Produces: `docs/e2e-agent-reports/` as the directory Task 2's skill writes reports into.

- [ ] **Step 1: Add gitignore entries**

Edit `.gitignore`, add these two lines right after the existing `.env` line:

```
.env
.env.e2e
docs/e2e-agent-reports/*.md
.venv/
venv/
```

- [ ] **Step 2: Create the env template**

Create `.env.e2e.example`:

```
# Copy to .env.e2e (gitignored) and fill in real values.
# Credentials for the dedicated test-org account on the production
# deployment. Never commit .env.e2e or paste these values into chat.

E2E_APP_URL=https://your-deployment.example.com
E2E_EMAIL=e2e-test@yourorg.example.com
E2E_PASSWORD=
```

- [ ] **Step 3: Create the reports directory placeholder**

Create `docs/e2e-agent-reports/.gitkeep` (empty file) so the directory exists in git even though its `.md` contents are ignored.

- [ ] **Step 4: Verify the ignore rules work**

Run:

```bash
touch .env.e2e docs/e2e-agent-reports/test-report.md
git check-ignore -v .env.e2e docs/e2e-agent-reports/test-report.md
```

Expected: both paths print a match against `.gitignore` (confirms they won't be committed). Then:

```bash
rm .env.e2e docs/e2e-agent-reports/test-report.md
```

- [ ] **Step 5: Commit**

```bash
git add .gitignore .env.e2e.example docs/e2e-agent-reports/.gitkeep
git commit -m "chore: add e2e-explore env template and report dir"
```

---

### Task 2: Write the e2e-explore skill

**Files:**
- Create: `.claude/skills/e2e-explore/SKILL.md`

**Interfaces:**
- Consumes: `.env.e2e` convention from Task 1 (`E2E_APP_URL`, `E2E_EMAIL`, `E2E_PASSWORD`), `docs/e2e-agent-reports/` directory from Task 1.
- Consumes: `mcp__Claude_Browser__*` tools (`preview_start`, `navigate`, `computer`, `read_page`, `get_page_text`, `find`, `form_input`, `read_console_messages`, `read_network_requests`) — already available in this environment, no setup needed.
- Produces: the `/e2e-explore` skill invocation and `docs/e2e-agent-reports/<timestamp>.md` report files, consumed by Task 3's dry run.

- [ ] **Step 1: Write the skill file**

Create `.claude/skills/e2e-explore/SKILL.md`:

````markdown
---
name: e2e-explore
description: Autonomous browser walkthrough of every route in the deployed production app, using a dedicated isolated test org, to catch broken pages and dead actions that a scripted test suite would miss. Use when the user says "/e2e-explore", "run the e2e agent", "test the app end to end", or asks for a live exploratory check of the deployed app.
---

# E2E Explore

Autonomous, on-demand walkthrough of the production app as a real user
would use it. Not a scripted regression suite — read each page, adapt to
whatever's actually rendered, use judgment about what counts as broken.

## 0. Load credentials

Read the file `.env.e2e` at the project root (plain `Read` tool, project
root relative). Parse lines of the form `KEY=VALUE`, ignoring blank lines
and lines starting with `#`. You need `E2E_APP_URL`, `E2E_EMAIL`,
`E2E_PASSWORD`.

If the file doesn't exist or any key is missing/empty: stop immediately and
tell the user to copy `.env.e2e.example` to `.env.e2e` and fill it in. Do
not proceed without real credentials.

**Never print the value of `E2E_EMAIL` or `E2E_PASSWORD` in chat output or
in the report file.** Use them only as input to the login form.

## 1. Log in

1. `mcp__Claude_Browser__preview_start` with `{url: "<E2E_APP_URL>/login"}`.
2. `read_page` to locate the email and password fields and the submit
   button.
3. `form_input` the email field with `E2E_EMAIL`, the password field with
   `E2E_PASSWORD`.
4. `computer` click the submit button.
5. `read_page` again to confirm you landed on the authenticated app (not
   still on `/login`, no error banner). If login fails, stop and report the
   failure — don't guess at credentials or retry differently.

## 2. Walk the route checklist

For **each** route below, in order:

1. `navigate` to `<E2E_APP_URL><route>`.
2. `read_console_messages` (recent) and `read_network_requests` (recent,
   filtered to the app's own API origin) to catch errors/warnings and
   4xx/5xx since navigating.
3. `get_page_text` or `read_page` to confirm real content rendered — not a
   blank area or an error-boundary message where content/data was expected.
4. If this route has a **primary action** (table below), perform it. Tag
   any text field you fill with the prefix `E2E-TEST-` followed by the
   route name and current timestamp, e.g. `E2E-TEST-items-20260721143000`,
   so test data is identifiable later. After submitting, confirm a visible
   effect (new row in a list, a success toast, navigation to a detail
   view). No visible effect after submit = a finding.
5. Record the result for this route: 🔴 with the specific evidence
   (console message text, network status + URL, or what was expected vs.
   shown), or ✅.

### Routes

```
/
/items
/boms
/procurement
/lots
/warehouse
/planning
/analytics/oee
/analytics/pace-tracking
/analytics/plant-summary
/analytics/downtime
/analytics/downtime-tracking
/analytics/throughput
/analytics/scrap
/analytics/shifts
/analytics/spc
/datasets
/sql-editor
/simulation
/ask
/bi
/bi/new
/workflows
/runs
/report-builder
/automl
/automl/advanced
/kpis
/kpis/dashboards
/integrations
/realtime
/rca-cases
/predictive
/mfg-forecast
/computer-vision
/alerts
```

Dynamic detail routes (`/datasets/:id`, `/bi/:id`, `/workflows/:id`,
`/automl/runs/:id`, `/automl/advanced/:id`) are not navigated to directly
with a made-up id — reach them naturally by clicking into a row from their
list page (`/datasets`, `/bi`, `/workflows`, `/automl`) if one exists after
the primary-action step, or skip if the list is empty and note that in the
report as informational (not a finding — an empty test org legitimately has
no rows yet on first run).

### Primary actions

| Route | Primary action |
|---|---|
| `/items` | Open the "new item" control, fill the name/required fields with `E2E-TEST-...` values, submit, confirm the item appears in the list. |
| `/boms` | Create a BOM referencing the item created above (or any existing item if that step failed), submit, confirm it's saved/listed. |
| `/procurement` | Open the create/new control for a purchase order or requisition, fill required fields with `E2E-TEST-...` values, submit, confirm it appears. |
| `/report-builder` | Build the simplest possible report/filter and run it; confirm results render (even if empty is a valid result — the action must not error). |
| `/sql-editor` | Run a trivial read-only query (e.g. `SELECT 1`); confirm a result renders and no error is shown. |
| Any other route with an obvious "New"/"Create"/"Add"/"Run" button as the page's clear primary purpose | Perform it once with `E2E-TEST-...` values; confirm visible effect. |
| Read-only/dashboard routes (analytics/\*, kpis, realtime, predictive, etc.) with no obvious create action | Load + verify content renders. No action needed. |

If a route's primary action isn't obvious within ~30 seconds of looking,
don't force one — record the route as ✅ (load-only check) and move on.
Breadth matters more here than forcing an action on every page.

## 3. Write the report

Write `docs/e2e-agent-reports/<YYYYMMDD-HHMMSS>.md`:

```markdown
# E2E Explore Report — <ISO timestamp>

Summary: <N> routes checked, <M> findings.

## Findings

### 🔴 <route path>
<what happened: console text / network status+url / expected vs actual>

### 🔴 <route path>
...

## Clean

- <route path> — ✅
- <route path> — ✅ (empty list, no rows to test primary action)
...
```

Then print a chat summary: the counts line, plus one line per 🔴 finding
(route + one-sentence cause). Full evidence lives in the file, not chat.

## Notes

- This agent mutates data in the test org freely — that's expected and
  safe, it's isolated by the app's `org_id` tenancy boundary. It does not
  clean up test data after itself.
  <!-- ponytail: no auto-cleanup; add a teardown pass if the test org gets
  cluttered enough to matter -->
- If login itself fails, or the app is unreachable, stop after step 1 and
  report that clearly — don't attempt the route checklist against a broken
  session.
````

- [ ] **Step 2: Verify the frontmatter is well-formed**

Read the file back and confirm the YAML frontmatter (`name:`, `description:`)
parses as valid — two fields, both non-empty, matching the `name: e2e-explore`
skill folder name.

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/e2e-explore/SKILL.md
git commit -m "feat: add e2e-explore autonomous testing skill"
```

---

### Task 3: Live dry run (acceptance test)

This is the real test for this feature — a skill file is only "done" once
a live `/e2e-explore` run actually produces a report. This task needs the
user's `.env.e2e` to exist with working test-org credentials; coordinate
with them before running rather than assuming it's in place.

**Files:** none (verification only, may produce a report file under
`docs/e2e-agent-reports/`, which is gitignored per Task 1 and not committed).

- [ ] **Step 1: Confirm credentials are in place**

Ask the user to confirm `.env.e2e` exists at the project root with real
`E2E_APP_URL`, `E2E_EMAIL`, `E2E_PASSWORD` values for the test-org account.
Do not proceed until confirmed.

- [ ] **Step 2: Run the skill**

Invoke `/e2e-explore`.

- [ ] **Step 3: Verify the outcome**

Confirm:
- Login succeeded (no stuck-on-`/login` failure).
- A report file was written to `docs/e2e-agent-reports/`.
- The chat summary line and report file's counts match.
- Every route in the Task 2 checklist appears in the report (either under
  Findings or Clean) — none silently skipped.

If the run surfaces a bug in the skill's own instructions (e.g. a selector
heuristic that doesn't work, a route that needs different handling), fix
`.claude/skills/e2e-explore/SKILL.md` directly and re-run this task until a
clean end-to-end pass happens.

- [ ] **Step 4: Commit any skill fixes made during the dry run**

```bash
git add .claude/skills/e2e-explore/SKILL.md
git commit -m "fix: adjust e2e-explore skill based on live dry run"
```

(Skip this step if no fixes were needed.)
