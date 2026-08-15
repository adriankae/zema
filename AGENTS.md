# Agent Instructions

## Delivery roles

- Hermes is the primary orchestrator. Hermes plans the work, assigns bounded implementation slices, verifies evidence, and performs the final review.
- Codex is the implementation agent. Run Codex with model `gpt-5.6-luna` and maximum reasoning effort.
- Codex does not approve its own work. Hermes reviews the resulting diff and test evidence before acceptance.
- Keep implementation slices small, independently verifiable, and aligned with the configured issue tracker.

## Agent skills

### Issue tracker

Issues and specs live in GitHub Issues for `adriankae/zema`. See `docs/agents/issue-tracker.md`.

### Triage labels

The repository uses the standard engineering-skill label vocabulary. See `docs/agents/triage-labels.md`.

### Domain docs

The repository uses a single-context domain documentation layout. See `docs/agents/domain.md`.
