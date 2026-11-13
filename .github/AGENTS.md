# GitHub automation

Root instructions apply. Paths are repository-relative. Keep detailed job settings in workflows
and executable controls, not duplicated here.

## Security and shared setup

- Pin external actions to full commit hashes. Disable persisted checkout credentials.
  Restrict cross-repository tokens to read-only access to the requested repository.
- Never expose secrets to PR code.
- Use least-privilege job permissions.
- A privileged workflow MUST NOT execute code or configuration from an untrusted pull request.

## Self-hosted runners

- Use `arc-runner-set`. Prefer baked-in tools and install only missing requirements.

## Required pre-merge workflow checks

- Build MUST require every selected mandatory job.
- The PR size budget job MUST enforce 1000 added lines, excluding tests and lockfiles.
  Exemptions require a full commit SHA and a reason in `.github/pr-size-exemptions.txt`.

## Advisory automation

- [Slopo](workflows/slopo-review.yml) is advisory, outside pre-merge. Review only same-repository,
  non-draft PRs. Only review gets `pull-requests: write`. Warn on failures.
  Slopo maintains one sticky comment and report/log evidence. It restores model/database caches
  every run and saves them only on main pushes/dispatch.
- [Nightly audit](workflows/dead-code-nightly.yml) defines the schedule, model,
  skill configuration, and timeouts of the LLM-based code cleanup workflow.
  Only the read-only audit job gets `DEEPSEEK_API_KEY`.
  Add project-specific toolchain and dependency setup where marked in the workflow.
  Scan all uploaded evidence for secrets before upload, including evidence from failed and
  no-change runs. Scan raw audit sessions separately. Omit a session when the scan fails.
