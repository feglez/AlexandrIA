---
title: "[US] Set up the repository engineering workflow"
milestone: "Hito 1 - Project definition"
labels: "user-story,docs"
---
## User story

**As a** developer,
**I want** issue and pull-request templates, a branching strategy and a commit convention,
**so that** every change is traceable from a user story to the commit that implements it.

**Affected components:** repository

## Acceptance criteria

- [ ] **Given** a new issue, **when** "User story" is chosen, **then** the template pre-fills the story, acceptance criteria and task checklist and applies the `user-story` label.
- [ ] **Given** a new pull request, **when** it is opened, **then** the PR template is pre-filled.
- [ ] **Given** the `main` branch, **when** someone tries to push directly, **then** the push is rejected and a PR is required.
- [ ] **Given** CONTRIBUTING.md, **when** a contributor reads it, **then** the branch naming and Conventional Commits rules are documented with examples.
- [ ] **Given** the issue tracker, **when** it is opened, **then** one milestone per Hito exists and every user story is assigned to one.

## Technical tasks

- [ ] Add a `.gitignore` for the Python stack
- [ ] Add `.github/ISSUE_TEMPLATE/user_story.md`
- [ ] Add `.github/PULL_REQUEST_TEMPLATE.md`
- [ ] Write CONTRIBUTING.md
- [ ] Configure a ruleset protecting `main` (PR required, no force push, squash merge only)
- [ ] Create labels and milestones, and load the initial backlog

## Definition of done

- [ ] All acceptance criteria are met and demonstrated.
- [ ] Changes merged into `main` through a pull request that references this issue.
