# Milestone 6 — Live Broker Integration (Kite Connect): Architecture Review and Design Document

**Status: REVIEW ONLY. No implementation code has been written.** Per your instruction, this document identifies gaps before any `KiteBrokerPort` code exists - one of them is serious enough that I am stopping and reporting it, not working around it, exactly as instructed.

## 0. Research basis

Everything below is grounded in Kite Connect's official documentation (`kite.trade/docs/connect/v3/`), fetched directly in this session - not recalled from training data, which would be unreliable for an API surface that changes over time (confirmed: rate limits and the static-IP requirement have visibly changed even within the forum history I read). Where sources conflicted, both are reported, not silently resolved.

## 1. The critical finding - stated first, not buried

**Module 28's existing crash-recovery mechanism (Milestone 3), as currently designed, is unsafe for real Kite Connect and would risk a duplicate real-money order under a specific, realistic crash timing.**

The mechanism: `ReconciliationService._reconcile_one()` handles a record with no local `broker_order_id` by searching `get_open_orders()` for a match by `client_reference`. If found, it adopts the broker's order; if not found, it concludes the order never reached the broker and reverts it to `VERIFIED` for a safe retry.

This works correctly against `PaperBrokerPort` because `PaperBrokerPort.get_open_orders()` and Kite's real "open orders" concept were assumed to behave the same way. Kite's own documentation confirms they do - an order that has reached `COMPLETE` status is no longer "open." **The gap**: if a real order is submitted, actually fills at the exchange, and *then* the process crashes before the `broker_order_id` is recorded locally, the next restart's reconciliation will search `get_open_orders()`, find nothing (the order is no longer open - it's complete), conclude `NEVER_REACHED_BROKER`, and retry the submission. **This creates a genuine second, duplicate real order for money that has already been spent.**

This is not a hypothetical - it is the literal scenario Milestone 3's own regression test (`test_the_critical_checkpoint_broker_has_it_but_local_record_does_not_know_yet`) exists to prevent, and it currently passes only because `PaperBrokerPort.get_open_orders()` was never tested against a *terminal* (filled) order in that exact crash window the same way a real Kite order legitimately can be.

**I am not proposing a fix in this document.** A fix would require either (a) extending `BrokerPort`'s interface with a method that searches *all* of today's orders, not just open ones (`GET /orders` returns both), or (b) some other mechanism - both are architecture changes, and you've explicitly instructed me not to change the Execution Manager architecture and to stop and report gaps rather than work around them. This is the report.

## 2. Kite Connect API - what was verified against official documentation

### 2.1 Order placement
`POST /orders/:variety` - returns only `order_id` immediately. Kite's own docs state explicitly: **"Successful placement of an order via the API does not imply its successful execution."** This matches `BrokerPort.submit_order()`'s existing contract exactly (returns a `broker_order_id`, caller must poll or reconcile for true status) - no gap here.

### 2.2 No true idempotency key exists at the API level
Searched specifically for this, since it was already flagged as the platform's highest-risk unknown. **Confirmed: there is no idempotency-key parameter.** The closest thing is `tag` - an optional, caller-supplied string (**max 20 alphanumeric characters**), which Kite does **not** use for deduplication. Submitting two orders with the same tag creates two real orders; `tag` is for the caller's own filtering, not broker-enforced idempotency. There is also a `guid` field in the order response, described only as "Unusable request id to avoid order duplication" - this is broker-generated and returned, not something the caller supplies to prevent a duplicate submission.

**Consequence**: Module 28's crash-recovery design already correctly does *not* rely on broker-side idempotency - it relies on client-side matching via `client_reference` (Milestone 3). That was the right call. But `client_reference` maps to Kite's `tag`, and `tag`'s 20-character alphanumeric constraint does not fit the current `cycle_id` format (e.g. `"2026-07-recurring_monthly"` - 25 characters, contains a hyphen and underscore). **A second, smaller, concrete gap**: `cycle_id`/`client_reference` values need a Kite-compatible encoding (e.g. a fixed-length hash) before they can be used as Kite `tag` values at all.

