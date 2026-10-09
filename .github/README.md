# Running CI

PR tests are manual. Tests on `main` are automatic. Both use Python 3.13 and
run the full pytest suite.

## Before merging a PR

1. Update your branch with the latest `main`, by rebasing or merging it into
   your branch, and push the result.
2. Add a label to the PR to request CI. Any label works; `run-ci` is a useful
   convention. If the label is already attached, remove it and add it again.
3. Wait for `pytest (Python 3.13)` to pass. Merge using **Squash and merge** or
   **Rebase and merge**.

Opening a PR, reopening it, or pushing commits does not start CI. After pushing
more commits, request CI again: a pass on an older commit does not satisfy the
required check. If `main` advances, update your branch and request CI again.
You need permission to apply labels to request a run.

Adding any label starts the full test suite, including labels used for other
purposes. There is deliberately no conditional that skips the required job:
GitHub can treat a skipped job as a successful required check.

The Actions **Run workflow** button is not used. GitHub does not evaluate
`workflow_dispatch` job checks as required PR checks. The manual label action
produces a `pull_request` event, which GitHub does evaluate.

## After merging

Every push to `main`, including a squash or rebase merge, automatically runs
pytest again on the resulting commit. Separate `main` updates have separate
concurrency groups, so a newer update does not cancel an older run. A push
containing multiple commits tests its final state, not every intermediate commit.

A failed post-merge run reports a problem; it does not undo the merge.

## Repository protection

The active `main` ruleset requires a PR, an up-to-date branch, and the
`pytest (Python 3.13)` check from GitHub Actions. Only squash and rebase merges
are allowed. There are no bypass actors, and force pushes and deletion are
blocked. No reviewer approval is required.

These protections are GitHub repository settings, not settings installed by
cloning this repository. The workflow changes take effect once this workflow
is present on the PR branch; automatic post-merge tests become active once the
workflow reaches `main`.

References: [required status checks](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks)
and [workflow events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows).
