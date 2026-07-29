# Operator Identity Capture

Status: Approved for planning
Date: 2026-07-29

Depends on `2026-07-29-operator-registry-design.md`, which must ship first —
every tier here resolves against `Operator` and `resolve_or_create_operator`
rather than storing identity of its own. This spec is in turn a prerequisite
for the live operator board spec that follows it. Supersedes this family of
specs' earlier reliance on Pace Tracking, which has been removed from the
product.

## Problem

The product direction is sensor-first: machine state comes from sensors, with
dataset upload remaining a fully supported path for customers without them.
Sensors solve none of the identity question, and were never going to — a
vibration or current sensor on a machine has no way to know who is standing
at it. That is a physical fact about what a sensor measures, not a gap this
codebase happens to have.

Manual identity capture used to come from Pace Tracking's session-start
screen, which is being removed. Removing it without replacing what it
incidentally provided reopens the exact hole: nothing in the system can
answer "who is running that machine" for a customer using sensors.

Customers' shop floors differ in what identity hardware they actually have.
Some have badge or RFID readers already, for access control or WIP scanning.
Some don't, but allow operators to use their own phones. Some allow neither,
and the only thing that exists is a production schedule that says who was
*supposed* to be where. Forcing one capture method onto all three is either
an unfunded hardware mandate or a feature that silently doesn't work for a
third of deployments.

## Scope

In scope: a machine-scoped capture event that answers "who was at this
machine, starting when"; three producers of that event (badge/RFID reader,
QR-code-plus-phone, work-order assignment as a weaker inferred fallback); and
one attribution query that anything downstream (the live operator board, a
future sensor-driven OEE-by-operator view) can call without knowing which
tier actually supplied the answer.

Out of scope (explicit decisions, not oversights):

- **Building reader or kiosk hardware, or a native phone app.** This spec is
  the ingestion endpoint and the QR check-in page. Which physical readers a
  customer buys and wires up is an integration/support conversation, the same
  posture the product already takes toward `RealtimeStream` sensor sources.
- **Continuous presence tracking.** A capture event marks a start. It does
  not detect someone leaving early or a phone being left near a machine, the
  same honesty limitation already accepted for badge-style capture generally:
  it records who was *selected/scanned in*, not verified continuous presence.
- **The live operator board UI itself.** That consumes the attribution query
  this spec defines. It remains its own spec, deferred, to follow.
- **Retiring Pace Tracking's code.** Removing `pace_tracking.py` and its
  table is a separate, destructive action with its own blast radius (dropped
  table, removed routes, removed tests) and gets its own explicit go-ahead
  when requested, not a side effect of this spec.

## Design

### The attribution model

A machine has, at any moment, at most one open capture event: `operator_id`,
`machine_id`, `started_at`, `ended_at` (nullable while open), `source`
(`"reader"` or `"qr"`). A new check-in on a machine closes whatever was open
on that machine and opens the new one. There is no explicit checkout,
matching the same simplicity `PaceSession` used for its own start/stop, minus
the throughput-tap machinery this product no longer wants.

Work-order assignment produces no event row at all. It is a live join
performed at query time: for a machine and a time window, if a `WorkOrder`
assigned to an operator was `in_progress` and its routing step used that
machine's work center during that window, attribute to that operator. This
stays a query rather than a stored event because a work order's assignment
can change after the fact (reassigned before it starts), and a capture event
is supposed to mean "this specific thing happened," not "this was true as of
whenever the schedule last changed."

`resolve_attribution(machine_id, window) -> Operator | None`:

1. Look for a capture event (reader or QR) overlapping the window. If found,
   that is the answer — a real scan beats an inference every time.
2. Otherwise, look for a `WorkOrder.assigned_operator_id` covering that
   machine and window.
3. Otherwise, `None`. The operator dimension for that window is simply
   unpopulated, the same graceful-absence behaviour the rest of this
   product's optional-column design already uses.

### Tier 1: badge/RFID reader

`Operator` (from the registry spec) gains a nullable `badge_id` (String,
unique per org), set by a customer once per person, the same one-time cost as
naming a `Machine`.

`POST /operators/capture` takes `{badge_id, machine_id, scanned_at}`. It
resolves `badge_id` to an `Operator` in that org, 404s if unrecognized rather
than guessing, and opens a capture event, closing whatever was previously
open on that machine. This is the integration surface a reader (or the small
edge device driving one) calls; it does not go through `autoMapColumns` or
any dataset flow, since it is a live event, not a file.

### Tier 2: QR code plus phone

Each `Machine` gets a stable check-in URL, `/checkin/{machine_id}`, shown as
a "Print QR" action next to it on the Machines panel of the Production page.
Scanning it opens a page listing the org's operator roster (from the
registry) to tap; tapping a name calls the same capture-event creation as
tier 1 with `source = "qr"`. First-time-on-this-phone, the browser remembers
the choice (local storage), so a returning operator's next scan is one tap
with no list to search.

Creating a name that is not on the roster from this screen is allowed and
routes through `resolve_or_create_operator`, exactly as dataset auto-discovery
does, so a new hire is not blocked from checking in on day one.

### Tier 3: schedule fallback

`WorkOrder` gains a nullable `assigned_operator_id` (FK to `operators.id`),
editable wherever work orders are already edited. No new endpoint; this is
one more optional field on an existing form. It participates only in
`resolve_attribution`'s query, producing no event history of its own, which
is the honest reflection of what it actually is: a plan, not an observation.

### Why not one universal mechanism

Requiring every customer to buy a reader would gate the whole feature behind
a purchase most won't make. Requiring QR-only would fail every floor that
restricts personal phones. Requiring schedule-only would give every customer
the weakest, plan-not-actual signal even when they have a real reader
sitting on the wall. The three tiers exist because the constraint genuinely
varies per deployment, and `resolve_attribution` hides that variance behind
one call so nothing downstream needs to know which tier answered.

## Testing

- A reader scan with an unrecognized `badge_id` returns 404 and creates no
  event.
- A second scan on the same machine closes the first event (`ended_at` set)
  before opening the new one.
- `resolve_attribution` prefers an overlapping capture event over a matching
  work-order assignment when both exist for the same window.
- `resolve_attribution` falls back to the work-order assignment when no
  capture event overlaps the window.
- `resolve_attribution` returns `None`, not an error, when neither exists.
- A QR check-in for a name not on the roster creates the operator via
  `resolve_or_create_operator` and the capture event in the same call.
- Cross-org isolation: a badge_id valid in one org resolves nothing in
  another; a machine_id from another org 404s.

## Open questions

None blocking. Whether `ended_at` ever needs an explicit checkout (versus
always being closed implicitly by the next check-in) is worth revisiting once
real usage shows how often a machine sits idle with a stale open event
between jobs.