### 2.3 Order status is an open-ended value space, not a closed enum
Kite's documentation lists common statuses (`OPEN`, `COMPLETE`, `CANCELLED`, `REJECTED`) plus several transient ones (`PUT ORDER REQ RECEIVED`, `VALIDATION PENDING`, `OPEN PENDING`, `MODIFY VALIDATION PENDING`, `MODIFY PENDING`, `TRIGGER PENDING`, `CANCEL PENDING`, `AMO REQ RECEIVED`, `MODIFIED`) - **and states explicitly that "there may be other values as well."**

`OrderLifecycleState` (Milestone 1) is a closed, 9-value enum. A mapping function from Kite's open-ended status strings to this closed enum is required, and **that mapping function must have a defined, safe behavior for a status string it doesn't recognize** - not raise, not silently default to a misleading state. This is a real design decision to make explicitly, not an incidental detail.

### 2.4 No distinct "partially filled" status
Kite represents a partial fill via the `filled_quantity` / `pending_quantity` integer fields on an order whose `status` remains `OPEN` - there is no `PARTIALLY_FILLED` string value in Kite's status space at all. `OrderLifecycleState.PARTIALLY_FILLED` must be *derived* (`status == "OPEN" and 0 < filled_quantity < quantity`), not read directly off a status field the way `PaperBrokerPort`'s internal simulation does it.

**Proposed concrete mapping** (subject to real-account verification before implementation is considered complete):

