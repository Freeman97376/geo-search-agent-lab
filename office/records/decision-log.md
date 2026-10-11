# Agent Governance Decision Log

Record only decisions that should influence future agent behavior. Add a new entry when a decision is superseded; keep the earlier entry intact.

## GOV-001 - Centralize agent governance in office

- Date: bootstrap date
- Status: accepted
- Decision: Keep detailed agent governance in `office/` and use the root `AGENTS.md` as the discovery entry point.
- Reason: One indexed, testable location reduces instruction drift while preserving Codex discovery.
- Consequences: Governance changes must update the relevant record and pass the office validator.

## GOV-002 - Govern behavior changes with reusable test evidence

- Date: bootstrap date
- Status: accepted
- Decision: Use the Office test ledger, governed canonical test cases, known-error lifecycle, and deterministic validator after material behavior changes and bug fixes.
- Reason: Separating user-confirmed contracts from actual execution evidence prevents ambiguous or stale claims of correctness.
- Consequences: Executable tests remain in native project directories; confirmed cases are versioned; verified errors require passed regression evidence or a user-approved manual waiver.
