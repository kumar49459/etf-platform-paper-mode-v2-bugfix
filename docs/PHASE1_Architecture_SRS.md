# Institutional-Grade ETF Investment Platform (India)
## Phase 1 — Architecture & Software Requirements Specification

**Status:** DRAFT — awaiting your approval. No code will be written until you sign off on this document.

---

## 0. Critical Design Challenge (Read This First)

You asked me to challenge the design rather than rubber-stamp it. One requirement conflicts directly with the objective, and I'm flagging it before anything else because it shapes every module below.

### The EC2 Micro problem

**t2.micro / t3.micro** = 1 vCPU (burstable), 1 GB RAM, no local NVMe, burst credits that throttle under sustained load.

This platform includes: a historical data engine ingesting years of OHLCV for the full Indian ETF universe, a portfolio optimizer (likely mean-variance / Black-Litterman / risk-parity, all matrix-heavy), an AI dynamic allocation engine, a vectorized/event-driven backtester running walk-forward validation across many parameter sets, a dashboard, live trading with a WebSocket feed from Kite, and monitoring — all on one machine.

Concretely, on a 1 GB RAM instance:
- A pandas DataFrame holding 5 years of daily OHLCV for ~250 Indian ETFs is small (~50–100 MB), so **data storage is fine**.
- But a walk-forward backtest that loads that data, runs an optimizer per window, and evaluates multiple candidate strategies **will spike RAM well past 1 GB** the moment you vectorize across parameter grids — which is exactly how you avoid overfitting (you need many out-of-sample windows, not one). You'll hit OOM kills or swap-thrash a burstable instance into uselessness.
- The **live trading + WebSocket tick listener must never be starved of CPU**. If a backtest or optimizer job runs on the same core at the same time, you risk delayed order placement — a real money-losing bug, not a cosmetic one.
- t2/t3.micro burst credits **deplete under sustained load** (e.g., a nightly backtest run). Once depleted, CPU is throttled to ~10% baseline, which can stall live trading if the schedules overlap.

**My recommendation:** Separate compute by workload, not by trying to fit everything on one box.

| Workload | Why it doesn't belong on micro | Recommendation |
|---|---|---|
| Live trading + Telegram + lightweight dashboard read | Must be always-on, low RAM, latency-sensitive but not CPU-heavy | **Keep on t3.micro** (or t4g.micro — ARM Graviton, ~20% cheaper, fine for Python) |
| Backtesting, optimization, walk-forward validation, AI allocation training | CPU/RAM-bursty, runs on a schedule, not latency-sensitive | Run on a **separate instance spun up on-demand** (t3.medium/large or a Spot instance), or as a scheduled **AWS Batch / Fargate task**. Costs a few dollars per run, then shuts down. |
| Historical data storage | Needs to persist, doesn't need compute | **SQLite on EBS or S3 + Parquet**, not RDS (RDS is overkill and costs more than the whole rest of this stack) |

This is a **cost-neutral or cost-reducing change** — you already need a micro for 24/7 live trading; you just don't run heavy jobs on it. I'll formalize this in the AWS Architecture section (§7) as **"AWS Micro-Anchored, Burst-Compute"** architecture. If you'd rather force everything onto a single micro for cost reasons, I'll document the degraded-mode fallback, but I don't recommend it — it directly threatens the "no overfitting, robust validation" objective, since you'll be tempted to cut backtest scope to fit RAM.

**I need your decision on this before Phase 2 coding starts.** Everything else below is designed either way; only §7 changes.

---

## 1. Software Requirements Specification (SRS)

### 1.1 Objective (restated precisely, so we don't drift later)

Maximize **validated, out-of-sample, risk-adjusted XIRR** for a long-term Indian ETF portfolio, subject to:
- Drawdown control (explicit max drawdown target, TBD by you — I recommend we set this number explicitly in Phase 1 sign-off, e.g. "never exceed X% peak-to-trough on a rolling basis," because "control drawdown" without a number is not testable).
- No overfitting: every strategy/allocation decision must survive walk-forward and out-of-sample validation, not just in-sample backtest performance.
- Extensibility beyond the 6 named ETFs to the full Indian ETF universe, with additions/removals justified by evidence, not preference.

### 1.2 Functional Requirements (by module)

1. **Historical Data Engine** — ingest, clean, adjust (dividends/splits/bonus), and store daily (and optionally intraday) OHLCV + AUM + expense ratio + tracking error for all listed Indian ETFs; detect and handle corporate actions; version data so backtests are reproducible against a data snapshot.
2. **ETF Universe Optimizer** — screen the full Indian ETF universe (NSE-listed) on liquidity, tracking error, expense ratio, AUM, bid-ask spread, index overlap; output a candidate universe with justification; flag redundant/overlapping ETFs (e.g., two ETFs tracking near-identical indices).
3. **Portfolio Optimizer** — construct target weights from the candidate universe using a defensible methodology (see §5.3 for the methodology decision I recommend and why).
4. **Strategy Engine** — encodes rebalancing rules, entry/exit logic, tax-aware lot selection (India has STCG/LTCG rules that materially affect ETF holding decisions), and glide-path logic if any.
5. **AI Dynamic Allocation Engine** — a *constrained, explainable* model (not a black-box return predictor) that adjusts allocation tilts within bounds set by the Portfolio Optimizer, retrained/revalidated on a strict walk-forward schedule, with an audit trail of every allocation change and its justification.
6. **Backtesting Engine** — event-driven (not just vectorized) backtester supporting walk-forward and Monte Carlo resampling, transaction costs, slippage, STT, and realistic order-fill assumptions.
7. **Risk Management Engine** — position sizing, max drawdown circuit breakers, correlation/concentration limits, volatility targeting, kill-switch for live trading.
8. **Performance Analytics** — XIRR, CAGR, Sharpe/Sortino, max drawdown, Calmar, rolling metrics, benchmark-relative attribution.
9. **Dashboard** — read-only visualization of portfolio state, performance, and risk; must not share compute with live trading.
10. **Paper Trading** — full order lifecycle simulation against live market data without real capital, using the same code path as live trading (critical: paper and live must share an execution interface so paper-trading results are actually predictive of live behavior).
11. **Live Trading** — order placement/management via Zerodha Kite Connect, with reconciliation against actual fills.
12. **AWS Deployment** — see §7.
13. **Telegram Notifications** — trade alerts, error alerts, daily/weekly summaries, drawdown breach alerts.
14. **Logging and Monitoring** — structured logs, health checks, alerting on failures (data pipeline failure, order failure, API downtime).
15. **Configuration Manager** — versioned, environment-aware config (dev/paper/live), no hardcoded secrets.
16. **Reporting Engine** — periodic PDF/HTML reports (performance, tax, rebalancing rationale).
17. **Symbol Resolution Engine for Zerodha Kite** — maps internal ETF identifiers to Kite instrument tokens, handles symbol changes/relistings.

### 1.3 Additional modules I recommend adding (with justification)

These aren't in your list but are required for the platform to be "institutional-grade" and safe with real capital. I'm flagging them now rather than discovering the gap mid-build.

| Proposed module | Why it's needed | What breaks without it |
|---|---|---|
| **Data Quality & Validation Engine** | Distinct from the Data Engine itself — a gatekeeper that checks for stale prices, impossible jumps, missing corporate action adjustments *before* data reaches the optimizer/backtester | A single bad tick (e.g., a 10x price glitch from a bad feed) can silently corrupt an optimization or trigger a live trade |
| **Cost & Tax Engine (India-specific)** | STT, stamp duty, exchange transaction charges, GST on brokerage, STCG (20%) vs LTCG (12.5% post-2024 rules) on ETF units — these materially change what "optimal" rebalancing frequency is | Backtest XIRR will be overstated vs. real net-of-tax, net-of-cost XIRR — directly undermines your stated objective |
| **Secrets & Credentials Manager** | Distinct from Configuration Manager — Kite API keys/tokens need rotation, encryption at rest, and must never touch logs or git | A leaked API key in a log file or repo is a live-capital-loss risk, not a hypothetical |
| **Execution Kill-Switch / Circuit Breaker Service** | A standalone watchdog that can halt all live order placement independent of the Risk Engine (defense in depth — if the Risk Engine itself has a bug, you still want a hard stop) | Without an independent kill switch, a bug in the Risk Engine has no backstop |
| **Walk-Forward / Overfitting Validation Framework** | You asked to "avoid overfitting" — this needs to be a first-class module (not a feature buried in the Backtesting Engine) that enforces train/validation/test splits, tracks how many strategy variants were tried (multiple-testing correction), and rejects strategies that don't survive out-of-sample | Without an explicit module enforcing this, overfitting avoidance becomes a vague intention rather than a gate every strategy must pass through |

I'll treat these as **Module 18–22** in the roadmap. Tell me if you want to cut, merge, or defer any of these — I'd push back hard on cutting the Cost & Tax Engine and the Walk-Forward Validation Framework specifically, since they're load-bearing for your stated objective.

### 1.4 Non-Functional Requirements

