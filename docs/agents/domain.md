# Domain Docs

Zema uses a single-context domain documentation layout.

## Before architecture, specification, or implementation work

- Read `CONTEXT.md` at the repository root when it exists.
- Read relevant ADRs under `docs/adr/` when they exist.
- Use the domain vocabulary defined in `CONTEXT.md`.
- Explicitly report conflicts with an existing ADR instead of silently overriding it.

`CONTEXT.md` and ADRs are created lazily when domain terms or decisions are settled. Their absence is not an error and does not block exploration.
