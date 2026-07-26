# KiteBrokerPort Known Limitations and Remaining Live Validation Requirements

## Known Limitations

1. **Nothing here has been tested against a real Kite account.** Every test uses a mocked HTTP transport. This environment has no network access and no real credentials - that has been stated at every stage of this milestone and remains true for the implementation itself.
2. **The guid field's real semantics remain unverified** (architecture review, Gap 1a). If it turns out to be a genuine client-suppliable idempotency key, it would be a better mechanism than tag-based matching - not adopted here, since it cannot be verified without a real account.
3. **net vs available.live_balance remains unconfirmed** (Decision 4) - the conservative choice was made and implemented, but not cross-checked against a real account's actual figures.
4. **Rate limit constants (5/sec, 200/min) are configured with headroom below Kite's documented current values, not verified against real observed behavior.** The architecture review found these limits have changed over time historically - re-verification at integration time is required, not optional.
5. **Whether cancel_order is safe to call twice is unverified** - KiteBrokerPort does not currently guard against double-cancellation; if Kite's real behavior on a second cancel call is anything other than a clean no-op or idempotent success, this could raise unexpectedly.
6. **get_open_orders()'s underlying GET /orders call retrieves the full day's order book on every call** - for a real account with substantial order history in a day, this could be a larger payload than PaperBrokerPort's equivalent in-memory operation; performance under real load is unverified.
7. **The status mapping table is built from Kite's documented status vocabulary, which its own documentation states is not exhaustive ("there may be other values as well").** UnrecognizedKiteStatusError will fail loudly rather than silently misclassify - which is the correct safety behavior, but means a genuinely new Kite status value discovered in production will halt processing for that order until the mapping is deliberately extended, not auto-resolve.
8. **resolve_ambiguous_execution() (DDR-001) has not been tested against a KiteBrokerPort-sourced AMBIGUOUS escalation specifically** - all DDR-001 testing used PaperBrokerPort. The underlying mechanism is broker-agnostic by design (it operates on ExecutionRecord, not broker-specific types), but this specific combination is unverified.
9. **Corporate actions, dividends, and other portfolio-level events are not handled by get_holdings()/get_positions()** - these methods return Kite's raw response data (a deliberate exception to "map into existing domain models," since nothing in Module 28 currently defines a domain model for holdings/positions; they exist for future reporting/risk-check callers, not for order-lifecycle logic).

## Remaining Live Validation Requirements (Before Any Real Order)

Per the architecture review's Decision 5 validation plan, still entirely unactioned since no real account access exists:

1. **Authenticate against a real (or sandbox, if available) Kite account** - confirm the token-exchange checksum computation, the actual session lifetime, and the real access_token expiry behavior match what's documented and implemented here.
2. **Place one real order at minimal size** - confirm the actual request/response shape matches every field this implementation assumes (order_id, status, filled_quantity, average_price, tag, etc.) exactly, not just as documented.
3. **Poll that order's status through its real lifecycle** - confirm the status mapping table produces correct results against real interim states, not just the documented examples.
4. **Cancel one real order, then attempt to cancel it again** - resolve Known Limitation #5 directly.
5. **Deliberately attempt an order likely to be rejected** (e.g. insufficient funds on a small test account) - confirm the real MarginException response shape matches what submit_order() expects, and that it correctly surfaces as OrderRejectedError.
6. **Query GET /user/margins/equity and manually cross-check net against the account's own displayed "available for trading" figure** - resolve Decision 4's open confirmation.
7. **Attempt to supply a guid on order submission and observe whether Kite honors it** - resolve Gap 1a; if it works, this should replace tag-based matching as a better mechanism, requiring a follow-up design decision before adoption.
8. **Observe real rate-limit behavior under sustained calls** - confirm the 429 response shape matches what's implemented, and that the configured headroom (5/sec vs. documented 10/sec) is neither too conservative nor insufficient.
9. **Run a full day of paper-parallel operation**: submit real orders at minimal size while a parallel PaperBrokerPort-based run processes the identical Strategy Engine output, comparing outcomes - the closest approximation to validating KiteBrokerPort's real behavior against this platform's own extensively-validated paper-trading baseline before trusting it with meaningful capital.

**None of the above has been done. This document exists specifically so that gap is not glossed over by the volume of work already completed on the mocked-testing side.**