- **Reproducibility:** every backtest result must be traceable to an exact data snapshot + code commit hash + config version.
- **Auditability:** every live order and every allocation change must be logged with the reasoning that produced it.
- **Fail-safe default:** on any uncertainty (data gap, API failure, ambiguous signal), the system defaults to *no action*, never a guessed action.
- **Cost ceiling:** infrastructure cost should stay in the range appropriate for a personal/small institutional deployment (I'll give concrete numbers in §7).
- **Separation of concerns:** research/backtesting code and live-trading code must share the same core logic (strategy, risk, execution interfaces) so that what's validated in backtest is what actually runs live — this is a common institutional failure point (backtest and live diverging silently).

---

## 2. High-Level Architecture

```
                        ┌─────────────────────────────┐
                        │   Historical Data Sources    │
                        │  (NSE, AMFI, Kite Historical) │
                        └───────────────┬──────────────┘
                                        │
                        ┌───────────────▼──────────────┐
                        │   Historical Data Engine       │
                        │   + Data Quality Validator      │
                        └───────────────┬──────────────┘
                                        │  (versioned Parquet/SQLite)
                ┌───────────────────────┼───────────────────────┐
                │                       │                       │
    ┌───────────▼───────────┐ ┌────────▼─────────┐ ┌───────────▼───────────┐
    │  ETF Universe Optimizer │ │ Portfolio Optimizer│ │  Walk-Forward Validation│
    └───────────┬───────────┘ └────────┬─────────┘ │      Framework          │
                │                       │            └───────────┬───────────┘
                └───────────┬───────────┘                        │
                            │                                    │
                ┌───────────▼───────────┐            ┌───────────▼───────────┐
                │   Strategy Engine       │◄──────────┤ AI Dynamic Allocation  │
                │                        │            │       Engine            │
                └───────────┬───────────┘            └────────────────────────┘
                            │
                ┌───────────▼───────────┐
                │  Backtesting Engine     │
                │  (event-driven + MC)     │
                └───────────┬───────────┘
                            │
                ┌───────────▼───────────┐
                │  Risk Management Engine │
                │  + Cost & Tax Engine     │
                └───────────┬───────────┘
                            │
                ┌───────────▼───────────┐
                │   Approval Console       │◄── you review & decide
                │  (Approve/Reject/        │    (Approve/Reject/Postpone/
                │   Postpone/Request       │     Request more analysis)
                │   more analysis)          │
                └───────────┬───────────┘
                            │  (only on status='approved')
        ┌───────────────────┼───────────────────┐
        │                   │                   │
┌───────▼────────┐ ┌────────▼────────┐ ┌────────▼────────┐
│  Paper Trading   │ │  Live Trading    │ │ Performance      │
│                  │ │  + Kill Switch   │ │ Analytics         │
└───────┬────────┘ └────────┬────────┘ └────────┬────────┘
        │                   │                   │
        │          ┌────────▼────────┐          │
        │          │ Symbol Resolution │          │
        │          │  Engine (Kite)     │          │
        │          └────────┬────────┘          │
        │                   │                   │
        └───────────────────┼───────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
┌───────▼────────┐ ┌────────▼────────┐ ┌────────▼────────┐
│    Dashboard     │ │ Telegram Notify  │ │ Reporting Engine │
│  (read-only,     │ │  (points to the  │ │                  │
│   no execution   │ │   Approval       │ │                  │
│   access)         │ │   Console)        │ │                  │
└─────────────────┘ └─────────────────┘ └─────────────────┘

  Cross-cutting: Configuration Manager | Secrets Manager | Logging & Monitoring
```

---

## 3. Folder Structure

```
etf-platform/
├── config/
│   ├── base.yaml
│   ├── paper.yaml
│   ├── live.yaml
│   └── secrets.env.example        # real secrets never committed
├── data/
│   ├── raw/                       # immutable, as-ingested
│   ├── processed/                 # cleaned, adjusted, versioned (Parquet)
│   └── snapshots/                 # named data-as-of snapshots for reproducible backtests
├── src/
│   ├── data_engine/
│   ├── data_quality/
│   ├── universe_optimizer/
│   ├── portfolio_optimizer/
│   ├── strategy_engine/
│   ├── ai_allocation/
│   ├── backtesting/
│   ├── validation/                # walk-forward / overfitting framework
│   ├── risk_management/
│   ├── cost_tax_engine/
│   ├── performance_analytics/
│   ├── execution/
│   │   ├── paper/
│   │   ├── live/
│   │   └── kill_switch/
│   ├── symbol_resolution/
│   ├── notifications/
│   ├── reporting/
│   ├── dashboard/                 # strictly read-only, no execution access
│   ├── approval_console/          # the only human-decision write path; gates live_trading
│   ├── config_manager/
│   ├── secrets_manager/
│   └── logging_monitoring/
├── infra/
│   ├── terraform/ (or CDK)        # EC2, IAM, security groups, S3, batch jobs
│   └── scripts/
├── tests/
│   ├── unit/
│   ├── integration/
│   └── backtest_regression/       # locked historical results, alert on drift
├── notebooks/                     # research only, never imported by src/
├── docs/
└── pyproject.toml
```

**Key design decision:** `notebooks/` is explicitly isolated from `src/` — research code never gets imported into production paths. This is a common institutional-platform failure mode (a notebook prototype quietly becomes a production dependency). I'll enforce this with a lint rule in Phase 2.

---

## 4. Module Design (interfaces, not implementation)

I'm keeping this at the interface/contract level per your "no code yet" instruction.

- **Historical Data Engine** exposes `get_ohlcv(symbols, start, end, snapshot_id=None)` and `get_corporate_actions(symbol)`. Internally pluggable data sources (NSE bhavcopy, AMFI, Kite historical API) behind a common adapter interface, so we're not locked to one vendor.
- **Data Quality Validator** sits between the Data Engine and everyone else — nothing downstream reads raw data directly.
- **Portfolio Optimizer** takes a candidate universe + constraints (max weight per ETF, max sector/asset-class concentration, drawdown target) and returns target weights + the methodology's confidence/robustness metrics, not just a single "optimal" point estimate.
- **Strategy Engine and AI Allocation Engine both implement the same `Allocator` interface** (`propose_weights(portfolio_state, market_state) -> weights, rationale`), so the Backtesting Engine and Live Trading system can swap between them without code changes. This is what makes paper/live/backtest consistent.
- **Execution interface** (`place_order`, `get_positions`, `reconcile`) is implemented identically by `paper/` and `live/`, differing only in whether orders hit Kite or a simulator. This directly serves your "avoid divergence between backtest and reality" goal.
- **Risk Management Engine** is called synchronously before every order (paper or live) — it's a gate, not a report.

---

## 5. Key Design Decisions (with rejected alternatives)

### 5.1 Data storage: SQLite + Parquet, not PostgreSQL/RDS
**Chosen:** SQLite for transactional/state data (positions, orders, config), Parquet files (local or S3) for time-series OHLCV.
**Rejected:** RDS Postgres — adds ~$15-30+/month minimum, a network hop, and operational overhead (backups, connection pooling) for a workload that's fundamentally single-writer, batch-oriented, and fits in a few hundred MB. RDS makes sense once you have multiple concurrent writers or need real-time multi-user access — you don't, at this scale.

### 5.2 Backtesting: event-driven, not purely vectorized
**Chosen:** Event-driven backtester (processes bar-by-bar or tick-by-tick) as the primary engine, with a vectorized engine as a fast pre-screening tool only.
**Rejected:** Vectorized-only backtesting — it's fast but silently permits look-ahead bias (e.g., using a day's close to decide that same day's rebalance) and can't realistically model order sequencing, partial fills, or same-day risk breaches. Since your objective explicitly prioritizes validated, non-overfit results over raw speed, event-driven is the correct default; vectorized is a screening tool, not the source of truth.

### 5.3 Portfolio construction: risk-based (risk parity / min-variance with constraints), not naive mean-variance
**Chosen:** Start with a risk-based approach (inverse-volatility or risk-parity weighting with correlation constraints), layer tactical tilts from the AI Allocation Engine within bounds.
**Rejected:** Classic Markowitz mean-variance optimization as the primary driver — it's notoriously sensitive to estimation error in expected returns (small changes in return assumptions cause large weight swings), which is a direct overfitting risk for a 6-9 ETF universe with limited independent historical regimes. Risk-based methods are more stable out-of-sample, which matters more than in-sample optimality here. We can still use mean-variance as one input/diagnostic, not the sole allocator.

### 5.4 AI Allocation Engine: constrained/explainable, not an unconstrained return predictor
**Chosen:** The AI engine adjusts tilts within bounds set by the Portfolio Optimizer (e.g., ±5% around a risk-parity baseline) and must output a human-readable rationale per decision.
**Rejected:** An unconstrained ML model predicting returns/positions directly — with ~6-20 ETFs and maybe 10-15 years of usable daily data, there isn't enough independent signal to train a reliable unconstrained predictive model without overfitting to noise. A constrained "tilt within bounds" model is falsifiable and auditable, which matches your explicit "avoid overfitting" and (implicit but necessary) "institutional auditability" requirements.

### 5.5 Symbol resolution: dedicated module, not inline in execution
**Chosen:** Standalone Symbol Resolution Engine, refreshed daily from Kite's instrument dump, with a manual-override table for edge cases (relistings, symbol changes).
**Rejected:** Hardcoding instrument tokens in config — Kite instrument tokens can change; hardcoding creates a silent live-trading failure mode when a token goes stale.

---

## 6. Database Schema (high-level; DDL comes in Phase 2)

**Time-series store (Parquet, partitioned by symbol/year):**
- `ohlcv(symbol, date, open, high, low, close, volume, adjusted_close, data_snapshot_id)`
- `corporate_actions(symbol, ex_date, action_type, ratio_or_amount, source)`
- `etf_metadata(symbol, name, index_tracked, expense_ratio, aum, inception_date, tracking_error, as_of_date)`

**Transactional store (SQLite):**
- `portfolio_state(as_of_date, symbol, quantity, avg_cost, market_value)`
- `orders(order_id, timestamp, symbol, side, quantity, price, status, mode[paper/live], kite_order_id, rationale_ref)`
- `allocation_decisions(decision_id, proposal_id, timestamp, allocator[strategy/ai], proposed_weights_json, rationale, source_commit_hash, status[pending/approved/rejected/postponed/analysis_requested], decided_at, decided_by, rejection_reason, postpone_until, analysis_request_notes)`
- `backtest_runs(run_id, code_commit_hash, config_version, data_snapshot_id, start_date, end_date, metrics_json)`
- `risk_events(event_id, timestamp, event_type, severity, action_taken)`
- `tax_lots(lot_id, symbol, buy_date, quantity, buy_price, sell_date, sell_price, gain_type[STCG/LTCG])`

**Design decision:** `backtest_runs` stores the commit hash and data snapshot ID explicitly — this is what makes results reproducible and defensible later ("why did we choose this allocation in March 2027" needs to be answerable).

---

## 7. AWS Architecture

### 7.1 Recommended: Micro-Anchored, Burst-Compute (see §0)

- **t3.micro or t4g.micro** (always-on): live trading engine, Telegram bot, lightweight dashboard (static/read-only, e.g., served via a small Flask app reading pre-computed state — not recomputing analytics on request), logging agent.
- **On-demand/scheduled compute** for backtesting, optimization, AI model retraining: options ranked by simplicity —
  1. **Simplest:** a second EC2 instance (t3.medium, Spot pricing) started by a cron/Lambda trigger, runs the job, self-terminates. Cheapest for infrequent (weekly/monthly) heavy runs.
  2. **More managed:** AWS Batch or Fargate task, same idea, less manual instance lifecycle management, slightly higher cost.
- **S3**: data lake for Parquet historical data + backtest artifacts + reports.
- **EBS**: small volume on the micro for SQLite + logs, with scheduled snapshots to S3.
- **CloudWatch**: logs + alarms (disk usage, process health, live-trading heartbeat).
- **IAM**: least-privilege roles per component; the live-trading instance should not have permissions to spin up other instances.
- **Secrets Manager or SSM Parameter Store** (not plain env files) for Kite API credentials.

### 7.2 Estimated cost (India/ap-south-1, approximate)

- t3.micro (or t4g.micro) 24/7: ~$6-8/month (t4g cheaper, ARM-compatible with Python/pandas).
- On-demand t3.medium for backtests, ~10 hrs/month: ~$1-2/month.
- S3 + EBS + CloudWatch: a few dollars/month.
- **Total: roughly $10-15/month** — cheaper than trying to force everything onto a single micro and fighting OOM kills, when you count the cost of your own debugging time.

### 7.3 Fallback (single-micro, if you override §0)
If you decide to keep everything on one t2/t3.micro regardless: I'll design the Backtesting Engine to process data in small chunked batches with explicit memory limits, restrict walk-forward validation to a reduced window count, and hard-schedule backtests only during hours markets are closed so they never contend with live trading. I'll document this as a formal constraint, but I want to flag clearly: this reduces validation robustness, which works against your stated objective. Your call.

---

## 8. Data Flow (summary)

1. Nightly: Data Engine pulls latest EOD data → Data Quality Validator checks it → written to a new immutable snapshot in `data/processed/`.
2. On schedule (e.g., weekly): Universe Optimizer re-screens the ETF universe against the latest snapshot → produces a candidate list with diffs vs. previous list, flagged for your review (not auto-applied).
3. On schedule: Portfolio Optimizer + Walk-Forward Validation Framework jointly produce/validate target weights against the candidate universe; only weights that pass validation gates are eligible to reach the Strategy/AI Allocation Engine.
4. Strategy Engine / AI Allocation Engine propose actual trade instructions → Risk Management Engine gates them → Cost & Tax Engine estimates net impact → routed to Paper or Live execution based on config.
5. Execution results reconciled → Performance Analytics updates → Dashboard reads pre-computed state → Telegram notified → Reporting Engine generates periodic reports.

---

## 9. Development Roadmap

Each phase requires your explicit approval before the next begins, per your instruction.

- **Phase 1 (this document):** Architecture & SRS — awaiting approval.
- **Phase 2:** Historical Data Engine + Data Quality Validator + Configuration/Secrets Manager (foundation everything else depends on).
- **Phase 3:** ETF Universe Optimizer (apply to your 6 ETFs + full universe, produce first real evidence-based recommendations).
- **Phase 4:** Backtesting Engine (event-driven) + Walk-Forward Validation Framework (built together, since the validation framework must constrain the backtester from day one, not bolted on later).
- **Phase 5:** Portfolio Optimizer + Risk Management Engine + Cost & Tax Engine.
- **Phase 6:** Strategy Engine (rule-based baseline) — get a full, validated, rule-based system working end-to-end before touching AI.
- **Phase 7:** AI Dynamic Allocation Engine (only after Phase 6 gives us a validated baseline to beat).
- **Phase 8:** Performance Analytics + Reporting Engine.
- **Phase 9:** Symbol Resolution Engine + Paper Trading (shared execution interface).
- **Phase 10:** Dashboard + Telegram Notifications + Logging/Monitoring.
- **Phase 11:** AWS Deployment (infra as code).
- **Phase 12:** Live Trading (smallest possible capital, extended paper-trading track record required first — I'll propose a minimum paper-trading duration/criteria before Phase 12 starts).

**Design decision:** AI Allocation Engine is deliberately late (Phase 7, after a rule-based baseline exists). Rejected alternative: building AI allocation early/in parallel — without a validated rule-based baseline to compare against, we'd have no way to tell if the AI engine is actually adding value or just adding noise/overfitting risk.

---

## 10. Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| EC2 micro resource exhaustion during backtesting | Live trading disruption, OOM crashes | Burst-compute separation (§7.1) |
| Overfitting to a small ETF universe / limited history | False confidence in strategy, real-money losses | Dedicated Walk-Forward Validation Framework as a hard gate, multiple-testing tracking |
| Kite API/token/symbol changes | Live trading failure | Dedicated Symbol Resolution Engine, daily refresh, alerting on resolution failures |
| Backtest/live divergence | Strategy that looked good never performs as validated | Shared `Allocator` and execution interfaces across paper/live/backtest |
| Data quality issues (corporate actions, splits, bad ticks) | Corrupted optimization/backtest results | Data Quality Validator as a mandatory gate before any downstream use |
| Tax/cost blind spots in backtest | Overstated net XIRR | Cost & Tax Engine integrated into every backtest run, not just live trading |
| Secrets leakage | Real capital loss | Secrets Manager module, no plaintext credentials in repo/logs |
| Single point of failure (one instance, one region) | Downtime during live trading | CloudWatch health alarms + Telegram alerting on heartbeat failure; accept that full HA is disproportionate for this scale, but detection must be fast |
| Silent strategy decay (regime change) | Strategy stops working without anyone noticing | Performance Analytics includes rolling out-of-sample comparison against the original backtest expectation, alert on significant deviation |

---

## 11. Open Questions for You (need answers before Phase 2)

1. **Drawdown target:** what's the explicit max acceptable drawdown (e.g., 15%? 20%?) — needed to make "control drawdown" testable.
2. **EC2 architecture (§0/§7):** approve the burst-compute split, or force single-micro with documented degraded validation?
3. **Capital scale for live trading:** affects position-sizing granularity and whether fractional/lot-size constraints matter much.
4. **Rebalancing frequency preference**, if you have one, or should the Strategy Engine determine it empirically?
5. **Do you want the 5 additional modules (§1.3) included in the roadmap**, or should I cut/defer any?
6. **Data source access:** do you have (or want me to plan for) a paid historical data source, or should Phase 2 rely on NSE bhavcopy archives + Kite historical API (rate-limited, free-tier)?

---

---

## 12. Phase 1 — Approved Decisions (Signed Off)

These decisions are now binding for all subsequent phases. Any future change to them requires explicit re-approval, since they affect module contracts already designed above.

### 12.1 AWS Architecture — APPROVED: Split, Micro-Anchored
- **Micro instance (always-on):** Live Trading Engine, Scheduler, SQLite database, Telegram Notifications, Logging & Monitoring, Configuration Manager, Dashboard (read-only), **Approval Console (Module 25 — see §13.6)**.
- **On-demand instance / local machine (burst, as-needed):** Backtesting Engine, Strategy Optimization, Walk-Forward Validation Framework, Monte Carlo simulation, AI research/training.
- **Binding constraint carried into Phase 2 design:** the live trading engine's runtime dependencies must stay minimal (no heavy numerical libraries loaded into the live process beyond what order placement/risk-gating strictly needs) so it never competes for the micro's limited RAM/CPU. Backtesting and research code must be fully decoupled — callable independently, not imported into the live process. The Approval Console is likewise kept dependency-light: it renders already-computed proposal artifacts (§13.1) rather than performing analysis itself.

### 12.2 Risk Management — APPROVED
- **Max portfolio drawdown target: 15–20%** (peak-to-trough, rolling). This becomes a hard constraint in the Risk Management Engine and a gate in the Walk-Forward Validation Framework — any strategy/allocation whose validated out-of-sample drawdown exceeds this range is rejected or flagged, not silently accepted because XIRR looked good.
- **Capital preservation priority:** when a candidate strategy improves XIRR but materially worsens drawdown, the platform must **never auto-select the higher-XIRR option**. Performance Analytics and the Walk-Forward Validation Framework will present both options side-by-side (XIRR, drawdown, Sharpe/Sortino, Calmar) with an explicit trade-off explanation, and require your manual approval before either is adopted. This is now a formal requirement on the reporting output of both modules, not just a conversational habit.

### 12.3 Capital Scale — APPROVED: Size-Agnostic Design
- No module may hardcode absolute rupee amounts, fixed lot counts, or capital-tier-specific logic. Position sizing, rebalancing thresholds, and risk limits are all expressed in **percentage/weight terms**, converted to actual order quantities only at execution time.
- **Design implication carried forward:** the Strategy Engine and Execution layer must handle fractional-weight-to-whole-unit rounding gracefully at any capital size (this matters more at small capital, where ETF lot/price granularity can meaningfully distort target weights — the platform must detect and report this distortion rather than silently absorb it).

### 12.4 Rebalancing — APPROVED: Intelligent Event-Driven (No Fixed Calendar Rebalancing)
Rebalancing triggers **only** on:
1. **Risk limit breach** (e.g., drawdown approaching the 15–20% band, concentration/correlation limit breach) — evaluated by the Risk Management Engine.
2. **Material allocation drift** (actual weights deviate from target beyond a defined tolerance band — the exact tolerance is a Phase 5 design parameter, to be backtested rather than guessed).
3. **AI-validated improvement** — a proposed reallocation from the AI Dynamic Allocation Engine that has passed the Walk-Forward Validation Framework's statistical significance gate (not just "looks better in-sample").
4. **Annual scheduled review** — a mandatory once-a-year check even if none of the above triggered, so the portfolio is never left un-reviewed for more than 12 months.

**Design implication carried forward:** the Strategy Engine's interface needs an explicit `should_rebalance(trigger_type, evidence) -> bool` decision point with a logged rationale (feeds the `allocation_decisions` table already in §6) — rebalancing is never a silent/implicit side effect of a scheduled job running.

**Rejected alternative (noted for the record):** fixed periodic (e.g., monthly/quarterly) rebalancing — explicitly rejected per your instruction. This also reduces unnecessary turnover, which directly helps the Cost & Tax Engine's objective (avoiding STCG-triggering trades and transaction cost drag that a rigid calendar schedule would otherwise force).

### 12.5 Additional Modules — APPROVED (Full List)
All six of your listed modules are approved and added to the module inventory and roadmap:
- **Module 18: Cost & Tax Engine**
- **Module 19: Monte Carlo Simulation Engine** — runs return-path resampling (bootstrap/block-bootstrap on historical returns) to stress-test a candidate portfolio beyond the single historical path already covered by the Backtesting Engine. Distinct from Walk-Forward Validation: walk-forward asks "does this survive out-of-sample time"; Monte Carlo asks "does this survive alternate plausible histories." Both are needed — one without the other leaves a real validation gap.
- **Module 20: Performance Attribution Engine** — decomposes realized/backtested XIRR into contribution by ETF, by allocation decision (baseline vs. AI tilt), and by rebalancing event, so "why did we get this return" has an evidence-based answer, not a guess.
- **Module 21: Walk-Forward Validation Framework**
- **Module 22: Portfolio Audit Engine** — periodic automated reconciliation of actual holdings (from Kite) vs. the platform's internal state, plus a full historical audit trail of every decision, order, and override. This is what makes the platform trustworthy with real capital over years, not just at launch.
- **Module 23: Decision Explanation Engine** — generates the human-readable rationale attached to every allocation/rebalancing decision (feeds `allocation_decisions.rationale` in §6), consumed by the Reporting Engine and Dashboard. Distinct from Performance Attribution: attribution explains past *returns*; this explains past *decisions* — including ones that turned out to be wrong, which matters for institutional-grade trust.

**One additional module I'm adding, not requested but legally load-bearing (verified via current search, since this is a 2026 regulatory fact, not something I'd trust from training data alone):**

- **Module 24: Compliance & Regulatory Engine** — SEBI's retail algo trading framework is now fully in force. It requires (a) a static IP, registered and whitelisted with the broker, for all API-based order placement; (b) a unique Algo ID tag attached to every order sent to the exchange; (c) staying under a broker/exchange-prescribed order-rate threshold (Zerodha's own API limit is 10 orders/second) or registering the strategy with the exchange if that's exceeded; (d) a multi-year audit trail. This module sits directly in front of the Execution layer alongside the Risk Management Engine: it validates the static IP at startup, stamps every order with the registered Algo ID, hard-enforces the order-rate ceiling client-side (never relying on the broker's rejection as the only safety net), and feeds the Portfolio Audit Engine's trail. It also confirms our `ap-south-1` (Mumbai) region choice in §7 is not just a latency optimization but now a broker-preferred compliance posture.

