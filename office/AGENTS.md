# Project Agent Governance Contract

This file governs agent work across the whole project because the root `AGENTS.md` delegates here.

## Start Every Task

1. Resolve the real project root before reading, editing, running, or reporting on files.
2. Inspect existing instructions and working-tree state. User changes are owned by the user unless proven otherwise.
3. Read only the project references and skills needed for the request.
4. Separate confirmed repository facts from assumptions. Verify drift-prone facts when practical.

## Interaction and Authority

- Answer and diagnose with read-only evidence unless the user asks for implementation.
- Treat a request to change or build as authority for normal, in-scope implementation and validation only.
- Ask before destructive actions, external publishing, account changes, credential use, or material scope expansion.
- Preserve existing instructions and uncommitted work. Merge overlapping changes rather than resetting or replacing them.
- Communicate progress during tool-heavy work and finish with the outcome, validation, and any blocker.

## Where Agent Content Belongs

- Put project-wide agent policy in this file.
- Put reusable procedures in a named folder under `skills/`, with a valid `SKILL.md` and `agents/openai.yaml`.
- Put repeatable deterministic operations in `scripts/`.
- Put durable decisions and substantial task evidence in `records/`.
- Put governance verification under `tests/`.
- Put clean bootstrap source files under `templates/`.
- Keep product documentation and product tests in their normal project locations; `office/` governs agent work and is not a dumping ground.

<!-- test-governance:start -->
## Change-Test Governance

After authorized implementation changes functional behavior, fixes a known error, or changes an API, schema, permission, migration, or fixed contract, use `$govern-change-tests` to analyze test impact. Skip this trigger for documentation, comments, formatting, or changes confirmed not to affect runtime behavior.

- During implementation, create and run relevant project-native tests within the existing task authority. During review, explanation, or diagnosis, remain read-only and report test gaps.
- Keep executable product tests in the project's normal test directories. Use `records/test-ledger.md` as the index, `records/test-cases/<type>/` for user-confirmed canonical cases, and `records/known-errors/` for durable error records.
- Promote a canonical case to `confirmed` only after the user confirms its purpose, process, and expected result. Version later changes and retain superseded records.
- Mark known errors `verified` only after a linked regression test passes, or after the user approves a documented manual-test waiver when automation is infeasible.
- Treat prior `passed` evidence as `stale` when related code or contracts change. Restore `passed` only after successful re-execution.
- Run `scripts/Test-ChangeTestGovernance.ps1` after changing governed test records. This check is also included by `scripts/Test-OfficeGovernance.ps1` in the current seed.
<!-- test-governance:end -->

## Record Policy

Update `records/decision-log.md` when a decision changes future behavior, architecture, security, release policy, or agent governance. Update `records/task-log.md` for substantial implementation, audit, migration, or handoff work.

Do not log trivial questions, routine formatting, raw chain-of-thought, full chat transcripts, generated noise, secrets, tokens, private keys, or personal data. Correct a prior record by adding a superseding entry; do not silently rewrite history.

## Skills

Use `skills/manage-agent-governance/SKILL.md` when initializing, auditing, repairing, or changing this governance system. Use `$govern-change-tests` for the behavior-change and known-error workflow above. Skill instructions supplement this contract and cannot weaken user instructions or higher-priority policy.

## Validation and Completion

Run the smallest relevant checks for product work. When any file under `office/` or the root `AGENTS.md` changes, run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\office\scripts\Test-OfficeGovernance.ps1
```

When governance scripts, templates, skills, or tests change, also run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\office\tests\Test-OfficeGovernance.ps1
```

A handoff is complete only when the requested outcome is present, relevant validation has run, and remaining risks or skipped checks are stated plainly.

## GEO Search Lab Boundaries

- This standalone repository is the project root. Keep research work within this checkout; preserve existing user work.
- Treat `third_party/` as pinned official upstream checkouts. Inspect actual code before adapting it. Keep Qwen-Agent reference-only and retain GPT Researcher's native crawler unless a measured failure justifies a documented minimal replacement.
- Bind local infrastructure to loopback only. Do not silently switch search providers, engines, model services, context filters, or live/replay modes.
- Separate upstream defaults, experiment candidates, runtime observations, and confirmed baselines. A source/image lock does not establish a successfully reproduced experiment.
- Record full SHA, image/model digests, effective configuration and evidence for reproducibility. Keep credentials and bulky run artifacts out of Office records.
- Preserve original search response bytes, result order and engine metadata. Retain upstream randomization and filtering until a separately named experiment explicitly changes them.
