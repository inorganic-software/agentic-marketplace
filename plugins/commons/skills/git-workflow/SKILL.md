---
name: git-workflow
description: How to operate git in this repo using trunk-based development, Conventional Branch names and Conventional Commits. Use when creating branches, writing commit messages or PR titles, syncing with main, opening pull requests, or merging.
---

# Git workflow

This repo uses **trunk-based development (TBD)**. `main` is the trunk: it is the single source of truth and must always be releasable.

## Rules

- Never commit or push directly to `main`. All changes land through a pull request.
- Never force-push `main` or rewrite its history.
- Keep branches short-lived: merge within a day or two. If a change is bigger, split it.
- Keep pull requests small and focused on one change.
- Unfinished work that must merge early goes behind a feature flag, not a long-lived branch.

## Flow

1. **Start from the latest trunk**

   ```sh
   git switch main
   git pull --ff-only
   git switch -c <type>/<description>   # e.g. chore/add-codeowners
   ```

   Branch names follow **Conventional Branch**:

   - Format: `<type>/<description>`, lowercase, words separated by hyphens.
   - Types: `feature/` (or `feat/`), `bugfix/` (or `fix/`), `hotfix/`, `release/`, `chore/`, and the other Conventional Commits types: `docs/`, `style/`, `refactor/`, `perf/`, `test/`, `build/`, `ci/`, `revert/`. Use the type the PR title will have.
   - Optionally include the ticket id: `feat/issue-123-add-login`.
   - `release/` may use dots for versions: `release/v1.2.0`.

2. **Commit small, working increments**

   Each commit should build and pass tests. Messages follow **Conventional Commits**:

   ```text
   <type>[(scope)][!]: <description>

   [optional body: why the change was made]

   [optional footer(s), e.g. BREAKING CHANGE: ..., Refs: #123]
   ```

   - Types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`, `revert`.
   - Description: imperative, lowercase, no trailing period, under ~72 chars.
   - Mark breaking changes with `!` after the type/scope or a `BREAKING CHANGE:` footer.
   - Example: `chore: add CODEOWNERS assigning the ADLC team to the whole repo`
   - If the work follows a defined process, such as RPI, end every commit with a `Process: <name>` and a `Stage: <stage>` trailer. The squash commit on `main` is built from the branch's commit messages, so it keeps them, and `git log --grep '^Process: rpi$'` lists the work done that way, even after the process changes.

3. **Stay in sync with trunk** — rebase, don't merge `main` into your branch:

   ```sh
   git fetch origin
   git rebase origin/main
   git push --force-with-lease   # only on your own branch
   ```

   Rebase often: small, frequent syncs keep conflicts small. If the rebase stops on a conflict, see **Resolving conflicts** below.

4. **Open a pull request into `main`**

   ```sh
   git push -u origin HEAD
   gh pr create --base main
   ```

   - The PR title must follow Conventional Commits with a scope, because it becomes the squash commit on `main`:

     ```text
     <type>(<scope>): [<TASK-ID> ]<description>
     ```

     Use a scope that identifies the affected product, component, or area. Keep the type and description conventions from the commit rules above. For example: `feat(collector): init configuration` or `chore(infra): change admin users`.
   - If the work has a Linear issue, start the description with its ID, keeping its case, so the commit on `main` points to the issue. For example: `docs(data-lake): ABC-123 research raw buckets for pub/sub telemetry`.
   - If the work follows a defined process, add the `process: <name>` label, such as `process: rpi`, so its PRs can be listed with `gh pr list --label "process: rpi" --state all`.
   - Review is required from the code owners in `.github/CODEOWNERS`.
   - Add the `do not merge` label to stop a PR from merging. The `do-not-merge label` check fails while the label is on the PR, and the "Required checks" ruleset requires that check to pass. Remove the label to unblock the PR.
   - The "Required checks" ruleset on `main` lists every check a PR must pass, such as `do-not-merge label` and `terraform lint`. It has no bypass actors. Add new mandatory checks to it rather than creating another ruleset.

5. **Merge and clean up**

   - Squash-merge once approved and checks pass.
   - Delete the branch locally and on the remote.

   ```sh
   gh pr merge --squash --delete-branch --auto
   git switch main && git pull --ff-only
   ```

   If on a worktree:
   - Remove the worktree
   - Move to `main`
   ```sh
   wt rm -d # remove the worktree (default: current one) and delete its branch, if it is merged
   ```

## Resolving conflicts

Conflicts show up while rebasing onto `main`. Resolve them one commit at a time:

1. **See what conflicts**

   ```sh
   git status
   git diff --name-only --diff-filter=U   # conflicted files only
   ```

2. **Understand both sides before editing.** During a rebase the sides are swapped compared to a merge:
   - `ours` / `HEAD` is `main` plus the commits already replayed.
   - `theirs` is your commit being applied.

   Use `git log -p origin/main -- <file>` to see why `main` changed.

3. **Resolve by intent, not by picking a side.** Keep what `main` changed and reapply your change on top of it. Never silently drop someone else's changes from `main`. Regenerate lockfiles and other generated files instead of editing them by hand.

4. **Verify, then continue**

   ```sh
   git diff --check          # fails if conflict markers remain
   # build and run tests
   git add <file>...
   git rebase --continue
   ```

5. **Push** once the rebase finishes: `git push --force-with-lease`.

If a resolution is unclear, or the two changes contradict each other, run `git rebase --abort` to go back to where you started and ask a human. Don't guess.