**Final approved module count: 24.** Full renumbering will be reflected in the Phase 2+ design docs; §1.2/§1.3 above remain the conceptual source of truth, with 18–24 as the confirmed extension set.

### 12.6 Data Source — APPROVED: NSE Primary, Kite Secondary, Provider-Abstracted
- **Primary:** NSE historical data, including bhavcopy archives where applicable.
- **Secondary:** Zerodha Kite Historical API + Kite Instrument Master (for symbol/token resolution, feeding Module 17 directly).
- **Mandatory Data Engine behaviors (binding, not aspirational):**
  1. Rate-limit handling — respect and back off against both NSE and Kite API limits; never hammer a source into a soft-ban.
  2. Missing-data detection — explicit gap-detection per symbol/date, not silent forward-fill.
  3. Symbol-change detection — cross-checked against the Kite Instrument Master and logged as an event, feeding both the Data Quality Validator and Module 17 (Symbol Resolution Engine).
  4. Validation and cleaning — every ingested batch passes through the Data Quality Validator (§1.3) before it's usable by any other module.
  5. Every data quality issue is logged — not just failures; near-misses (e.g., a stale-but-not-missing price) get logged too, since patterns in near-misses are often the earliest warning of a worse problem.
  6. **Critical missing data halts the pipeline** — if data required for an active strategy/risk decision is missing, the platform must refuse to proceed (fail-safe default, per §1.4 NFRs) rather than substitute an estimate or stale value silently.
