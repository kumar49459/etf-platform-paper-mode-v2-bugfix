# KiteBrokerPort API Mapping Report

## BrokerPort Interface Mapping

| BrokerPort method | Kite endpoint | Mapping notes |
|---|---|---|
| submit_order(symbol, side, quantity, limit_price, client_reference) | POST /orders/regular | LIMIT order type, CNC product, DAY validity always. client_reference encoded via encode_tag() (Decision 2) and sent as tag. Returns order_id directly. |
| get_order_status(broker_order_id) | GET /orders/:order_id | Returns Kite's FULL order history (every interim status); only the last entry is used, matching BrokerPort's "current state" contract. |
| cancel_order(broker_order_id) | DELETE /orders/regular/:order_id | Direct mapping. |
| get_open_orders() | GET /orders, filtered client-side | Kite has no separate "open only" endpoint - GET /orders returns all of today's orders (open and executed). Filtered to exclude FILLED/CANCELLED, matching PaperBrokerPort's exact semantics. REJECTED (mapped to FAILED) is NOT filtered out, matching PaperBrokerPort exactly. |
| get_available_cash() | GET /user/margins/equity | Uses the net field (Decision 4 - conservative choice over available.live_balance, not yet confirmed against a real account). |

## Status Mapping

| Kite status | Quantity condition | -> OrderLifecycleState |
|---|---|---|
| PUT ORDER REQ RECEIVED, VALIDATION PENDING, OPEN PENDING, AMO REQ RECEIVED, MODIFY VALIDATION PENDING, MODIFY PENDING, MODIFIED, CANCEL PENDING | any | PENDING |
| OPEN, TRIGGER PENDING | filled_quantity == 0 | PENDING |
| OPEN, TRIGGER PENDING | 0 < filled_quantity < quantity | PARTIALLY_FILLED |
| OPEN, TRIGGER PENDING | filled_quantity >= quantity | FILLED |
| COMPLETE | any | FILLED |
| CANCELLED | any | CANCELLED |
| REJECTED | any | FAILED |
| (anything else) | any | raises UnrecognizedKiteStatusError - fails loudly, never guessed |

## Error Taxonomy Mapping

| Kite exception / HTTP code | -> Module 28 handling |
|---|---|
| TokenException / 403 | BrokerCommunicationError, NOT retried (a dead token will never become valid mid-retry-loop) |
| MarginException | OrderRejectedError (a genuine, validly-evaluated rejection, not a communication failure) |
| InputException / 400 | OrderRejectedError (treated as a rejection at this layer; a real InputException in production likely indicates a Module 28 bug in the request itself, worth investigating even though it surfaces as a rejection) |
| HoldingException | OrderRejectedError (not currently reachable given this platform's buy-only rule; mapped for completeness) |
| NetworkException | BrokerCommunicationError, retried (transient) |
| GeneralException, DataException / 500 | BrokerCommunicationError, retried (transient) |
| 429 (rate limit) | BrokerCommunicationError, retried (transient) |
| 502, 503, 504 | BrokerCommunicationError, retried (transient) |

## Rate Limits Applied

Configured with headroom below Kite's documented current limits (architecture review, Section 3.3): 5 orders/second (vs. documented 10/sec ceiling), 200 orders/minute (vs. documented 400/min). Deliberately conservative, not maximal - and explicitly flagged as a constant to re-verify at real integration time, not a permanent fact about Kite's API (the architecture review found these limits have changed over time).

## Tag Encoding (Decision 2)

tag = hex(SHA-256(client_reference))[:20] - deterministic, alphanumeric-only, 80 bits of collision resistance from the truncated hash. Reversible via TagMappingStore, a locally-persisted append-only mapping (tag -> original client_reference), queried during reconciliation and audit. Verified against the real production cycle_id format ("2026-07-recurring_monthly", from strategy.py) - 26 characters, contains non-alphanumeric characters, and correctly encodes to a valid 20-character hex tag.
