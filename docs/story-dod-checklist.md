# Story Definition of Done (DoD) Checklist

---

## 📋 Jira Ticket Format (MANDATORY — applies to ALL new tickets)

Every Jira ticket created for this project **must** include the following sections. No exceptions.

```
### User Story
As a [role], I want [goal], so that [benefit].

### Background
2–4 sentences explaining the context, why this matters, and what exists today.

### Acceptance Criteria
Numbered list. Each item is specific, testable, and unambiguous.
1. ...
2. ...

### Test Cases
Named test cases that map to the ACs above.
- test_[descriptive_name] — what it tests and what it asserts

### Out of Scope
Explicit list of what is NOT included in this ticket.

### Dependencies
Any other tickets that must be done first, or external blockers.
Include a resolution plan for each dependency.

### Notes (optional)
Any design decisions, valid values for env vars, links to relevant code, etc.
```

**Minimum bar:** A ticket without a User Story, Acceptance Criteria, and Test Cases is **not ready for development**. Do not transition to In Progress until all three are present.

---

## Instructions for Developer Agent

Before marking a story as 'Review', please go through each item in this checklist. Report the status of each item (e.g., [x] Done, [ ] Not Done, [N/A] Not Applicable) and provide brief comments if necessary.

> ## 🚨 TAIGA IS NON-NEGOTIABLE
> **Taiga (http://localhost:9000/project/revueio/kanban) is the authoritative project board.**
> Updating Taiga is NOT optional. It is NOT skippable. It is NOT deferrable.
>
> **Rules — no exceptions:**
> 1. Every story marked Done in code MUST be marked Done in Taiga before the session ends.
> 2. Every new story created (in docs or code) MUST be created in Taiga in the same session.
> 3. If Taiga is unreachable: STOP. Tell the human immediately. Do not proceed with closing stories. Wait for Taiga to come back or get explicit human approval to defer — and document the deferral explicitly.
> 4. `kanban-board.md` is a mirror only — it is NEVER a substitute for Taiga.
> 5. The developer agent must confirm Taiga status in their DoD summary before sign-off.

[[LLM: INITIALIZATION INSTRUCTIONS - STORY DOD VALIDATION

This checklist is for DEVELOPER AGENTS to self-validate their work before marking a story complete.

IMPORTANT: This is a self-assessment. Be honest about what's actually done vs what should be done. It's better to identify issues now than have them found in review.

EXECUTION APPROACH:

1. Go through each section systematically
2. Mark items as [x] Done, [ ] Not Done, or [N/A] Not Applicable
3. Add brief comments explaining any [ ] or [N/A] items
4. Be specific about what was actually implemented
5. Flag any concerns or technical debt created

The goal is quality delivery, not just checking boxes.]]

## Checklist Items

1. **Requirements Met:**

   [[LLM: Be specific - list each requirement and whether it's complete]]

   - [ ] All functional requirements specified in the story are implemented.
   - [ ] All acceptance criteria defined in the story are met.

2. **Coding Standards & Project Structure:**

   [[LLM: Code quality matters for maintainability. Check each item carefully]]

   - [ ] All new/modified code strictly adheres to `Operational Guidelines`.
   - [ ] All new/modified code aligns with `Project Structure` (file locations, naming, etc.).
   - [ ] Adherence to `Tech Stack` for technologies/versions used (if story introduces or modifies tech usage).
   - [ ] Adherence to `Api Reference` and `Data Models` (if story involves API or data model changes).
   - [ ] Basic security best practices (e.g., input validation, proper error handling, no hardcoded secrets) applied for new/modified code.
   - [ ] No new linter errors or warnings introduced.
   - [ ] Code is well-commented where necessary (clarifying complex logic, not obvious statements).

3. **Testing:**

   [[LLM: Testing proves your code works. Be honest about test coverage]]

   - [ ] All required unit tests as per the story and `Operational Guidelines` Testing Strategy are implemented.
   - [ ] All required integration tests (if applicable) as per the story and `Operational Guidelines` Testing Strategy are implemented.
   - [ ] **Web E2E tests pass** — run `pytest tests/e2e/ -v` from `src/web/`. All Playwright tests must be green before story is marked Done. If the story touches auth, dashboard, onboarding, runs, analytics, or billing flows — add or update the relevant E2E test in `tests/e2e/`.
   - [ ] **Pipeline / cross-platform E2E** *(required when the story modifies pipeline execution, fallback logic, error handling, or platform-specific behaviour — e.g. rate-limit cascade, comment ordering, deduplication, reply tracking)*:
     - [ ] Live CI run verified on **all active platforms** (Bitbucket, GitHub, GitLab) — actual log output checked, not just green/red status.
     - [ ] If the story introduces a new error path or fallback mechanism: the error condition has been **exercised in at least one live or local-simulation run** (e.g. `pytest -s` with real production code), and the expected log output (warnings, mode transitions, degradation notices) is captured as evidence.
     - [ ] Evidence committed to `docs/` or included in the PR description before merge. Unit test assertions alone are not sufficient — the condition must be *triggered* and the output *shown*.
   - [ ] All tests (unit, integration, E2E) pass successfully.
   - [ ] Test coverage meets project standards (if defined).

4. **Functionality & Verification:**

   [[LLM: Did you actually run and test your code? Be specific about what you tested]]

   - [ ] Functionality has been manually verified by the developer (e.g., running the app locally, checking UI, testing API endpoints).
   - [ ] Edge cases and potential error conditions considered and handled gracefully.

5. **Story Administration:**

   [[LLM: Documentation helps the next developer. What should they know?]]

   - [ ] All tasks within the story file are marked as complete.
   - [ ] Any clarifications or decisions made during development are documented in the story file or linked appropriately.
   - [ ] The story wrap up section has been completed with notes of changes or information relevant to the next story or overall project, the agent model that was primarily used during development, and the changelog of any changes is properly updated.

6. **Dependencies, Build & Configuration:**

   [[LLM: Build issues block everyone. Ensure everything compiles and runs cleanly]]

   - [ ] Project builds successfully without errors.
   - [ ] Project linting passes
   - [ ] Any new dependencies added were either pre-approved in the story requirements OR explicitly approved by the user during development (approval documented in story file).
   - [ ] If new dependencies were added, they are recorded in the appropriate project files (e.g., `package.json`, `requirements.txt`) with justification.
   - [ ] No known security vulnerabilities introduced by newly added and approved dependencies.
   - [ ] If new environment variables or configurations were introduced by the story, they are documented and handled securely.

7. **Documentation (If Applicable):**

   [[LLM: Good documentation prevents future confusion. What needs explaining?]]

   - [ ] Relevant inline code documentation (e.g., JSDoc, TSDoc, Python docstrings) for new public APIs or complex logic is complete.
   - [ ] User-facing documentation updated, if changes impact users.
   - [ ] Technical documentation (e.g., READMEs, system diagrams) updated if significant architectural changes were made.

8. **Git & PR Hygiene:**

   [[LLM: All commits and PRs must follow Conventional Commits with ticket reference. No exceptions.]]

   - [ ] All commits follow Conventional Commits format with ticket number: `type(scope)[TICKET-ID]: description`
     - Examples: `feat(auth)[PROJ-23]: add OAuth2 login`, `fix(billing)[PROJ-45]: correct Stripe webhook handler`
     - Types: `feat`, `fix`, `chore`, `refactor`, `test`, `docs`, `ci`
   - [ ] PR title follows the same format: `feat(scope)[TICKET-ID]: Short description`
   - [ ] Branch name references the ticket: `feat/TICKET-ID-short-description` or `fix/TICKET-ID-short-description`
   - [ ] PR description follows the official template at `.bitbucket/pull_request_template.md` — every required section filled in (🎯 Ticket, 📝 Summary, 🔧 Changes, ✅ Acceptance Criteria, 🧪 Testing, 📊 Impact, 📋 Checklist). See `docs/PR_TEMPLATE_GUIDE.md` for guidance.
   - [ ] PR links back to the ticket (in description or via smart commit if using Jira)

9. **Board Sync (NON-NEGOTIABLE — story is NOT done until PR is merged AND Jira is updated):**

   [[LLM: The correct SDLC order is strictly enforced. A story is not Done until ALL steps below are complete IN ORDER. Never mark Jira Done before the PR is merged. No exceptions.]]

   **Required order — do not skip or reorder:**
   1. [ ] All tests pass (unit, integration, E2E)
   2. [ ] Branch pushed to origin with correct naming: `feat/REVUE-XX-short-description`
   3. [ ] PR opened with title following Conventional Commits: `feat(scope)[REVUE-XX]: description`
   4. [ ] PR passes CI (all builds green, no failures)
   5. [ ] PR reviewed and **merged into main**
   6. [ ] **Only after merge:** Jira ticket transitioned to **Done** via API — confirm with 204 response
   7. [ ] Any NEW stories created during implementation are created in Jira immediately
   8. [ ] Epic status in Jira updated if all stories in the epic are now Done
   9. [ ] `docs/kanban-board.md` updated to mirror Jira (secondary mirror, never a substitute)
   10. [ ] **Jira confirmation statement written in DoD summary** — e.g. "REVUE-65 marked Done on Jira after PR #12 merged at 14:30 UTC."

   **If Jira is unreachable:** STOP. Notify the human. Do NOT mark the story Done. Wait for resolution or get explicit written approval to defer — document the deferral in the session notes.

## Final Confirmation

[[LLM: FINAL DOD SUMMARY

After completing the checklist:

1. Summarize what was accomplished in this story
2. List any items marked as [ ] Not Done with explanations
3. Identify any technical debt or follow-up work needed
4. Note any challenges or learnings for future stories
5. Confirm whether the story is truly ready for review

Be honest - it's better to flag issues now than have them discovered later.]]

- [ ] I, the Developer Agent, confirm that all applicable items above have been addressed.