- **Provider abstraction (binding design constraint):** the Data Engine exposes a single internal interface (`get_ohlcv`, `get_corporate_actions`, `get_instrument_master`, as sketched in §4) implemented by pluggable source adapters (`NSEAdapter`, `KiteAdapter`, and future `PaidVendorAdapter`). No module outside the Data Engine may import a source-specific client directly. This means adding a paid data provider later is a new adapter class plus a config change — zero changes to the Universe Optimizer, Backtesting Engine, or any downstream consumer. This was already implicit in §4's design; it's now an explicit, binding requirement rather than an assumption.

---

---

## 13. Pre-Code Architecture Review (Gate Before Phase 2)

Per your instruction, before starting Phase 2 I reviewed the full document adversarially — checking sections against each other, not just re-reading each in isolation. This surfaced real gaps that wouldn't have been visible until mid-implementation. Resolutions below are now binding, same as §12.

### 13.1 Cross-instance handoff protocol (previously undefined) — RESOLVED
**Problem:** §12.1 splits compute across the live micro and an on-demand/local instance, but no mechanism moved a validated result between them.
**Chosen:** An **S3 "handoff bucket"** with a fixed structure: the on-demand/local instance writes a signed artifact (`proposal_id`, target weights, full validation metrics from the Walk-Forward and Monte Carlo engines, code commit hash, data snapshot ID) to `s3://<bucket>/proposals/`. A lightweight poller in the micro's Scheduler checks this path on a schedule, pulls new proposals into the local SQLite `allocation_decisions` table with `status = 'pending'`, and triggers the approval flow (§13.6, Approval Console). Nothing on the on-demand side ever talks to Kite or touches live state directly — it only ever produces a proposal artifact.
**Rejected:** Direct SSH/API calls from the research instance into the live micro — this would mean the security-critical live instance has to accept inbound connections or credentials from a machine that's spun up and torn down regularly (larger attack surface, harder to audit). A one-way, pull-based, artifact-based handoff is safer and matches the "live instance stays isolated" principle already agreed in §12.1.

### 13.2 SQLite concurrency — RESOLVED
**Chosen:** SQLite in **WAL (Write-Ahead Logging) mode**, with a single dedicated writer process per logical domain (e.g., only the Live Trading Engine writes to `orders`/`portfolio_state`; only the Compliance & Audit Engine writes to its own audit tables), and all other components reading via WAL's concurrent-read support. Cross-domain writes go through a thin internal queue rather than direct concurrent writes to the same table.
**Rejected:** Default SQLite rollback-journal mode — locks the whole database file on write, which is fine for infrequent backtesting workloads but not for a live process with multiple always-on writers (Scheduler, Compliance Engine, Logging). WAL is a config-level change, not an architecture change, so this doesn't affect §5.1's original SQLite-vs-Postgres decision — it refines the "how," not the "what."

### 13.3 Version consistency across split compute — RESOLVED
**Chosen:** Every proposal artifact from §13.1 carries the exact git commit hash it was produced with (already planned for `backtest_runs` in §6 — now extended to every proposal, not just backtest runs). The live micro **refuses to execute** a proposal whose commit hash doesn't match the commit hash currently deployed on the live instance. If code has moved on since the proposal was generated, the proposal is auto-rejected with a clear log reason, not silently executed against mismatched logic.
**Rejected:** Trusting that "the code probably hasn't changed" — for a long-running platform you'll actively develop over years, this assumption fails eventually, and it fails silently, which is the worst way for it to fail.

### 13.4 CI / pre-deployment test gate — RESOLVED
**Chosen:** A deployment pipeline step (mechanics finalized in Phase 11, AWS Deployment) that blocks any code from reaching the live micro unless `tests/unit`, `tests/integration`, and `tests/backtest_regression` all pass against it first. `backtest_regression` specifically re-runs a locked historical scenario and fails the deploy if results drift beyond a defined tolerance — this catches the case where a "small refactor" silently changes strategy behavior.
**Rejected:** Manual pre-deploy checklist — relies on discipline holding for years without lapse; an automated gate doesn't get tired or skip a step under time pressure.

