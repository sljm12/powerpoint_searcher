# Project instructions

Read [the project constitution](docs/constitution.md) before planning or implementing features. Identify conflicts with it before changing behavior. Keep feature-specific tasks and implementation details in separate plans.

## Current library documentation

Use the `ctx7` CLI for current library, framework, SDK, API, CLI, or cloud-service documentation. Resolve the library first with `npx ctx7@latest library <name> "<question>"`, then fetch documentation with `npx ctx7@latest docs <libraryId> "<question>"`. Use the full specific question, avoid sensitive data, and run no more than three commands per question. A supplied `/org/project` ID can skip resolution.

Prefer official, relevant, reputable matches and version-specific IDs when needed. Do not use Context7 for general programming concepts, business-logic debugging, code review, refactoring, or scripts written from scratch.

Run Context7 requests outside the default sandbox where permitted. If network restrictions prevent access, report the limitation. For quota errors, report them and suggest `npx ctx7@latest login` or `CONTEXT7_API_KEY`. Do not silently substitute remembered documentation.
