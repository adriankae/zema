# Issue tracker: GitHub

Issues and specs for this repository live as GitHub issues. Use the `gh` CLI for all operations.

Repository: `adriankae/zema`

## Conventions

- Create, read, update, comment on, label, and close issues with `gh issue`.
- Infer the repository from the current checkout when possible.
- Pull requests are not a request or triage surface.
- When a skill says “publish to the issue tracker”, create a GitHub issue.
- When a skill says “fetch the relevant ticket”, read the full issue body, comments, and labels.
- Use native GitHub issue dependencies for blocking relations where available.
- If native dependencies are unavailable, record `Blocked by: #<issue>` in the issue body.
- A ticket is ready only when all blocking issues are closed.

## Pull requests as a triage surface

PRs as a request surface: no.
