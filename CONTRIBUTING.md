# Contributing guide

This document defines the engineering workflow used throughout the project: branching strategy, commit conventions, and how work flows from a user story to `main`.

## Branching strategy

The project follows a **trunk-based workflow with short-lived feature branches**.

- `main` is the only long-lived branch and must always be in a working state.
- Direct pushes to `main` are not allowed. All changes reach `main` through a **pull request** that uses the [PR template](.github/PULL_REQUEST_TEMPLATE.md).
- Work happens in short-lived branches created from the latest `main`. They are deleted after merging.

### Branch naming

`<type>/<issue-number>-<short-description>`

| Prefix      | Use                                               | Example                               |
|-------------|---------------------------------------------------|---------------------------------------|
| `feat/`     | New functionality                                 | `feat/4-submission-review`            |
| `fix/`      | Bug fixes                                         | `fix/3-duplicate-hash-check`          |
| `docs/`     | Documentation only                                | `docs/1-architecture-diagram`         |
| `refactor/` | Internal changes without behaviour change         | `refactor/4-nextcloud-client`         |
| `test/`     | Tests only                                        | `test/7-search-integration`           |
| `chore/`    | Maintenance (dependencies, configuration)         | `chore/bump-fastapi`                  |

### Merge policy

- PRs are merged with **squash merge**, so each PR becomes one Conventional Commit on `main`.
- A PR must reference the user story it implements (`Closes #<n>`).

Repository settings (*Settings → Rules → Rulesets* for `main`): require a pull request before merging, block force pushes, and allow only squash merges.

## Commit convention

Commits follow [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/):

```
<type>(<optional scope>): <short imperative summary>

[optional body: what and why, not how]

[optional footer: Closes #12, BREAKING CHANGE: ...]
```

**Types:** `feat`, `fix`, `docs`, `refactor`, `test`, `chore`.

**Scopes:** `watchman`, `librarian`, `observer`, `nextcloud`, `docs`.

Examples:

```
feat(watchman): add SHA-256 duplicate detection to submission checks
fix(librarian): skip empty chunks when indexing scanned PDFs
docs: add NIST SP 500-292 actor mapping
chore: add pull request template
```

## From user story to `main`

1. Pick (or create) a GitHub Issue using the **User story** template. It must have the `user-story` label, acceptance criteria, a checklist of technical tasks, and a milestone (Hito 1–5).
2. Create a branch following the naming convention above.
3. Commit using Conventional Commits; tick the issue checklist as tasks are completed.
4. Open a pull request with the PR template and `Closes #<n>`.
5. Once the checklist is complete, squash-merge and delete the branch.
