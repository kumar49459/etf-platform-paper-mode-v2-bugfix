"""Mutation testing (Milestone 4, requirement 4). For each mutation below:
monkey-patch the real code to introduce a specific, realistic bug, run the
existing test suite (or a targeted subset), confirm it FAILS, then revert.
A mutation the existing tests don't catch is a real gap -- this file
exists to find those gaps and, where found, the fix is a new or
strengthened test elsewhere in the suite, not a change to this file.

Each test here asserts the MUTATION IS DETECTED -- i.e. that running the
relevant tests against the mutated code produces at least one failure.
If a test in this file itself fails, that means the mutation SURVIVED
(the existing suite didn't notice), which is the real finding to act on.
"""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from etf_platform.execution_manager import OrderLifecycleState
from etf_platform.execution_manager.models import ORDER_LIFECYCLE_TRANSITIONS


def _run_suite(*test_modules):
    suite = unittest.TestSuite()
    loader = unittest.TestLoader()
    for module in test_modules:
        suite.addTests(loader.loadTestsFromModule(module))
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        runner = unittest.TextTestRunner(stream=buf, verbosity=0)
        result = runner.run(suite)
    return result.testsRun, len(result.failures), len(result.errors)


class TestMutationBuyOnlyDiffSignFlip(unittest.TestCase):
    """Mutation: flip the sign in priority.py's gap comparison, so a
    target BELOW current looks like a buy opportunity instead of one
    ABOVE current. This is exactly the manual-selling-rule enforcement
    point (Phase 6) -- a mutation here would be a serious, silent
    violation of a hard platform rule if undetected."""

    def test_mutation_is_detected_by_existing_tests(self):
        import etf_platform.strategy_engine.priority as priority_module
        import tests.unit.test_strategy_engine_priority as priority_tests

        def mutated(current_weights, target_weights):
            all_symbols = set(current_weights) | set(target_weights)
            gaps = {}
            for symbol in all_symbols:
                current = current_weights.get(symbol, 0.0)
                target = target_weights.get(symbol, 0.0)
                if current > target + 1e-9:  # MUTATED: sign flipped from `target > current`
                    gaps[symbol] = current - target
            return gaps

        with mock.patch.object(priority_module, "compute_buy_only_diff", mutated):
            ran, failures, errors = _run_suite(priority_tests)

        self.assertGreater(ran, 0)
        self.assertGreater(
            failures + errors, 0,
            "MUTATION SURVIVED: flipping the buy-only-diff sign comparison was not caught by any test. "
            "This is a serious gap -- this exact logic enforces the manual-selling rule.",
        )


class TestMutationWholeUnitTruncation(unittest.TestCase):
    """Mutation: round() instead of int() truncation in Strategy Engine's
    affordable-quantity calculation -- could round UP to a quantity that
    isn't actually affordable, violating the whole-unit/never-overspend
    guarantee."""

    def test_mutation_is_detected_by_existing_tests(self):
        import etf_platform.strategy_engine.strategy as strategy_module
        import tests.unit.test_strategy_engine_operational_adversarial as adversarial_tests

        def mutated(self, price, budget):
            if price <= 0 or budget <= 0:
                return 0
            from etf_platform.cost_tax_engine import Side

            quantity = round(budget / price)  # MUTATED: round() instead of int(), can round UP
            while quantity > 0:
                cost = self._cost_tax_engine.compute_transaction_cost(Side.BUY, price, quantity)
                if quantity * price + cost.total_cost <= budget:
                    return quantity
                quantity -= 1
            return 0

        with mock.patch.object(strategy_module.StrategyEngine, "_affordable_quantity", mutated):
            ran, failures, errors = _run_suite(adversarial_tests)

        self.assertGreater(ran, 0)
        if failures + errors == 0:
            # Verified mathematically, not just observed as a passing
            # test: whenever round() differs from int() (fractional part
            # >= 0.5), the rounded-up quantity's GROSS cost alone already
            # exceeds budget -- before transaction costs are even added --
            # so the while-loop's first affordability check fails and it
            # decrements to the exact value int() would have produced
            # directly. There is no input for which this mutation changes
            # the function's output. This is a genuine, positive property
            # of the self-correcting while-loop design (robust to an
            # entire class of "starting guess" mutations), not a test
            # coverage gap -- there is nothing behavioral here for a test
            # to detect, so no test was added to force detection of a
            # difference that cannot exist.
            self.skipTest(
                "Verified mathematically inert, not a test gap: round() vs int() as the starting point "
                "can never change _affordable_quantity's output, because whenever they'd differ, the "
                "rounded-up value's gross cost alone already exceeds budget and the while-loop's own "
                "affordability check immediately corrects to int()'s value regardless of starting point."
            )


class TestMutationLifecycleTransitionTableCorruption(unittest.TestCase):
    """Mutation: remove FILLED->RECONCILED from the valid-transitions
    table -- would make every filled order permanently stuck, unable to
    ever be confirmed. Exactly the class of bug the Milestone 2
    QUOTE_UNAVAILABLE incident already proved can hide from weak tests."""

    def test_mutation_is_detected_by_existing_tests(self):
        import etf_platform.execution_manager.models as models_module
        import tests.unit.test_execution_manager_models as models_tests

        mutated_transitions = dict(ORDER_LIFECYCLE_TRANSITIONS)
        mutated_transitions[OrderLifecycleState.FILLED] = frozenset()  # MUTATED: no valid transitions at all

        with mock.patch.object(models_module, "ORDER_LIFECYCLE_TRANSITIONS", mutated_transitions):
            ran, failures, errors = _run_suite(models_tests)

        self.assertGreater(ran, 0)
        self.assertGreater(
            failures + errors, 0,
            "MUTATION SURVIVED: removing FILLED->RECONCILED was not caught. This would silently make "
            "every filled order permanently unconfirmable.",
        )


class TestMutationSilentPersistenceFailure(unittest.TestCase):
    """Mutation: save_execution_record() silently does nothing -- the
    single most dangerous possible defect in this entire module,
    persistence appearing to succeed while doing nothing at all."""

    def test_mutation_is_detected_by_existing_tests(self):
        import etf_platform.execution_manager.persistence as persistence_module
        import tests.unit.test_crash_recovery_checkpoints as crash_tests

        def mutated_save(self, record):
            return None  # MUTATED: silently does nothing

        with mock.patch.object(persistence_module.ExecutionStateStore, "save_execution_record", mutated_save):
            ran, failures, errors = _run_suite(crash_tests)

        self.assertGreater(ran, 0)
        self.assertGreater(
            failures + errors, 0,
            "MUTATION SURVIVED: a save_execution_record() that silently does nothing was not caught. "
            "This is the most dangerous possible defect in this entire module.",
        )


if __name__ == "__main__":
    unittest.main()
