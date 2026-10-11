# Test Governance Ledger

Use this file as an index. Keep full canonical test cases under `test-cases/<type>/`, full known-error records under `known-errors/`, and executable product tests in the project's native test directories.

## State meanings

- Specification: `draft`, `derived`, `confirmed`, `superseded`.
- Execution: `not_implemented`, `not_run`, `passed`, `failed`, `blocked`, `stale`.
- `confirmed` means the user confirmed purpose, process, and expected result. It does not mean the test passed.
- `passed` is valid only for the recorded commit or working-tree fingerprint. Related code or contract changes make the result `stale` until re-executed.

## Canonical test cases

| ID | Version | Type | Spec status | Execution status | Source | Test path | Verified against | Record |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |

## Known errors

| ID | Status | Severity | Regression case | Verification or waiver | Record |
| --- | --- | --- | --- | --- | --- |

## Waivers

| ID | Scope | Reason | User-confirmed at | Review condition | Record |
| --- | --- | --- | --- | --- | --- |
