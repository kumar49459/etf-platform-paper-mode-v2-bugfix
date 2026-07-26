import random

from etf_platform.paper_trading_operations.session import ExtendedPaperTradingSession


def main():
    session = ExtendedPaperTradingSession(
        db_path="data/paper_simulation.db",
        symbols=("NIFTYBEES", "MON100", "PSI", "SOXL"),
        seed=42,
    )

    print("Starting simulation...")

    session.run(
        num_days=10,
        cycles_per_day=2,
        rng=random.Random(42),
    )

    print("Simulation completed")
    print(f"Total cycles: {len(session.state.cycle_log)}")

    session.close()


if __name__ == "__main__":
    main()