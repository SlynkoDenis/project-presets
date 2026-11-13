# Structural cleanup audit

Reduce unnecessary implementation and dependency weight left by previously merged changes.

## Inputs

The scope file lists commits, merged pull requests, and changed files.
`DEAD_CODE_BASE_SHA` and `DEAD_CODE_HEAD_SHA` identify the range of recent commits.
These commits prioritize the search for cleanup opportunities. Older code, tests, and live internal
callers remain eligible.

Read applicable repository guidance before editing. Use relevant skills. Delegate independent
discovery and review to subagents when useful.

## Discovery and implementation

Below is a list of common places to search for cleanup opportunities. Use it as a reference
and search for other opportunities exhaustively.

1. Compare declared direct dependencies in the project's manifest files with source, build,
   test, and tooling usage.
   Inspect dependency configs for obsolete entries. Remove proven unused dependencies, update
   each matching lock file with the project's package manager, and remove obsolete suppressions.
2. Inspect sibling methods, modules, and helpers for repeated implementation.
   Reuse existing helpers before adding a small local helper.
   Consolidate only when the result reduces maintenance work without hiding distinct semantics.
3. Find internal forwarding re-exports and unnecessary modules or layers.
   Update all internal consumers together when removing a layer.
4. Inspect tests and fixtures for redundant behavior, obsolete assumptions, and repeated expensive
   setup. Identify tests that check only implementation details instead of required outcomes
   and target them for removal.
   Consolidate equivalent test cases while preserving distinct boundaries, isolation,
   negative controls, and useful diagnostics.
   Before removing coverage, identify the surviving check for each protected failure condition.
   If equivalence is uncertain, verify it with a representative fault or retain the case.
5. Remove dead code, fixtures, configuration, and documentation after searching their consumers.

Do not stop after removing unused dependencies when justified structural cleanups remain.
Recheck the patch for additional cleanup opportunities. Use subagents for rechecks until they
report no reasonable cleanup candidates.

## Boundaries

Preserve signatures and observable behavior for APIs still in use.
Preserve validation, error precedence, concurrency guarantees, and useful test coverage.

Search imports, re-exports, generated code, feature flags, tests, fixtures, configuration, CI,
and documentation.
Update stale guidance and links caused by a removal.
Do not change CI behavior during this cleanup.
Do not add features, formatting churn, or rule suppressions.
Do not weaken tests, lints, or fixtures to make validation pass.

Do not read or print credentials, environment secrets, or API keys.
Do not commit, push, tag, create a pull request, or call another model provider.
The workflow collects the uncommitted patch and publishes a draft separately.

## Verification

Verify code changes according to the project guidelines.

## Report

Write `.dealer-local/dead-code-nightly/report.md` in direct technical English:

- `## Summary`: describe completed cleanups and unresolved failures.
- `## Removed`: describe each before/after structure, affected paths, consumer searches, and actual
  check results.
- `## Added`: explain why each helper or file is necessary.
- `## Not removed`: identify unresolved consumers and the exact check needed to settle them.
