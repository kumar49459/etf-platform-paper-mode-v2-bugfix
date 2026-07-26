# Phase 5 Objectives — Portfolio Optimizer & Risk Management Engine

**Status: IMPLEMENTED, ADVERSARIALLY REVIEWED, AND FROZEN as v0.5. 405 tests passing. See docs/PHASE5_Production_Readiness_Report.md for full verification detail, including four real defects found and fixed during the adversarial review, and RELEASE_POLICY.md for the release criteria this version was verified against.**
**Builds on:** frozen Phases 1-4 (v0.4) and approved amendments §15 (Capital-Agnostic Design) and §16 (Module 28). No frozen interface modified — verified via `git diff v0.4 --stat`.

## Binding decisions from approval (supersede the proposals below where they differ)

1. **Manual selling — strict interpretation confirmed.** The system must never initiate, recommend, schedule, or auto-execute a sell. Sells always originate from the user. Risk Management Engine may only detect risk, calculate impact, alert, recommend *protective, non-sell* actions, and activate the Kill Switch if required — it must never create or submit a sell proposal. This resolves §0 below in favor of reading (b).
2. **Portfolio Optimizer methodology — inverse-volatility approved as default, with a mandatory pluggable-method architecture** so Risk Parity, Minimum Variance, Black-Litterman, HRP, etc. can be added later without changing any other module.
3. **Constraints — explicit hard/soft split, categories specified by the user:** HARD = regulatory limits, capital protection, risk limits, the manual-selling rule, approval workflow, compliance, data quality, kill switch. SOFT = optimization-objective preferences only.
4. **Drift tolerance** ships as a provisional, clearly-flagged, configurable value — not a permanent hardcoded number — pending future backtesting/walk-forward validation to set it properly.
5. **Capital rules** — Available Investment Pool design continues exactly as §15 specifies; the optimizer must behave identically from ₹1,000 to ₹5,00,000+ with zero amount-specific logic anywhere.
6. **Monthly SIP timing** ("before the 8th of every month") is a Strategy Engine / Execution Policy concern (Phase 6), not Portfolio Optimizer's. Phase 5 calculates target allocations only — noted here for Phase 6's future reference, not implemented now.
7. **No frozen Phase 1-4 interface may be modified.** Backward compatibility maintained throughout.

---


## 0. One assumption I need resolved before anything else

Your requirement #7 says "manual selling only." I found two readings that lead to materially different scope, and I'm not picking one silently:

- **(a) Weak reading:** nothing executes without your approval — already true platform-wide via the Approval Console (§13.6). Under this reading, Risk Management Engine can *recommend* a sell (e.g., "trim GOLDBEES, it's 12 points overweight"), and you approve or reject it exactly like any other proposal.
- **(b) Strong reading:** the system never recommends a sell at all. Risk Management Engine can flag a breach ("GOLDBEES is 12 points overweight, drawdown contribution is elevated") but the *action* — whether to sell, trim, or do nothing — originates from you, not from a system-generated sell proposal.

