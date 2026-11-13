# Dealer

## Development rules

- When developing code, keep the cumulative added non-test SLOC under 1000. If the changes cannot
  stay under this limit, use `.github/pr-size-exemptions.txt` to exempt the PR size budget check.

## Local verification

- Install git hooks with `make setup-pre-commit` and run them with `pre-commit run --all-files`.