| Kite status | filled_quantity vs quantity | → OrderLifecycleState |
|---|---|---|
| `PUT ORDER REQ RECEIVED`, `VALIDATION PENDING`, `OPEN PENDING`, `AMO REQ RECEIVED` | n/a | `PENDING` |
| `OPEN`, `TRIGGER PENDING` | `filled_quantity == 0` | `PENDING` |
| `OPEN`, `TRIGGER PENDING` | `0 < filled_quantity < quantity` | `PARTIALLY_FILLED` |
| `COMPLETE` | `filled_quantity == quantity` | `FILLED` |
| `CANCELLED` | any | `CANCELLED` |
| `REJECTED` | any | `FAILED` |
| `MODIFY*`, `CANCEL PENDING` | any | `PENDING` (no dedicated Module 28 state for "modification in flight" — treated as still-pending; this platform doesn't currently modify orders post-submission, so this row is a completeness note, not an active code path) |

This is the structural difference worth designing carefully rather than discovering as a bug during implementation: the mapping function is `(status_string, filled_quantity, quantity) -> OrderLifecycleState`, not `status_string -> OrderLifecycleState`.

### 2.5 The order book is transient - today only
Kite's own documentation: "The order history or the order book is transient as it only lives for a day in the system." `GET /orders` returns only the current day's orders (open, executed, and cancelled, all included - this is what Section 1's fix would need to query). Cross-day history requires `GET /orders/:order_id`, whose actual retention window I could not confirm from the documentation and would need to verify directly against the API before depending on it. **`ExecutionStateStore` (Module 28, frozen behavior since Milestone 1) remains the platform's only genuinely long-term source of truth - Kite is authoritative only for what it currently reports, on a timescale that isn't fully documented.**

### 2.6 Rate limits - current, with a disclosed historical inconsistency
Current official documentation (`kite.trade/docs/connect/v3/exceptions/`, and Zerodha's own FAQ page): **10 orders/second, 400 orders/minute, 5,000 orders/day** per account (429 response on breach). Multiple older forum discussions (2021-2023) cite different, lower figures (3-5/second) - these appear superseded by the current official docs, not simultaneously true; flagged so a future reader isn't confused by the historical discussion still visible in forum search results. **Order-submission rate limiting is currently untested in Module 28** - `PaperBrokerPort` has no rate-limit simulation at all. A real integration needs client-side rate limiting *before* hitting Kite's 429, not just handling 429 reactively.

### 2.7 Mandatory static IP for order placement, since April 1, 2025
Confirmed via Zerodha's own FAQ. Order-placement requests specifically must originate from a registered static IP (data/quote endpoints are exempt). **This was already anticipated correctly** - `MinimalInlineComplianceChecker`'s static-IP check (Milestone 3 design, `PHASE7_Objectives.md` Decision 3) exists for exactly this. No gap; a validating finding, not a new one.

### 2.8 Authentication is a genuinely manual, once-daily human step

Confirmed, not contradicted: the login flow requires a human to authenticate through a browser redirect (`https://kite.zerodha.com/connect/login?v=3&api_key=xxx`, including 2FA per standard Kite account security) and produces a `request_token`, exchanged for an `access_token` that Kite's own docs state **"will expire at 6 AM the next day (regulatory requirement)"** unless explicitly logged out first — some forum-reported inconsistency in the exact expiry time exists (see 2.8a below), but the regulatory framing is the documented, authoritative statement.

A `refresh_token` exists for extending a session without the full interactive flow, but the documentation states it is **"only available to certain approved platforms"** — not standard developer-tier access. This means continuous, unattended operation — exactly what Milestone 5B and Production Verification validated for paper trading — is **not achievable for live trading** on the standard API tier without either (a) a human completing an interactive browser login once daily, or (b) an automated headless-browser login scripting credential entry and 2FA.

**I am not going to build (b).** Scripting entry of trading credentials and 2FA is a genuine security concern, and very likely violates Kite's own terms of service (the API's stated purpose explicitly excludes "fully automated trades," per `kite.trade/terms` point E, found during this research). Working around this gap with a fragile, credential-scripting automation is exactly the kind of thing the instruction to stop and report gaps rather than work around them exists to prevent.

**This confirms, rather than changes, the platform's existing documented assumption** (`PHASE7_Objectives.md`: "token refresh is a manual runbook step, not yet automated") — not a new gap, but a materially more precise one now: "continuous operation" for live trading has a genuinely lower ceiling than continuous operation for paper trading, not just an unautomated convenience. The Operational Runbook needs a live-trading-specific startup section stating this plainly once implementation begins.

### 2.8a Token expiry time — a minor, disclosed inconsistency

Independent of the "6 AM, regulatory requirement" statement in Kite's own docs, several Zerodha support forum threads report observed expiry anywhere from 6:00 AM to 7:30 AM, with one official-sounding forum response stating tokens generated after 7:30 AM remain valid the full day. Flagged as a real inconsistency between the documentation and operator-reported experience, not resolved by picking one — the practical implication is unaffected either way: token validity does not survive a calendar-day boundary.

### 2.8b `get_available_cash()` maps to an ambiguous choice of two fields

`GET /user/margins/equity` exposes at least two candidate fields for "available cash": `net` (computed as intraday_payin + adhoc_margin + collateral, per the API's own field semantics) and `available.live_balance` ("current available balance"). These are not documented as equivalent, and which one `VerificationService`'s affordability check should treat as authoritative is a real design decision, not an assumption to make silently. **Recommendation**: use `net` as the more conservative of the two pending direct observation against a real account, since an affordability check that's too optimistic risks a rejected order at submission time, while one that's too conservative only costs a smaller position size — the asymmetry favors conservatism here.

### 2.9 Postbacks exist and are Kite's own recommended mechanism for non-market orders - in tension with this platform's architecture
Kite's docs recommend postbacks (webhooks) over polling for orders that may stay open indefinitely, since continuous polling is "impractical." **This is in direct tension with the frozen architecture's Section 17 (Event-Driven Resource Optimization: idle by default, no continuous polling, short-lived processes).** A postback receiver requires a persistently-running HTTP server, which the platform's current design deliberately does not have. **Recommendation, not yet a decision**: continue with the already-built polling model (`BrokerPort.get_order_status()`), consistent with Section 17 and with everything already tested against `PaperBrokerPort`, and treat postbacks as a future enhancement requiring its own architecture review - not adopt them silently as part of this milestone.

### 2.10 Order modification exists at the API but has no `BrokerPort` equivalent
`PUT /orders/:variety/:order_id` allows modifying an open order's quantity/price/type. `BrokerPort` (Milestone 1) has no `modify_order()` method. **Assessed, not treated as blocking**: this platform's strategy is buy-only, simple limit orders (Phase 5/6's binding manual-selling rule) - order modification does not appear to be a real requirement for the current strategy, so its absence from `BrokerPort` is very likely correct as-is, not an oversight. Flagged for your explicit confirmation rather than assumed.

### 2.11 Error taxonomy (from `kite.trade/docs/connect/v3/exceptions/`)

Not covered above — the exception/error-code structure, useful for mapping Kite failures onto Module 28's existing exception vocabulary without inventing a new one:

| Kite exception | HTTP code | → Module 28 handling |
|---|---|---|
| `TokenException` | 403 | Session expired/invalidated — the precise, unambiguous signal to trigger re-authentication (Section 2.8). Fail loudly, never silently retry against a dead token. |
| `NetworkException` | (varies) | Kite's own docs describe this as "the API was unable to communicate with the OMS" — explicitly, textually, the ambiguous-outcome case Section 1's finding and Module 28's reconciliation design both exist for. Maps to `BrokerCommunicationError` (existing, unmodified). |
| `MarginException` | (via `OrderException` family) | Insufficient funds — a genuine rejection (validly evaluated and declined), not a communication failure. Maps to an `OrderRejectedError`-equivalent, not `BrokerCommunicationError`. |
| `HoldingException` | (via `OrderException` family) | Insufficient holdings for a sell — not reachable given this platform's buy-only manual-selling rule (frozen, Phase 5/6); included for completeness only. |
| `InputException` | 400 | Bad request parameters — almost always a Module 28 bug (malformed request), not an environmental condition; should fail loudly in development, not be silently retried in production. |
| `GeneralException`, `DataException` | 500 | Unclassified/internal Kite error — treat as `BrokerCommunicationError`, transient and retry-worthy. |
| (rate limit) | 429 | Maps to rate-limit handling (Section 2.6) — back off and retry per the documented figures. |
| (infra down) | 502, 503, 504 | Backend OMS/API unreachable — `BrokerCommunicationError`, transient, retry-worthy. |

Also confirmed from the same page: order **modification** is capped at 25 attempts per order (not currently relevant — see 2.10), and per-endpoint rate limits are more granular than the order-placement figure alone: Quote 1 req/sec, Historical candle 3 req/sec, Order placement 10 req/sec, all other endpoints 10 req/sec.

## 3. What Remains Genuinely Unverified

Stated together, not scattered, so nothing gets lost:

1. Real network failure behavior under actual internet conditions (timeouts, partial responses, connection resets) — the docs describe the API's *intended* behavior, not its behavior under real-world network stress, which is exactly what Module 28's crash-recovery design most needs to be tested against.
2. Whether `cancel_order` is safe to call twice (idempotency of cancellation itself is undocumented).
3. Which of `net` vs `available.live_balance` is the correct field for `VerificationService`'s affordability check (Section 2.8b) — recommending `net` pending real-account observation, not asserting it as confirmed.
4. Whether `tag` is server-side filterable in `GET /orders` or requires a full client-side scan of the day's order book — likely fine either way given this platform's low order volume, but an assumption, not a confirmed fact.
5. Real behavior of the original 8 assumptions in `PHASE7_Objectives.md`'s Broker Capability Matrix — this review has now confirmed some (idempotency: confirmed absent; static IP: confirmed mandatory since April 1, 2025) and sharpened others (rate limits: confirmed but with a disclosed historical inconsistency) rather than fully resolved all of them.
6. The `guid` field's actual purpose (Section 2.2) — genuinely ambiguous from documentation text alone, needs direct account verification.

## 4. Kite Concept to BrokerPort Mapping Table

| `BrokerPort` method | Kite Connect equivalent | Notes |
|---|---|---|
| `submit_order()` | `POST /orders/:variety` | Returns `order_id` only; true status unknown at return time - matches existing contract |
| `get_order_status(broker_order_id)` | `GET /orders/:order_id` | Returns an array (full status history) - `BrokerPort`'s contract expects current state; needs the *last* entry, not the whole history, unless the design is deliberately extended to expose history (not proposed here) |
| `cancel_order()` | `DELETE /orders/:variety/:order_id` | Mapping itself is straightforward; whether calling it twice on an already-cancelled order is safe is undocumented (Section 3, item 2) - flagged for real-account verification before relying on it in a retry path |
| `get_open_orders()` | `GET /orders`, filtered to non-terminal `status` values | **This is the exact method implicated in Section 1's critical finding** - Kite's version of "all of today's orders" is broader than "open" ones, and the crash-recovery matching logic needs the broader set |
| `get_available_cash()` | `GET /user/margins/equity` | Two candidate fields (`net` vs `available.live_balance`) with different definitions - see Section 2.8b. Recommending `net` as the more conservative choice, not yet confirmed against a real account. |
| *(no equivalent)* | `modify_order()` | See Section 2.10 - likely correctly absent, needs your confirmation |

## 5. Summary of Gaps Requiring Your Decision

1. **[CRITICAL, Section 1]** - `get_open_orders()`-based crash recovery is unsafe for a specific real crash timing (crash after a real fill, before local recording). Needs a decision: extend `BrokerPort`, or some other resolution. Not workable within "no architecture changes."
2. **[Concrete, Section 2.2]** - `client_reference`/`cycle_id` values need a Kite-`tag`-compatible encoding (20 alphanumeric characters or fewer).
3. **[Design decision needed, Section 2.3]** - the Kite-status-to-`OrderLifecycleState` mapping function's behavior for an unrecognized status string needs to be explicitly decided, not left implicit.
4. **[Design decision needed, Section 2.4]** - partial-fill detection must be derived from `filled_quantity`/`pending_quantity`, not read from a status enum value, unlike `PaperBrokerPort`'s internal design.
5. **[Open question, Section 2.5]** - the actual retention window of `GET /orders/:order_id` beyond the current day is undocumented and needs direct verification against the real API before Module 28's cross-day reconciliation can depend on it for anything Kite-side.
6. **[New capability needed, Section 2.6]** - client-side rate limiting (10/sec, 400/min, 5000/day) does not exist anywhere in Module 28 today; `PaperBrokerPort` has never needed to simulate it.
7. **[Recommendation pending your confirmation, Section 2.9]** - continue with polling (`get_order_status()`), do not adopt postbacks, given the tension with frozen Section 17.
8. **[Assessment pending your confirmation, Section 2.10]** - `modify_order()`'s absence from `BrokerPort` is very likely correct given the buy-only strategy, not an oversight.
9. **[Operational constraint, not a code defect, Section 2.8]** - daily interactive re-authentication (2FA) is a regulatory requirement, not an automatable inconvenience; `refresh_token` is restricted to approved platforms only. Live trading's "continuous operation" ceiling is genuinely lower than paper trading's — needs your acknowledgment as an accepted operational constraint before implementation, since no code-level fix exists.
10. **[Design decision needed, Section 2.8b]** - `get_available_cash()` has two non-equivalent candidate fields (`net` vs `available.live_balance`); recommending `net` as the conservative default, needs your confirmation or a real-account observation to settle it properly.

## 6. Recommendation

**Do not proceed to `KiteBrokerPort` implementation until Section 1's critical finding is resolved.** Everything else in this document (tag encoding, status mapping, partial-fill derivation, rate limiting) is real, necessary implementation work, but none of it is unsafe on its own - Section 1 is the one finding where proceeding without a decision risks a duplicate real order with real money behind it. This is exactly the category of thing you asked me to stop and report rather than work around.