**I've designed this document around (b), the stricter reading**, because it's the safer default to build toward and easy to relax later if you meant (a) — the reverse (discovering after the fact that the system was proposing sells you didn't want it to) would be worse. Confirm or correct this in your approval.

**Practical effect of (b):** Risk Management Engine's output on a breach is always an **alert with analysis**, never a **sell OrderIntent or a rebalance-down proposal**. Portfolio Optimizer's output can still *reduce* a target weight as part of a full reallocation (e.g., "next month's SIP should tilt more toward X, less toward Y" — a forward-looking allocation change, not an instruction to sell existing Y holdings) — new capital naturally re-weights the portfolio over time without ever selling anything. An explicit sell proposal (liquidate part of an existing position) requires you to initiate it; the system will not generate one on its own under reading (b).

---

## 1. Scope: what Phase 5 is, and deliberately is not

Per the roadmap (§9), Phase 5 is **Portfolio Optimizer + Risk Management Engine** (Cost & Tax Engine, originally also listed here, was already built in Phase 4). Reading the original module descriptions (§1.2) precisely: **Portfolio Optimizer** and **Strategy Engine** are two different modules, not one — Portfolio Optimizer computes *what the mathematically justified target weights are*; Strategy Engine (Phase 6) decides *when and how to act on them*, including the Recurring-SIP/Lump-Sum execution policies from §15.3 and rebalancing-trigger orchestration.

**Phase 5 is therefore scoped as two analytical/computational engines, callable on demand — not a live, scheduled, always-on pipeline:**

- **Portfolio Optimizer:** given a screened ETF universe, current holdings, and risk constraints, compute target weights with full explainability. A pure function, in spirit — same inputs always produce the same output.
- **Risk Management Engine:** (a) supplies the constraints Portfolio Optimizer must respect; (b) monitors current portfolio state against those constraints and against the drift/breach conditions from §12.4, emitting `risk_events` when something needs attention.

**Explicitly out of scope for Phase 5** (deferred to the phase where they actually belong, so Phase 5 doesn't quietly assume infrastructure that doesn't exist yet):
- Automatic/scheduled triggering of a new proposal (Phase 6/Scheduler — for now, both engines are invoked manually or by a test harness, not by a live daemon).
- Converting target weights into `OrderIntent`s or share quantities — that is explicitly forbidden here per §15.2's binding rule; it belongs to Strategy Engine + Module 28 (Phase 6/10/12).
- Actual Kill-Switch halt mechanics (Execution Kill-Switch / Circuit Breaker Service, §1.3) — there's no live trading to halt yet. Risk Management Engine defines the *interface* (`risk_events`, a `request_halt(reason)` call point) that Phase 12's Kill-Switch will consume; it doesn't implement the halt itself.
- Any change to Module 26 (Self-Healing Framework) sequencing — §14.6 remains open and unrelated to this phase.

---

## 2. Frozen interfaces — none modified

Checked against the actual frozen source, not assumed:

| Frozen component | Used by Phase 5 | Modified? |
|---|---|---|
| `HistoricalDataEngine.get_ohlcv/get_instrument_master` (Phase 2) | Yes — price history and metadata input | No |
| `ETFMetadataManager`, `UniverseScreeningEngine`, `ETFUniverseOptimizer`, `PortfolioCandidateGenerator` (Phase 3) | Yes — Portfolio Optimizer consumes the *screened, scored* universe as its candidate set; does not re-screen or re-score | No |
| `CostTaxEngine.compute_transaction_cost` (Phase 4) | Yes — estimating the cost/tax impact of a candidate reallocation, for the proposal's "cost and tax impact" field (§13.6) | No |
| `BacktestEngine`, `WalkForwardValidator`, `MonteCarloSimulator` (Phase 4) | Yes — validating a candidate weight scheme by backtesting a strategy that targets it | No |
| `OrderIntent`, `Strategy` interface (Phase 4) | Not used directly — Phase 5 stops at target weights, never constructs an `OrderIntent` | No |
| `backtest_runs`, `allocation_decisions` schema (§6, §13.6) | Yes — Portfolio Optimizer proposals populate `allocation_decisions`; validation runs populate `backtest_runs` | No — reuses the existing schema as designed |

**One new interface point, not a modification:** Risk Management Engine needs somewhere to write breach/drift events. `risk_events(event_id, timestamp, event_type, severity, action_taken)` already exists in the frozen §6 schema, unused until now — Phase 5 is its first real consumer. No schema change required.

---

## 3. Functional objectives

### 3.1 Portfolio Optimizer

- **F1.** Accept a candidate ETF universe (Phase 3's screened/scored output), current holdings expressed as weights, and a set of risk constraints (from Risk Management Engine); return target weights summing to <=100% (the remainder, if any, is an intentional cash reserve, never an accounting error).
- **F2.** Default methodology: **inverse-volatility weighting** (see §6.1 for why this is the default over risk parity or minimum-variance, both of which remain available as alternatives).
- **F3.** Every output weight carries a breakdown explaining *why* — which inputs drove it up or down (volatility, correlation to existing holdings, diversification value, constraint binding) — consistent with the explainability standard already set in Phase 3's `ETFScore`.
- **F4.** Respect hard constraints from Risk Management Engine (max weight per ETF, max weight per asset class) as *infeasible-to-violate*, not *checked-after-the-fact* — the optimization is constrained at solve time, and if no feasible solution exists, F5 applies.
- **F5.** On infeasibility (constraints too tight for any valid allocation to exist), refuse to produce a silently-relaxed answer — return a clear, specific explanation of which constraint(s) conflict, never a best-effort violation.
- **F6.** Handle missing ETF metadata (expense ratio, AUM, tracking error — still `null` for all six named ETFs per Phase 3's disclosed limitation) the same way Phase 3's scorer does: missing data contributes neutrally, never silently as zero or as a penalty.
- **F7.** Package a candidate allocation change as a full proposal artifact matching the Approval Console's required fields (§13.6): current portfolio, recommended portfolio, reason, expected XIRR improvement, expected drawdown impact, risk analysis, confidence score, cost and tax impact, supporting backtest summary. Portfolio Optimizer computes the allocation; the packaging step orchestrates Cost & Tax Engine and Phase 4's validation tools to fill in the rest — it does not invent these numbers itself.

### 3.2 Risk Management Engine

- **F8.** Define and expose portfolio-level constraints (max weight per ETF, max weight per asset class, target max drawdown band per §12.2's 15-20%) as structured, versioned config — not scattered magic numbers.
- **F9.** Continuously (on-demand, per §1 scope) evaluate current holdings against these constraints; on a breach, emit a `risk_events` row with severity and a plain-language description — **never** a sell proposal, per §0's resolved assumption.
- **F10.** Detect "material allocation drift" (§12.4's second rebalancing trigger) — actual weights deviating from the last-approved target beyond a configurable tolerance band. The exact tolerance is a Phase 5 parameter to be **backtested, not guessed** (§12.4 already flagged this as a Phase 5 design parameter when rebalancing was originally approved).
- **F11.** Implement the "present both options" rule (§12.2) as a concrete mechanism: when a candidate allocation's expected XIRR improves but expected max drawdown also increases beyond a configurable materiality threshold, the proposal packaging (F7) must include **both** the higher-XIRR/higher-drawdown candidate and a lower-drawdown alternative (e.g., the same candidate re-solved with a tighter drawdown constraint), never silently pick one.
- **F12.** Provide the interface point (`request_halt(reason)`) that Phase 12's Kill-Switch will eventually call — implemented in Phase 5 only as a logged `risk_events` entry with severity CRITICAL, since there's no live trading to actually halt yet.

---

## 4. Non-functional objectives

- **Determinism:** identical inputs (universe, holdings, constraints, price data snapshot) must always produce identical output weights — same reproducibility standard as Phase 4 (`config_version` + `data_snapshot_id` + code commit hash apply here too).
- **Explainability over raw performance:** an allocation that can't be explained in plain terms is not acceptable output, even if it scores well numerically — consistent with the platform's objective (Phase 1 §0) prioritizing validated, understandable decisions over rawest possible return.
- **No silent constraint violation, ever** (F4/F5) — this is treated as seriously as Phase 2's "never silently continue on critical data quality issues" and Phase 4's "never place uncertain orders."
- **Capital-agnostic** (§15) — nothing in this phase touches an absolute rupee amount. All outputs are weights.

---

## 5. Module boundaries (explicit, per the pattern already established for every module in this platform)

- **vs. ETF Universe Optimizer (Phase 3):** Phase 3 decides *which* ETFs are eligible and how they individually rank; Portfolio Optimizer decides *how much* of each to hold. Portfolio Optimizer never re-screens or re-scores an ETF — it consumes Phase 3's output as a fixed input for that run.
- **vs. Cost & Tax Engine (Module 18):** cost/tax *estimation* for a candidate reallocation is delegated entirely to `CostTaxEngine`; Portfolio Optimizer never computes brokerage, STT, or capital-gains tax itself.
- **vs. Backtesting Engine / Walk-Forward / Monte Carlo (Phase 4):** validating whether a candidate weight scheme is any good historically is Phase 4's job, invoked by the proposal-packaging step, not reimplemented.
- **vs. Approval Console (Module 25):** Portfolio Optimizer and Risk Management Engine only ever produce a *proposal*. Neither can write an `approved` status, execute anything, or bypass the Console — same non-negotiable boundary as every other module that touches allocation decisions (§13.6, §14.5, §16.2).
- **vs. Module 28 (Portfolio Cash & Execution Manager):** no interaction in Phase 5 at all — Module 28 doesn't activate until Paper/Live Trading (§16.8), and Phase 5 never produces quantities or touches cash.
- **vs. Strategy Engine (Phase 6, not yet built):** Strategy Engine will be the *caller* of both Phase 5 engines — deciding when to invoke Portfolio Optimizer (SIP timing, lump-sum timing, drift/breach triggers detected by Risk Management Engine) and translating its weight output into actual orders via the Available Investment Pool (§15). Phase 5 does not anticipate or stub this orchestration; it only ensures its own interfaces are clean enough for Phase 6 to call.

---

## 6. Key design decisions (with rejected alternatives)

### 6.1 Default methodology: inverse-volatility weighting, not risk parity or minimum-variance

**Chosen:** inverse-volatility (`weight_i proportional to 1/sigma_i`, then constrained/normalized) as the default.
**Rejected — risk parity (equal risk contribution):** more sophisticated and theoretically better-diversified, but requires the full covariance matrix, not just individual variances — meaningfully more estimation-error-sensitive with a small universe (6-20 ETFs) and limited independent market regimes in the available history. Available as a selectable alternative, not the default.
**Rejected — minimum-variance optimization:** already flagged in the original Phase 1 §5.3 decision as inheriting much of mean-variance's estimation-error fragility. Not offered as a primary method; may be used as a diagnostic/comparison tool only, the same treatment §5.3 already gave to mean-variance itself.
This is a direct continuation of the estimation-error-avoidance reasoning already locked in at §5.3 — not a new philosophy, applied one level more specifically.

### 6.2 Constraints are solved into the optimization, not checked afterward

**Chosen:** F4/F5 — infeasibility is reported explicitly, never silently relaxed.
**Rejected — solve unconstrained, then clip/rescale weights to fit constraints:** this is the common shortcut, and it's wrong for this platform's objective: a clipped-and-rescaled solution isn't the same allocation the optimizer actually judged best, and presenting it as if it were would misrepresent the methodology's own reasoning to you. If the constraints don't allow a solution, that's information you need, not something to paper over.

### 6.3 Manual invocation only, no scheduled daemon in Phase 5

**Chosen:** both engines are callable functions/services, not always-on processes.
**Rejected — building the trigger-and-schedule orchestration now:** would require assuming Phase 6's Strategy Engine design (SIP timing, lump-sum timing policy) before it exists, and would put scheduling logic in a phase whose job is quantitative methodology, not operations. Keeps Phase 5 testable in isolation and keeps the roadmap's phase boundaries meaningful.

### 6.4 Missing-metadata handling reuses Phase 3's pattern exactly

**Chosen:** the same "missing = neutral, zero contribution, explicitly noted" policy from `ETFScorer` (Phase 3).
**Rejected — inventing a different missing-data policy for Portfolio Optimizer:** two different "what do we do when we don't know something" philosophies in the same platform would be an inconsistency with no justification — the reasoning that made Phase 3's approach correct (missing data shouldn't silently bias toward either penalty or reward) applies identically here.

---

## 7. Inputs and outputs (interface sketch, not implementation)

**Portfolio Optimizer**
```
optimize(candidate_universe: list[ETFScore],           # from Phase 3
         current_holdings: dict[symbol, weight],
         constraints: RiskConstraints,                  # from Risk Management Engine
         price_history: dict[symbol, list[OHLCVBar]],   # from Phase 2, via HistoricalDataEngine
         method: OptimizationMethod = INVERSE_VOLATILITY)
    -> OptimizationResult(target_weights, explanation_breakdown, feasible: bool, infeasibility_reason: str | None)
```

**Risk Management Engine**
```
get_constraints() -> RiskConstraints
evaluate(current_holdings, price_history, last_approved_weights) -> list[RiskEvent]
request_halt(reason: str) -> None   # writes a CRITICAL risk_event; no live effect until Phase 12's Kill-Switch exists
```

---

## 8. Dependencies

Phase 2 (`HistoricalDataEngine`), Phase 3 (all four modules), Phase 4 (`CostTaxEngine`, `BacktestEngine`, `WalkForwardValidator`, `MonteCarloSimulator`), the frozen `allocation_decisions`/`risk_events`/`backtest_runs` schema (§6). No new external dependency beyond what Phase 3/4 already introduced (numpy/scipy) — this stays research-side per §12.1, same placement rationale as `etf_optimizer` and `backtesting`.

---

## 9. Failure scenarios and recovery strategy

| Scenario | Behavior |
|---|---|
| Infeasible constraint set | Refuse, explain which constraints conflict (F5) — never silently relax |
| Singular/degenerate covariance or volatility estimate (e.g., too little price history for one ETF) | Exclude that ETF from this run with a clear reason, do not crash and do not silently substitute a guessed value |
| Candidate universe is empty (Phase 3 screened everything out) | Refuse to produce a proposal; explain that no eligible candidates exist |
| Validation step (Phase 4 backtest/walk-forward) fails or times out | Proposal is not packaged/submitted; the failure is reported, not swallowed |
| Risk Management Engine detects a breach mid-evaluation and Portfolio Optimizer is mid-computation | No shared mutable state between the two in this phase (both are stateless/pure per call) — no race condition is possible by construction, not by locking |

This is intentionally a smaller failure surface than Phase 4's — Phase 5 has no live execution, no persistent state of its own, and no scheduled process yet, so most of Module 26's detection categories (process crashes, stuck heartbeats, network failures) don't apply here. What *does* apply (infeasibility, missing data, degenerate math) is handled by explicit refusal and clear explanation, consistent with the platform's fail-safe default (Phase 1 §1.4).

---

## 10. Testing strategy

- **Unit tests, hand-verified:** inverse-volatility math against known small examples (2-3 assets with hand-computable weights), constraint-feasibility edge cases, missing-metadata neutrality (mirroring Phase 3's `test_missing_metadata_contributes_zero_not_penalty` pattern).
- **Integration tests:** full pipeline — Phase 3 screened universe -> Portfolio Optimizer -> Cost & Tax Engine impact estimate -> Phase 4 walk-forward validation -> packaged proposal artifact matching the Approval Console's exact required fields.
- **Regression tests:** a locked-value baseline (same discipline as Phase 4's `test_backtest_regression.py`) — a fixed universe/constraint/price-history scenario with hardcoded expected output weights, so silent methodology drift is caught immediately.
- **Adversarial pass before freeze:** consistent with the precedent set at the end of Phase 4, Phase 5 will get its own deliberate "try to break it" review before being considered done — candidates already on my list: a single-ETF universe (degenerate case), perfectly correlated ETFs (does the optimizer handle a singular correlation matrix gracefully), a constraint set that's *barely* feasible (does F4/F5's boundary behave correctly), and extremely short price history (does F6's missing-data handling kick in correctly at the threshold).

## 11. Performance targets

- Portfolio Optimizer: a full solve over a 20-50 ETF candidate universe completes in well under 5 seconds on the research instance (no live-latency requirement exists yet — this is an on-demand analytical tool, not a real-time system).
- Risk Management Engine's breach/drift evaluation: sub-second for a portfolio of the scale this platform targets (single-digit to low-double-digit number of holdings).
- No target for the live/micro instance in this phase, since Phase 5 code doesn't run there (research-side placement, §8/§12.1).

## 12. Security considerations

No new attack surface: this phase introduces no new secrets, no new network calls, no new external data source. The only sensitivity is the same one every proposal-generation step already has — proposal artifacts flow through the existing one-way S3 handoff (§13.1) with the same trust direction already established; Phase 5 doesn't change that mechanism, it produces content that flows through it.

---

## 13. Production-readiness and exit criteria (before this phase can be considered done)

1. All functional objectives (§3) implemented and tested.
2. Full test suite passing, including the locked regression baseline and the adversarial review pass (§10).
3. A documented production-readiness report in the same format as Phases 2-4, including honest disclosure of any remaining limitations (I expect at least one: the drift-tolerance-band parameter in F10 needs actual backtesting against historical data to set sensibly, not a guessed default — this may mean Phase 5 ships with a provisional value explicitly flagged as "needs empirical tuning," rather than blocking the whole phase on a number that's hard to get right without more data than currently exists).
4. Demonstrated end-to-end against your real six ETFs: a full run from Phase 3's screened universe through a validated, packaged proposal artifact, even though (per Phase 3's own disclosed limitation) AUM/expense-ratio/tracking-error remain unpopulated for all six.
5. Explicit confirmation from you on §0 (manual-selling-only interpretation) before this document is considered fully approved.
6. No modification to any frozen Phase 1-4 interface (§2) — verified, not assumed, the same way §15.4 and the Module 28 boundary decisions were verified against actual source rather than asserted from memory.

---

## 14. Summary of items requiring your explicit approval

- **§0** — confirm the strong reading of "manual selling only" (Risk Management Engine never proposes a sell), or correct me if you meant the weaker reading.
- **§6.1** — confirm inverse-volatility as the default methodology (with risk parity as a selectable alternative, minimum-variance as diagnostic-only).
- **§3.1/F4** — confirm you want hard (infeasible-to-violate) constraints rather than soft/penalized ones.
- **§9/13.3** — accept that the drift-tolerance-band parameter (F10) may ship as a provisional, explicitly-flagged value pending empirical backtesting, rather than blocking the phase.
- Everything else in this document is a design decision, not a question — consistent with how §14/§15/§16 were structured.