### 13.5 Compliance Engine external prerequisite — RESOLVED (roadmap dependency, not a design change)
**Finding:** Module 24 (Compliance & Regulatory Engine) assumes a static IP is already registered and whitelisted with the broker, and an Algo ID is already issued. Both require a manual registration process with the broker/exchange (per §12.5's Module 24 description) that takes real calendar time and isn't something the platform can automate away.
**Resolution:** Added as an explicit **roadmap dependency**: this registration process must be *initiated* well before Phase 12 (Live Trading) begins — realistically alongside Phase 9-10, since we don't know the broker's processing time. I'll flag this again as we approach Phase 9 so it doesn't become a surprise blocker right when you're ready to go live.

### 13.6 Dashboard vs. Approval workflow — RESOLVED: Separate Approval Console (Module 25)

**Decision:** The Dashboard stays strictly read-only and independent from the execution layer, exactly as originally specified in §1.2. A new, separate module — **Module 25: Approval Console** — owns all human-in-the-loop decisions. It is the only component (besides the Live Trading Engine itself) with write access to the `allocation_decisions` table, and it is the **sole gate** the Live Trading Engine checks before executing any portfolio change.

**Approval Console — required content per proposal (binding):**
- Current portfolio (live, from `portfolio_state`)
- Recommended portfolio (from the proposal artifact, §13.1)
- Reason for the recommendation (sourced from the Decision Explanation Engine, Module 23 — the Approval Console renders it, it doesn't generate it, keeping explanation logic in one place)
- Expected XIRR improvement, expected drawdown impact, risk analysis, confidence score (sourced from Performance Attribution / Walk-Forward / Monte Carlo outputs already in the proposal artifact — no new computation happens in the Console itself)
- Cost and tax impact (sourced from the Cost & Tax Engine)
- Supporting backtest summary (sourced from `backtest_runs`)

**Approval Console — allowed actions (exactly these four, no others):**
- **Approve** → writes `status = 'approved'`, timestamp, and the console session identity to `allocation_decisions`; only this state unlocks the Live Trading Engine to act on that specific `proposal_id`.
- **Reject** → writes `status = 'rejected'` with a required `rejection_reason`; proposal is terminal, cannot be re-approved without a new proposal.
- **Postpone** → writes `status = 'postponed'` with a `postpone_until` re-review date; Live Trading Engine takes no action either way.
- **Request additional analysis** → writes `status = 'analysis_requested'` with `analysis_request_notes`, and a new request artifact to a *separate* S3 prefix (`s3://<bucket>/analysis-requests/`, distinct from `proposals/`) specifying what additional analysis is wanted (e.g., wider Monte Carlo resampling, sensitivity to a specific ETF, alternate lookback window). The on-demand/local research instance polls this prefix the same way it already polls for scheduled work, and responds with a revised proposal artifact (a new `proposal_id`). This keeps the one-way trust direction from §13.1 intact — the live micro never pushes a job directly to another machine, it only writes to S3 and the research side pulls.

**Hard execution gate (binding on the Live Trading Engine, not just the Approval Console):** the Live Trading Engine must query `allocation_decisions` for `status = 'approved'` on the specific `proposal_id` **immediately before** placing any order tied to that proposal, not rely on a cached "already approved" flag from earlier in the session. This prevents a race condition where a proposal is approved, then later invalidated (e.g., by a subsequent risk event) between approval and execution.

**Where it runs:** on the micro, alongside the Live Trading Engine and Dashboard, since it needs low-latency read/write access to the same SQLite instance and must gate execution directly. It stays lightweight by design — it renders data that's already been computed elsewhere (proposal artifacts, Decision Explanation output); it performs no backtesting, optimization, or heavy computation itself. This is consistent with §13.2's WAL/single-writer-per-domain rule: the Approval Console is the single writer for `allocation_decisions`' approval fields.

**Rejected alternative 1 — making the Dashboard interactive:** rejected per your explicit decision; also would have blurred the "Dashboard has no execution-layer access" boundary that was a deliberate NFR from the start (§1.2).
**Rejected alternative 2 — Telegram-only approval:** viable for a quick yes/no, but a decision of this weight (real capital, XIRR/drawdown trade-off) benefits from seeing the full comparison table side-by-side, which a chat interface renders poorly. Telegram remains the *notification* channel (Module 13) that tells you a proposal is waiting — it links to the Approval Console rather than replacing it.

**Module count is now 25.**

---

## 14. Architecture Amendment — Autonomous Operations & Self-Healing Framework (Module 26)

**Status: proposed, awaiting your approval on §14.6 before implementation begins. Everything else in this section is a design decision, not a question.**

This is a post-freeze amendment, added after Phase 3 approval, per your explicit new permanent requirement: since you are not a software developer, the platform must detect and — where safe — recover from failures with minimal manual intervention, and must never guess when it comes to capital.

### 14.1 Placement decision: one new cross-cutting module, composing existing infrastructure — not a rewrite

**Chosen: Module 26, a dedicated Autonomous Operations & Self-Healing Framework**, structured as four internal components (detection, decision, recovery execution, alerting/reporting-feed) rather than scattering error-handling logic across every existing module.

**Why a new module and not "add self-healing to each module individually":** you already have a Kill-Switch (§1.3), Logging & Monitoring (Module 14), Compliance & Regulatory Engine (Module 24), and Portfolio Audit Engine (Module 22) each doing a piece of this. Distributing "detect failure and decide how to recover" logic into all 26 modules individually would mean 26 different, inconsistent recovery policies, 26 places to audit, and no single place to answer "what is the platform's overall health right now." A dedicated module gives you one coherent policy and one audit trail — directly serving your stated objective (reliability, capital protection, auditability, minimal manual intervention) better than a distributed approach would.

**Why NOT a full rewrite of existing modules:** Module 26 is designed to **consume signals from and trigger actions in** existing modules, not replace their logic:
- It does not re-implement retry logic — Phase 2's `common/retry.py` (exponential backoff, already built and tested) remains the *call-level* retry primitive; Module 26 operates one level up, deciding whether a *job* (not a single API call) needs to be retried, resumed, or escalated after the call-level retries are already exhausted.
- It does not re-implement data quality judgment — the Data Quality Validator's fail-safe halt (§1.4) is **never overridden** by Module 26. A CRITICAL data quality issue is a *permanent* stop requiring your `force` override (§1.3), not something Module 26 is allowed to "recover" from automatically. This distinction — operational failures are auto-recoverable, financial/data judgment calls are not — is the single most important rule in this whole framework and is enforced structurally, not just by convention (see §14.5).
- It does not gain any new authority over trading — it can only *trigger the same Kill-Switch* the Risk Management Engine and Live Trading Engine already respect, and can only *request* Approval Console attention. It cannot place, cancel, or approve an order under any circumstance. This preserves the entire chain of authority already frozen in §12.2/§13.6 — Module 26 adds a new way to *pull the emergency brake*, not a new hand on the accelerator.
- It does not re-implement broker reconciliation — it schedules and reacts to the Portfolio Audit Engine's (Module 22) existing reconciliation runs for "failed or partial order execution" detection, rather than building a second reconciliation path.
- It does not re-implement report rendering — it writes structured records to new tables (§14.3); the Reporting Engine (Module 16) — which already owns "periodic report generation" — is extended to read from those tables for the five new report types you asked for. Building report rendering inside Module 26 as well would mean two systems generating reports, which is exactly the kind of duplication this platform has consistently avoided (see the Decision Explanation Engine vs. Performance Attribution Engine boundary, Phase 1 §12.5).
- **It builds on groundwork already committed to**, rather than starting from nothing: §7's AWS Architecture already specified "CloudWatch: logs + alarms (disk usage, process health, live-trading heartbeat)" — Module 26 is substantially the formalization and completion of that line item, not a new architectural direction.

### 14.2 Component breakdown

**14.2.1 Detection layer — reuse-first, custom-only-where-nothing-else-provides-it**

| Requirement | How it's detected | Reused or new |
|---|---|---|
| Software errors / runtime exceptions | Structured exception logging already in place (Phase 2's `logging_setup.py`); Module 26 subscribes to ERROR/CRITICAL-level log records | Reused (subscribes to existing logging) |
| API failures | `DataProviderError`/`RetryExhaustedError` from `common/retry.py`, observed after call-level retries are exhausted | Reused (consumes existing exception types) |
| Database issues | SQLite `PRAGMA integrity_check` run on a low-frequency schedule (cheap); `sqlite3.OperationalError`/`DatabaseError` caught at the point of use | New (thin — SQLite gives you `integrity_check` for free, no custom corruption-detection algorithm needed) |
| Data corruption | Same `integrity_check`, plus the Data Quality Validator's existing checks (§1.3) — corruption in *time-series* data is a data quality issue, not a new detection path | Reused for time-series data; new (thin) for the SQLite files themselves |
| Data quality issues | The Data Quality Validator (already built, Phase 2) | Fully reused — Module 26 only observes its `CriticalDataQualityError` events, never re-implements checks |
| Memory leaks | A lightweight heuristic: process RSS sampled hourly via `psutil`, compared against a rolling baseline; monotonic growth beyond a threshold over N consecutive samples is flagged as *probable*, not diagnosed with certainty — full leak diagnosis tooling would itself be a heavy, RAM-competing process, wrong for the micro | New (deliberately minimal) |
| Abnormal CPU/RAM/disk | **AWS CloudWatch Alarms**, not custom Python polling — this is what CloudWatch is for, it costs nothing extra beyond what §7 already budgeted, and it doesn't consume the micro's own scarce RAM to monitor the micro's RAM | Reused (AWS-native, already in §7) |
| Network failures | A lightweight periodic reachability check (HTTP HEAD) against the data/broker endpoints, distinct from a provider call failure — this tells you "is it us or is it the internet," which matters a great deal for a non-developer reading an alert | New (thin) |
| Scheduler failures | Each scheduled job writes a heartbeat/last-run timestamp (extends the existing `ingestion_runs` pattern from Phase 2's `SnapshotRegistry` to all scheduled jobs, not just data ingestion); staleness beyond the expected interval is the signal | Reused pattern, extended scope |
| Failed jobs | Same heartbeat/run-status mechanism, generalized from `ingestion_runs` | Reused pattern, extended scope |
| Failed/partial order execution | Portfolio Audit Engine's (Module 22) reconciliation output — Module 26 schedules reconciliation runs and reacts to discrepancies, it does not talk to the broker directly | Reused (Module 22 does the actual broker-facing work) |

**14.2.2 Decision engine — the safe/unsafe policy, made explicit rather than implicit**

A small, auditable rules table maps `(failure_category, context) → (auto_recoverable, action, limits)`. The categories are deliberately coarse and are grouped by *what kind of thing failed*, not by which module reported it — this is what makes the policy auditable by you, a non-developer, rather than requiring you to understand 26 modules' internals:

| Failure category | Auto-recoverable? | Action |
|---|---|---|
| Process crashed | Yes | Rely on systemd's native `Restart=on-failure` (see §14.4 — not custom Python code) — Module 26 verifies the restart succeeded via the heartbeat resuming, and logs it |
| Process alive but stuck (stale heartbeat, no crash) | Yes, once | Controlled restart of that one service; escalate to alert if it recurs within 1 hour |
| Transient network/API failure | Yes | Already handled call-level by `common/retry.py`; if the *job* still fails after that, retry the whole job once more with backoff; escalate after 2 job-level failures |
| Database connection/lock issue | Yes | Reconnect, retry the transaction; WAL mode (§13.2) already makes this rare |
| Approaching memory/CPU exhaustion (CloudWatch alarm) | Yes, if no order is in flight | Pre-emptive controlled restart of the affected service during a safe window — a controlled restart is safer than waiting for an uncontrolled OOM kill mid-order; **never** restarts a service with an order currently in flight |
| Data quality CRITICAL issue | **No — never** | Halt stands exactly as the Data Quality Validator already enforces (§1.4); Module 26 only alerts and explains, it does not touch the `force` override |
| SQLite corruption (failed `integrity_check`) | **No — never** | Halt the affected service; corruption recovery requires a human decision about which backup/snapshot to restore from |
| Failed or partial order execution / unknown order state | **No — never** | Trigger the existing Kill-Switch immediately; require Module 22 reconciliation and Approval Console confirmation before any further order-related action; this is the literal implementation of "never place uncertain orders" |
| Repeated restart loop (systemd's own `StartLimitBurst` exceeded) | **No — never** | Something is structurally broken, not transient; stop and escalate rather than keep trying |

**14.2.3 Recovery action executor**

Executes exactly the actions the decision engine approves: retry-job, restart-service (via `systemctl restart`, not a custom process manager — see §14.4), reconnect (re-initialize a provider's session), rebuild-cache (re-run `SymbolResolver.refresh()` or regenerate a derived file), resume-job (re-enter a scheduled job using its last checkpoint, leaning on the same `snapshot_id`/run-tracking pattern Phase 2 already uses for resumability). Every single action taken — successful or not — is written to the `recovery_actions` table (§14.3) before anything else happens, so the audit trail exists even if the recovery action itself then fails.

**14.2.4 Alerting & plain-English explanation layer**

Reuses Telegram Notifications (Module 13) as the delivery channel — no new integration built. Adds one new capability: an explanation template, since you explicitly are not a developer. Every alert follows the same fixed structure:
> **What happened:** [plain English, no stack traces, no jargon]
> **What we did:** [action taken, or "Nothing — this needs your decision"]
> **What you should do:** [the single safest recommended next step]

Technical detail (stack trace, exception type, affected module) is still logged in full for audit purposes — it's just not what gets pushed to your phone.

**14.2.5 Reporting — feeds Module 16, does not duplicate it**

Module 26 writes to `error_events`, `recovery_actions`, and `health_snapshots` (§14.3). The Reporting Engine (Module 16, already scoped for Phase 8) is extended with five new report types reading from these tables: **Daily Health Report, Weekly System Report, Error Summary, Recovery Report, Performance Report** (the last of these also draws from Performance Analytics, Module 8, exactly as Module 16 already does for its existing performance reports).

### 14.3 Database schema additions (extends §6; no existing table is modified)

```
service_heartbeats(service_name, last_heartbeat_at, status[healthy/stuck/crashed], pid)
error_events(event_id, timestamp, category, source_module, severity, message_technical, message_plain_english, resolved_bool)
recovery_actions(action_id, timestamp, triggered_by_event_id, action_type, target_service, outcome[succeeded/failed/escalated], detail)
health_snapshots(snapshot_id, timestamp, cpu_pct, memory_pct, disk_pct, rss_trend_flag, open_db_connections)
```

`recovery_actions` directly satisfies your "every recovery action must be logged with timestamp, reason, action taken, and final outcome" requirement — `triggered_by_event_id` links back to `error_events` for the reason, `action_type`/`target_service` for the action, `outcome`/`detail` for the result.

### 14.4 AWS architecture updates (extends §7)

- **CloudWatch Alarms** (CPU/memory/disk) formalized as the primary mechanism for the "abnormal CPU/RAM usage" requirement — already budgeted in §7, now with an explicit consumer (Module 26 reacts to alarm state changes rather than the alarms just existing unread).
- **systemd** (`Restart=on-failure`, `StartLimitBurst`) recommended as the process-supervision mechanism for the live micro's services (Live Trading Engine, Scheduler, Dashboard, Approval Console), rather than a custom Python process manager. This is a deliberate "don't reinvent a solved problem" choice: systemd is the industry-standard, battle-tested mechanism for exactly this, ships with the OS at zero additional cost or dependency weight, and free up Module 26 to focus on the application-level failures nothing else can see (stuck-but-not-crashed processes, data corruption, uncertain order state) — which is where custom logic actually adds value.
- Module 26's own daemon is intentionally lightweight (hourly memory-trend sampling, not continuous polling; reacts to log/event signals rather than busy-polling every component) — it must not become the kind of RAM-competing process that Phase 1 §0 and §12.1 were designed to keep off the live micro.

### 14.5 Boundary with existing modules (explicit, because this is the highest-risk part to get wrong)

Restating as one binding rule, since it matters more than any other part of this amendment: **Module 26 can only pull the emergency brake (Kill-Switch) or ask for your attention (Telegram/Approval Console). It can never place, modify, or approve an order, never override a Data Quality Validator halt, and never bypass the Approval Console.** Every recovery action it's permitted to take (§14.2.2's "Yes" rows) is confined to *infrastructure* — restarting a stuck process, retrying a network call, reconnecting to a database. Anything touching *capital or data judgment* is a hard "No — never" with an immediate, plain-English alert instead. This is what makes "maximum reliability" and "protect capital" compatible requirements rather than a contradiction — the automation is scoped to exactly the class of problem where automation is safe.

### 14.6 Roadmap placement — needs your decision

Two reasonable options:

1. **Foundational slice now, full framework before Phase 12 (Live Trading).** Build the lightweight parts (heartbeats, `error_events` logging, job-level retry, Telegram alert template) starting in Phase 4, since Phase 2/3's unattended jobs (data ingestion, universe scoring) already benefit from it. The trading-critical parts (Kill-Switch integration, order-state handling) get built as a hard gate before Phase 12, alongside the Compliance Engine registration dependency already noted in §13.5.
2. **Standalone phase now**, before Phase 4, so the operational safety net exists under every subsequent phase from the start rather than being retrofitted partway through.

I'd lean toward option 1 — the trading-critical half of this framework has nothing to monitor yet (there's no live trading until Phase 12), so building it fully now means testing it against a system that doesn't exist yet. But this is a genuine judgment call about sequencing, not something I should decide unilaterally given how much you've emphasized this requirement.

**Final module count: 26.**

---

## 15. Architecture Amendment — Capital-Agnostic Strategy Design & the Available Investment Pool (Version 1.0 Permanent Requirement)

**Status: recorded as binding. No code change required — verified against the frozen Phase 4 implementation and confirmed compliant (see §15.4). This amendment governs Phase 5 onward, primarily Module 3 (Strategy Engine, Phase 6) and the Portfolio Optimizer.**

### 15.1 This formalizes a principle already approved, it doesn't introduce a new one

§12.3 (Capital Scale — APPROVED, from Phase 1's original closure) already states: *"No module may hardcode absolute rupee amounts, fixed lot counts, or capital-tier-specific logic. Position sizing, rebalancing thresholds, and risk limits are all expressed in percentage/weight terms, converted to actual order quantities only at execution time."* Your new requirement is that principle made concrete and non-negotiable, with a named abstraction (the Available Investment Pool) and an explicit Recurring-vs-Lump-Sum distinction that §12.3 didn't spell out. I'm recording it as its own section because it's specific enough to need its own binding definition, not because it changes direction.

### 15.2 The Available Investment Pool — formal definition

A single, explicit input every allocation decision is computed against. No strategy, optimizer, or config file may reference an absolute rupee amount as a *parameter of the strategy itself* — the only place an absolute amount may ever appear is as the **current value of this pool**, supplied at decision time.

```
AvailableInvestmentPool:
    existing_portfolio_value: float   # current market value of already-held positions (0 for a fresh account)
    new_capital: float                # capital being added this cycle (0 for a pure rebalance with no new money)
    capital_source: CapitalSourceType # RECURRING_MONTHLY | LUMP_SUM | NONE
    as_of_date: date

    total_investable = existing_portfolio_value + new_capital
```

**Binding rule:** the Strategy Engine's output is always a set of **target weights** (percentages summing to <=100%), computed from `market_state` and `current_holdings` — never from `total_investable` directly. `total_investable` is consulted only by the translation step that converts weights into order quantities, which happens strictly after the Strategy Engine has already decided *what* to buy, not *how much money* is available. This ordering is what makes the same strategy logic produce correct, proportional results whether `total_investable` is Rs.1,000 or Rs.5,00,000 — the strategy never sees the number until translation time, so it cannot special-case it.

### 15.3 Recurring Monthly Investment vs. One-Time Lump Sum — same strategy, different execution policy

Both draw target weights from the identical Strategy Engine output. They differ only in **how the resulting orders are scheduled**, which is the Execution Policy's job, not the Strategy Engine's:

- **Recurring Monthly Investment Policy:** each month's `new_capital` is allocated toward the *current* target weights, effectively buying whatever is furthest underweight first — this is standard SIP-style dollar-cost-averaging-into-target behavior. Optionally deployable immediately on receipt, or spread across a few business days to reduce single-day timing risk on a recurring basis.
- **One-Time Lump Sum Policy:** a larger `new_capital` amount may be deployed immediately in full, or staggered across a configurable number of tranches/days — a standard, well-documented institutional practice for reducing the timing risk of deploying a large sum in one day. This is a real design choice with a real trade-off (immediate deployment maximizes time-in-market; staggering reduces regret risk from bad timing), and the platform should support both, not silently pick one.

**Rejected alternative — one undifferentiated "add money" pathway:** collapsing both into a single execution path would either force lump sums through slow incremental deployment (unnecessarily delaying capital deployment) or force recurring contributions through immediate-full-deployment logic that doesn't suit a small recurring amount. Two named policies sharing one allocation engine is more correct than one policy trying to serve both cases adequately.

### 15.4 Verified against the frozen Phase 4 implementation — no conflict, no modification needed

I checked the actual Phase 4 source rather than assuming compliance:
- No hardcoded rupee amount exists anywhere in `src/etf_platform/` outside of test fixtures (which use arbitrary example values like Rs.1,00,000 purely as *test inputs* demonstrating the logic works at that value — not as a structural assumption baked into any module's logic; this distinction matters and is worth being explicit about, since a test asserting correct behavior at one specific capital level is not the same thing as a module hardcoding that level).
- `BacktestConfig.initial_capital` is already a caller-supplied parameter, not a constant.
- `OrderIntent.quantity` is unit-quantity, not a percentage — this is correct and expected: `OrderIntent` is deliberately the **execution-layer** artifact (Phase 1 §4/§12.6), sitting exactly where §12.3 says quantities belong — "converted to actual order quantities only at execution time." The not-yet-built Strategy Engine (Phase 6) sits above this interface and is responsible for producing percentage-based target weights; translating those weights into `OrderIntent`s (using the Available Investment Pool's `total_investable` and current prices) is the last step before Phase 4's engine ever sees an order — exactly the boundary this amendment requires.

**No code in the frozen Phase 4 implementation needs to change.** Its interface was already scoped correctly for this requirement to slot in cleanly above it.

### 15.5 Database schema addition (extends §6; no existing table modified)

```
investment_pool_events(event_id, as_of_date, capital_source[recurring_monthly/lump_sum/none],
                        new_capital, existing_portfolio_value, total_investable, notes)
```

An audit record of every capital-inflow event feeding an allocation decision — consistent with the platform's existing reproducibility/audit pattern (every `backtest_runs` row and every `allocation_decisions` row already answers "what produced this," this table answers "how much capital was available when it did").

### 15.6 Binding constraint carried into Phase 6 (Strategy Engine) design

When Phase 6 begins, the Strategy Engine's public interface must accept an `AvailableInvestmentPool` (or be entirely blind to capital amount and receive only `current_holdings` expressed as weights, with the pool consulted exclusively by the translation step) and must return weights, never amounts or quantities. This is now a binding interface constraint on that module's design, the same way the no-look-ahead sequencing was a binding constraint on Phase 4's engine before it was built.

---

## 16. Architecture Amendment — Module 28: Portfolio Cash & Execution Manager (Version 1.0 Permanent Requirement)

**Status: recorded as binding. Design-only — no code written this turn (no phase was invoked). Three open questions in §16.8 need your decision before implementation.**

### 16.1 Numbering note

Module 26 (Self-Healing Framework) was the last assigned module number. §15 was a design amendment, not a new module, so it didn't consume a number. That leaves 27 unassigned. I'm recording this as Module 28 exactly as you specified, but flagging the gap rather than silently renumbering it to 27 — if 27 was meant for something else (a Recurring/Lump-Sum Execution Policy module would be a reasonable candidate, given §15.3), tell me and I'll record it separately; otherwise I'll treat 27 as reserved/unused going forward.

### 16.2 Authority placement — this adds a gate, it does not become a new authority

Restating the rule you gave as precisely as possible, because it's the highest-risk part to get wrong (same reasoning as §14.5 for the Self-Healing Framework): **"No module may directly spend cash"** means every purchase must pass through Module 28's cash-availability check, but Module 28 does **not** gain any of the authorities already assigned elsewhere. It sits *between* an already-approved action and the money actually moving:

```
Approval Console (sole authority: WHICH allocation is approved)
        |
Risk Management Engine / Compliance Engine / Kill-Switch (sole authority: is it SAFE to trade right now)
        |
Portfolio Cash & Execution Manager  <-- NEW: sole authority on whether the CASH exists for this specific order
        |
Live Trading Engine (places the order with the broker)
```

Module 28 can **block** an order for lack of funds (a new, narrow veto — exactly the kind of addition already established as the safe pattern for new modules touching execution, see §14.1/§14.5), but it cannot approve a trade the Approval Console rejected, cannot override a Compliance/Risk halt, and cannot invent a new order the Strategy Engine didn't propose. It answers exactly one question: *"do we have the money, right now, for this already-approved instruction?"*

### 16.3 Relationship to existing modules — reuse, not duplication

Four existing pieces of this architecture overlap with what you've specified. I'm resolving each boundary explicitly rather than letting them blur, consistent with how every other module boundary in this document has been handled:

- **Cost & Tax Engine (Module 18, already built, Phase 4)** — your Execution History requirement (#7: brokerage, taxes per execution) is exactly `CostBreakdown`, which `CostTaxEngine.compute_transaction_cost()` already computes. Module 28 **records** that output against a cash-ledger entry; it does not recompute brokerage/STT/stamp duty/GST itself. One cost calculation, reused everywhere it's needed — same principle as everywhere else in this platform.
- **Portfolio Audit Engine (Module 22, approved but not yet built)** — your Audit requirement (#8: reconciliation, mismatch detection) overlaps with Module 22's already-defined job ("periodic automated reconciliation of actual holdings vs. the platform's internal state," Phase 1 §1.3). Boundary: **Module 22 reconciles positions** (do we hold what we think we hold, per the broker); **Module 28 reconciles cash** (do we have the money we think we have, per your exact formula in point 8). Complementary, not duplicative — together they answer "is the whole portfolio, cash and holdings, exactly what our records say it is."
- **Reporting Engine (Module 16, approved but not yet built)** — your Monthly Report requirement (#9) is data Module 28 *produces*; Module 16 is still the sole *renderer* of periodic reports, exactly the boundary already established for the Self-Healing Framework's five report types (§14.2.5). Module 28 does not build its own report-rendering path.
- **Any mismatch → CRITICAL alert (#8)** — reuses Module 26's existing detection→decision→alert pipeline rather than inventing a second alerting mechanism. A cash-reconciliation mismatch is logged as an `error_event` with category `cash_reconciliation_mismatch`; per §14.2.2's decision matrix this is unambiguously a **"No — never" auto-recover** case (a cash discrepancy is a data/financial-integrity problem, not an infrastructure glitch) — Module 26 halts the affected service and sends the plain-English Telegram alert exactly as it already does for every other "No — never" case. Module 28 doesn't need its own Telegram integration; it needs to raise the right event.

### 16.4 Relationship to Phase 4's `Portfolio` class — deliberately separate, not an extension

Phase 4's `Portfolio` (in `backtesting/portfolio.py`, frozen at v0.4) already tracks cash and positions and applies fills with cost accounting — on the surface, similar to what you're describing. I'm recommending Module 28 be a **separate module for live/paper trading**, not an extension of Phase 4's class, for three concrete reasons:

1. Phase 4's `Portfolio` is an in-memory object for the lifetime of one backtest run — it has no persistence. Your Investment Queue explicitly needs to survive across days ("Day 1 invest ₹7,500 ... Day 3 invest ₹5,000 ... Day 6 invest ₹7,500") and across scheduler restarts — that requires SQLite-backed state (the same WAL+lock pattern already used for `SnapshotRegistry` and `BacktestRunRegistry`), which a backtest's single-process, single-run object was never designed for.
2. A backtest is a closed simulation you fully control; live cash has real-world asynchrony this doesn't — deposits arrive on their own schedule, dividends post on the broker's timeline, brokerage refunds happen irregularly. None of this exists in a backtest.
3. Reusing Phase 4's class here would mean touching frozen code, which you've twice now explicitly told me not to do.

**What Module 28 *does* reuse from Phase 4:** the cash-safety invariants (never negative, never invent money, everything explicit) and the general design pattern (FIFO where order matters, explicit sign handling for cash flows) are the same principles, just re-implemented against persistent state. I'm not reinventing the philosophy, only the storage model.

### 16.5 Investment Queue — state machine and draw-down order

```
InvestmentQueueEntry:
    queue_id, deposit_date, amount, source [MONTHLY_SIP | LUMP_SUM | DIVIDEND | MANUAL],
    remaining_balance, status [PENDING | PARTIALLY_INVESTED | FULLY_INVESTED | CANCELLED]
```

**Draw-down order across multiple simultaneous queue entries (e.g., a monthly SIP deposit arrives while a prior lump sum still has a remaining balance) — proposed default: FIFO by deposit date.** The oldest pending cash gets deployed first. This is a proposal, not yet a decision — flagged in §16.8 — but I'm recommending it because it's the same FIFO principle already governing tax-lot matching (`CostTaxEngine`), which keeps the platform's "what gets consumed first" logic consistent across cash and holdings rather than having two different draw-down philosophies.

**CANCELLED status:** occurs if pending (not-yet-invested) cash is withdrawn before deployment. The corresponding cash-ledger withdrawal entry and the queue entry's transition to CANCELLED must reference each other, so the reconciliation formula in §16.6 stays exact even for cash that was deposited and then pulled back out before ever being invested.

**"Never ignore pending cash" (your cash-safety point 5):** I'm not silently defining a maximum allowable idle period, since how aggressively to deploy pending cash is a strategy policy question, not an architecture one, and shouldn't be decided by an infrastructure amendment — but Module 28 will expose pending-queue age/amount as a standard signal, so whatever Phase 6 strategy consumes it can enforce its own deployment-speed policy.

### 16.6 Reconciliation formula and Monthly Report — as specified

Your formula (`Total Deposits − Investments − Charges + Dividends = Current Cash`) is implemented as a scheduled, and post-every-transaction, check — not just a periodic batch job, since "any mismatch immediately generates a CRITICAL alert" implies detection should be as close to real-time as the cash ledger's own write path allows. Monthly Report fields (#9) are exactly as you specified, sourced from the cash ledger and investment queue tables below, rendered by Module 16.

### 16.7 Database schema (extends §6; no existing table modified)

```
cash_ledger(entry_id, timestamp, transaction_type[deposit/withdrawal/dividend/interest/
            brokerage_refund/investment/charge], amount, running_balance, queue_id, notes)

investment_queue(queue_id, deposit_date, amount, source[monthly_sip/lump_sum/dividend/manual],
                  remaining_balance, status[pending/partially_invested/fully_invested/cancelled])

execution_history(execution_id, queue_id, timestamp, symbol, quantity, limit_price, executed_price,
                   brokerage, taxes, cost_breakdown_ref, remaining_cash_after)
```

`execution_history.cost_breakdown_ref` ties back to the `CostBreakdown` already computed by `CostTaxEngine` (§16.3) — the same reuse-not-recompute principle applied structurally in the schema, not just in prose.

### 16.8 Open questions — RESOLVED

1. **Queue draw-down order — APPROVED: FIFO, mandatory, with one named exception.** The oldest deposit is always fully utilized before the next-oldest is touched (e.g., 01 Jan's ₹20,000 must reach zero before 08 Jan's ₹15,000 is drawn down, which must reach zero before 15 Jan's ₹30,000). **Exception:** an explicit manual instruction assigning a specific deposit to a specific investment overrides FIFO for that instruction only — logged the same way every other decision in this platform is logged (with the override reason recorded), not a silent bypass. Absent a manual override, FIFO is not a default that can be reasoned around; it's mandatory.
2. **Module 27 — RESERVED, not renumbered, not assigned.** No existing module number changes. Module 27 stays open for future use (a Recurring/Lump-Sum Execution Policy module, per §15.3, remains a reasonable future candidate, but nothing is decided or assigned yet).
3. **Backtesting integration — CONFIRMED: Module 28 is not part of the Backtesting Engine.** Backtesting validates investment strategy only; cash-ledger management is an execution concern that belongs to Paper Trading (Phase 10) and Live Trading (Phase 12) exclusively. If staggered lump-sum deployment needs to be evaluated in a backtest, it is modeled as an execution policy inside the Strategy Engine (§15.3), not by invoking Module 28. Phase 4's frozen `Portfolio` class remains untouched and needs no integration point with Module 28 — they are permanently separate, not just separate-for-now.

### 16.9 Additional permanent rule — Idle Cash Must Never Be Silently Held, and Must Never Be Force-Invested

Two failure modes, both ruled out explicitly:

- **Silent indefinite idling:** every `investment_queue` entry now carries, in addition to the fields in §16.5, a **Creation Date**, a derived **Current Age**, and a **Reason Still Pending** (populated by whatever is holding the deployment back — e.g., "awaiting a favorable entry per strategy signal," "partial fills only, remainder queued," "manual hold"). If an entry's age exceeds a **configurable maximum holding period**, the platform notifies you with a recommendation. This is a detection-and-inform behavior, not a detection-and-act one.
- **Forced investment:** the platform must **never** automatically invest cash purely because it has aged past the threshold. Capital protection outranks capital deployment — an aging queue entry is a signal for your attention, never an authorization for the system to act on its own. This is the same "detect, alert, recommend, but never act unilaterally on financial judgment calls" boundary already established for Module 26 (§14.5) and for Module 28's own authority scope (§16.2) — this rule is a direct instance of that same principle, not a new one.

**Placement:** the aging check and notification integrate with Module 26's existing detection/alerting pipeline (§14.2.1's heartbeat/staleness pattern is structurally the same mechanism — "how long has X been true without change" — applied here to queue entries instead of price data or service health), rather than building a fourth place in the platform that decides when to send you a Telegram message.

### 16.10 Section 16 status: APPROVED — permanent Version 1.0 architecture requirement

All open questions resolved. No further decisions pending on Module 28's design. Implementation is scoped to Phase 10 (Paper Trading) and Phase 12 (Live Trading), per §16.8 item 3 — it is not part of Phase 5 or any phase before real execution exists to manage.



---

**Phase 1 is now fully reviewed, finalized, and closed (original scope: 25 modules).**

Final original module inventory (25 total): the 17 you originally specified, plus Data Quality Validator, Secrets & Credentials Manager, Execution Kill-Switch, Cost & Tax Engine, Monte Carlo Simulation Engine, Performance Attribution Engine, Walk-Forward Validation Framework, Portfolio Audit Engine, Decision Explanation Engine, Compliance & Regulatory Engine, and the Approval Console. §12 and §13 are the authoritative record of every binding decision from that closure — treat them as the source of truth if anything in §1–§11 reads ambiguously by comparison.

The pre-code review (§13) resolved five architectural gaps directly (cross-instance handoff, SQLite concurrency, version consistency, CI test gate, compliance registration timing) and one — the Approval Console — per your explicit direction at the time.

**§14 is a post-freeze amendment**, added after Phase 2 and Phase 3 were built and approved, introducing Module 26 (Autonomous Operations & Self-Healing Framework) as a new permanent requirement. It does not alter any decision in §0–§13 — Phase 2 and Phase 3's frozen code and production-readiness reports stand as delivered. §14.6 has one open roadmap-sequencing question awaiting your decision before implementation begins; everything else in §14 is a finalized design decision, not a question.

**§15 is a second post-freeze amendment**, added after Phase 4 was built, reviewed, and frozen (v0.4), recording the Available Investment Pool abstraction and the capital-agnostic design requirement as permanent for Version 1.0. It formalizes a principle already approved at §12.3 rather than introducing a new direction. Verified against the actual Phase 4 source: no hardcoded amounts exist, and the frozen `OrderIntent`/execution-layer interface was already correctly scoped for this requirement to slot in above it — **no Phase 4 code was modified**, consistent with your instruction not to touch the frozen implementation. §15 is fully resolved, no open questions.

**§16 is a third post-freeze amendment**, recording Module 28 (Portfolio Cash & Execution Manager) — design-only, no code written. It draws explicit boundaries against three existing modules (Cost & Tax Engine reuses its cost calculations rather than recomputing them; Portfolio Audit Engine reconciles positions while Module 28 reconciles cash; Reporting Engine still renders all periodic reports) and against Phase 4's frozen `Portfolio` class (deliberately not extended — different persistence and concurrency needs, and permanently excluded from backtesting per §16.8). All open questions are resolved: FIFO draw-down (mandatory, manual-override exception only), Module 27 reserved and unused, Module 28 scoped exclusively to Paper Trading and Live Trading. §16.9 adds a permanent idle-cash rule: aging queue entries trigger a notification with a recommendation, never an automatic forced investment. **§16 is fully approved and closed.**

**Current status: awaiting your decision on §14.6 (Module 26 roadmap sequencing) only. §15, §16, §17, and §18 are all fully resolved and binding, with no open questions.**

---

## 17. Architecture Amendment — Event-Driven Resource Optimization (Version 1.0 Permanent Requirement)

**Status: recorded as binding, cross-cutting across every component named below. No conflict found with anything already approved — see §17.4.**

### 17.1 The principle

This platform is built for long-term ETF investing, not high-frequency trading. Every component — Strategy Engine, Investment Queue, Execution Manager (Module 28), Scheduler (Phase 9), and supporting services — must minimize AWS resource usage and external API calls by **remaining idle whenever there is no work to perform**, waking only on defined events:

- Daily scheduled funding check.
- Detection of new cash in the Kite account.
- Telegram commands.
- Approved strategy or portfolio changes.
- Approved rebalance events.
- Scheduled monthly reports.
- System recovery events.
- Critical alerts from Module 26.

After completing all required work, a component must: persist its complete state, write audit logs, release unnecessary resources, and return to idle. **No continuous polling loops or unnecessary background processing are permitted anywhere in this platform.**

### 17.2 What "idle" means concretely (a clarification, not a new decision)

"Detection of new cash in the Kite account" is not a separate real-time push listener — Kite does not offer a balance-change webhook this platform relies on. It is the **outcome** of the once-daily scheduled funding check (already specified in Phase 6's Monthly Funding Policy, PHASE6_Objectives.md §3.2) — the check itself is the detection mechanism, not a distinct always-on watcher.

For most components named above (Strategy Engine, Scheduler-triggered checks, report generation), "idle" is best implemented as a **short-lived process invoked once per event, that exits when its work is done** — not a resident daemon sleeping in a loop. The Scheduler (Phase 9) is what actually fires these invocations (cron-like), and this is consistent with, not a change to, what was already specified for Strategy Engine's daily funding check (PHASE6_Objectives.md §3.4).

**One explicit, deliberate exception:** the Live Trading Engine's WebSocket connection to Kite (§7's original AWS architecture) must remain continuously connected — "idle" for that component means low CPU/resource usage *while connected*, not process exit between events, since a live order-execution path cannot afford the latency of re-establishing a broker connection on every tick. This exception was already implicit in §7's original wording ("the live trading + WebSocket tick listener must never be starved of CPU") and is not weakened or reopened by this amendment — it's the one component this principle does not apply to in the "process exits between events" sense, and I'm stating that explicitly rather than leaving it to be inferred incorrectly later.

### 17.3 Funding workflow termination and reactivation

Confirms and formalizes what PHASE6_Objectives.md §3.2 already specified as the `IDLE (until next 1st)` terminal state: once a given month's contribution has been fully allocated per the priority/capital rules then in effect (§5) — which is not necessarily the same as every target weight gap being closed, since insufficient capital can still leave gaps that simply carry forward per §5's self-correcting design — the funding workflow terminates for that month and performs no further daily checks. It reactivates automatically, and only, at the start of the next monthly cycle (the 1st).

### 17.4 Consistency check against existing approved design (no conflicts found)

- **Module 26 (§14.2.1)** already specifies "hourly memory-trend sampling, not continuous polling... reacts to log/event signals rather than busy-polling every component" — this amendment formalizes as a platform-wide rule what was already Module 26's own design.
- **Risk Management Engine (Phase 5)** was already scoped as on-demand/callable, explicitly not a scheduled daemon, in its own Phase 5 objectives document — already consistent.
- **Strategy Engine (Phase 6)** was already designed as "intentionally a pure, stateless computation given its inputs... holds no persistent state of its own" (PHASE6_Objectives.md §6) — already consistent; this amendment adds the explicit persist/log/release/idle lifecycle around that existing statelessness, it doesn't change the underlying design.
- No existing approved decision required weakening or reopening to accommodate this amendment.

**§17 is fully resolved and binding. No open questions.**

---

## 18. Architecture Amendment — Module 27: Market Intelligence Engine (Version 1.0 Permanent Requirement)

**Status: APPROVED AND FULLY RESOLVED. All five originally-flagged items closed — see §18.6.**

### 18.0 Exhaustive prohibition list (recorded verbatim, binding)

Module 27 shall never: generate buy signals; generate sell signals; trigger portfolio changes; override Strategy Engine; override Risk Management; override Approval Console; override Compliance; execute trades. This is a stricter, more exhaustive statement than §18.2's original "never a trigger, only an input" — recorded here word-for-word as its own subsection specifically so it can't be quietly narrowed in a future paraphrase.

**Module 27's approved responsibilities, exhaustively (nothing beyond this list without a further amendment):**
- Observe market behavior.
- Record historical market conditions.
- Calculate lightweight technical and statistical indicators.
- Classify market regimes (Bull, Bear, Sideways, High Volatility, Low Volatility, etc.).
- Measure ETF relative strength.
- Measure sector strength **using available ETF data** (see §18.4a — this phrase resolves the sentiment/macro question).
- Build a long-term historical market knowledge database.

### 18.1 I'm reversing my Module 27 recommendation, and telling you so directly

When Module 27 came up during Phase 6 design (§0.2 of PHASE6_Objectives.md), I recommended it stay reserved and unused, with Execution Policy logic folded into Strategy Engine instead. You approved that (Decision 3). Market Intelligence changes the calculus: it is read-only, decision-independent, persists its own historical database, and — per your own framing ("the Strategy Engine *may* use this information... but only as one input among many") — is explicitly meant to be a separate thing Strategy Engine *consumes*, not something built into Strategy Engine's own code. This is a clean single-responsibility module in a way Execution Policy wasn't. **Module 27 = Market Intelligence Engine — CONFIRMED.**

### 18.2 Scope and hard boundary — never a trigger, only an input

**CONFIRMED**, and now stated exhaustively at §18.0 rather than only generally here. Market Intelligence's output can only ever be a **soft input** — in Phase 5's own hard/soft constraint framework, it is soft, never hard, and it is not even a full soft *constraint* in the optimization sense — it's advisory context attached to a proposal (e.g., "market currently classified Bearish, elevated volatility" shown alongside a proposal in the Approval Console) for **your** judgment, never Strategy Engine's own gate or trigger. Strategy Engine's core buy-only decision logic (§5/§6 of PHASE6_Objectives.md) must remain fully functional with Market Intelligence entirely absent or disabled — this is the concrete, testable form of "one input among many, never the sole reason."

### 18.3 Not the AI Dynamic Allocation Engine — a boundary worth stating plainly

**CONFIRMED**, restated even more explicitly in your latest message: *"Market Intelligence itself shall never learn, predict, optimize, or make investment decisions."* Module 27 **collects and records** observational data over time; it performs no inference, no model training, no prediction. The AI Dynamic Allocation Engine (Phase 7, not yet built) may use Market Intelligence as one input — but that is Phase 7's future decision to design, not something Module 27 does itself.

### 18.4 Data sources — RESOLVED by scope narrowing

Your approved responsibility list (§18.0) omits "market sentiment" and "significant macroeconomic or market events" — present in your original message, absent from this one. **My reading: sentiment/macro-event tracking is out of Module 27's current scope**, narrowed specifically to what's derivable from ETF price data already flowing through `HistoricalDataEngine` (Phase 2, frozen, unmodified) — trend, volatility, regime classification, relative strength, sector strength "using available ETF data" (§18.0's own phrase, which I'm reading as confirming this). No new provider type is needed under this reading. **I'm proceeding on this interpretation** — if you actually intended sentiment/macro to remain in scope via a source you have in mind, say so and I'll treat it as a distinct, later addition to Module 27 rather than assume silently either way.

### 18.4a Compute placement — RESOLVED (confirms §18.5's original recommendation, adopted verbatim)

**CONFIRMED, your wording adopted directly:** *"Lightweight calculations using the tracked ETF universe may run on the EC2 Micro. Heavy market-wide analysis, broad market breadth calculations, optimization, or computationally intensive analytics shall remain on the research-side architecture and publish their results for later use."* This is exactly the split I proposed; recording your own phrasing as the binding text rather than my paraphrase of it.

### 18.5 Storage design — proceeding without objection, not yet explicitly confirmed

No objection was raised to the recommended two-tier pattern (bulk daily indicator history via the same `TimeSeriesStore` abstraction used for OHLCV data, lightweight run-metadata in SQLite mirroring `SnapshotRegistry`). Treating this as approved-by-default given "persist its results, update its historical database" is consistent with it and it's a low-stakes implementation detail relative to the other items — but noted here as inferred, not word-for-word confirmed, in case that distinction matters to you later.

### 18.6 Status of the five originally-flagged items

1. **Module 27 reassignment** — CONFIRMED (§18.1).
2. **Sequencing (Phase 6 vs. separate phase)** — **RESOLVED: separate, later phase.** Module 27 will not be implemented in Phase 6. Phase 6 defines only the `MarketIntelligencePort` interface (specified in PHASE6_Objectives.md §21) that Strategy Engine will optionally consume once Module 27 exists — every port method returns `| None` as its normal, expected value when Module 27 is absent, disabled, or has no data, and Strategy Engine's core decision logic (§5/§6 of PHASE6_Objectives.md) is required to behave identically regardless. Module 27's actual implementation is deferred to a dedicated phase after Strategy Engine is complete, reviewed, adversarially tested, and frozen.
3. **Sentiment/macro data source** — RESOLVED by scope narrowing (§18.4).
4. **Compute split** — CONFIRMED, your wording adopted verbatim (§18.4a).
5. **Storage design** — proceeding without objection (§18.5), not word-for-word confirmed.

**Wake event note (ties to §17):** "Daily Maintenance Window" is not currently one of §17.1's named wake events — I recommend it becomes the consolidated umbrella event that both the funding check (§3 of PHASE6_Objectives.md) and this Market Intelligence update fire under, rather than the Scheduler waking the live instance twice a day for two conceptually-daily tasks. Still a recommendation, not yet applied to §17's already-closed text — needs your confirmation before I touch a section already marked fully resolved.

**§18 is now fully resolved and closed — all five originally-flagged items addressed.**
