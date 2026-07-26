# Release Policy

This document establishes the permanent release criteria for every future phase of this platform, starting with v0.5. It exists so that "frozen" means the same, verifiable thing every time, not a judgment call that varies by phase or by mood.

## Release Criteria

A phase may only be tagged and frozen when **all** of the following are true:

1. **All tests pass.** The complete suite (`python -m unittest discover -s tests`) exits clean, with zero failures and zero errors.
2. **All regression tests pass.** Every defect found during implementation or adversarial review - in this phase or any prior one - has a dedicated regression test, and it passes. A defect without a regression test is not considered fixed for release purposes, regardless of whether the underlying code was patched.
3. **No Critical or High severity issues remain open.** A Critical issue is one that could cause capital loss, a manual-selling-rule violation, silent data corruption, or a security exposure. A High issue is one that could produce a materially wrong result without an error or warning. Both block a release; neither may be deferred to "a future pass" without an explicit, written, user-approved exception.
4. **Documentation is fully updated and internally consistent.** The design/objectives document, the production-readiness report, and CHANGELOG.md must agree with each other and with the actual code - same test counts, same defect list, same status language. A stale "pending review" sentence left in one document after another document says "complete" is itself a release blocker.
5. **Reproducibility is verified.** The exact git commit hash, config version, and data snapshot id (where applicable) for the release must be capturable and demonstrated working against the actual repository state at release time - not merely asserted to exist as a feature.
6. **Repository consistency has been verified**, specifically:
   - No frozen interface from an earlier phase has been modified, checked via `git diff <previous-tag> --stat` against every previously-frozen package, not asserted from memory.
   - No `TODO`, `FIXME`, or placeholder/stub code remains in anything being released.
   - Version numbers, module numbers, and architecture section references are consistent everywhere they appear.
7. **Final release metadata is recorded** - see the Release Record format below - and committed before the tag is created.

## What Counts as a "Genuine Adversarial Review"

Per the standard set at Phase 4 and repeated at Phase 5: a review is not "I re-read the code and it looks right." It is a deliberate, hostile attempt to break the software - constructing adversarial inputs, boundary values, concurrent access, and constraint conflicts, and running them for real. A review that finds zero defects on the first pass should be treated with suspicion, not celebrated - it more often means the attack wasn't aggressive enough than that the code is flawless. Both Phase 4 and Phase 5's reviews found real, meaningful defects; that is the expected and healthy outcome of doing this properly.

## Post-Freeze Change Policy

Once a phase is frozen:

- **Only genuine production-critical bug fixes are permitted** without further process - a bug that could cause capital loss, a manual-selling-rule violation, a crash, or a security exposure, discovered after the freeze.
- **No feature additions, architectural changes, or interface changes** are permitted on a frozen phase without an explicit architecture amendment (following the same pattern already established: PHASE1_Architecture_SRS.md sections 14/15/16) and your explicit approval, recorded the same way those were.
- Any post-freeze fix must itself follow this same policy: a regression test, a CHANGELOG entry, and a re-verification that the fix didn't touch anything outside its own frozen phase's boundary.
- A post-freeze fix does not retroactively "unfreeze" the phase for other changes - it is a narrow, logged exception, not a reopening.

## Release Record Format

Recorded in CHANGELOG.md at the top of each frozen version's entry:

```
Version: v<X.Y> (Frozen)
Frozen date: <date>
Git commit: <full commit hash>
Test suite: <N> tests, all passing
Regression tests: <N> (one per historical defect across all phases to date)
Critical/High issues open: 0
Frozen interfaces from prior phases modified: 0 (verified via git diff)
Documentation consistency: verified (design doc, readiness report, CHANGELOG cross-checked)
```

## Why This Document Exists

Every phase in this platform so far has found real defects when genuinely tested adversarially - nine in Phase 4, five in Phase 5 (one during implementation, four during the dedicated review), plus thread-safety and resource-leak issues in Phase 2. That pattern is the reason this policy exists: "looks done" and "verified done" have consistently been different things on this project, and the gap between them is exactly what has protected real capital-loss risks from reaching a frozen state. This document makes that verification a checklist, not a one-off effort that has to be reinvented and re-argued for every phase.
