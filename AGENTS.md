# Project instructions

Read [the project constitution](docs/constitution.md) before planning or implementing features. Identify conflicts with it before changing behavior. Keep feature-specific tasks and implementation details in separate plans.

## Module layout and ownership

Follow the architecture and language requirements in the constitution. Use this layout as modules are implemented. The paths below are conventions, not claims that these modules already exist.

- `frontend/` contains the TypeScript frontend and its dependencies, configuration, and tests.
- `services/powerpoint-extraction/` contains the independently startable Python PowerPoint extraction service.
- `services/<service-name>/` contains each additional Python backend service, named for its responsibility.
- `docs/` contains project rules, architecture decisions, feature plans, and deployment documentation.

Create module folders when implementing their responsibilities. Keep module-specific source, tests, dependency manifests, and configuration inside the owning folder. Keep only repository-wide tooling and local service orchestration at the root. Do not split every helper into a service or add empty placeholder services.

Give each module a README that describes its responsibility, public interface, required configuration, dependencies, and commands to run and verify it independently. Backend services must start without launching the entire application. Document any services needed to exercise their workflows.

## Service communication and data

Use REST over HTTP between application modules. Keep endpoints and credentials configurable. Document request and response schemas, status codes, and errors in the owning service's API contract. Update affected clients and contract checks when changing that contract.

Validate requests and external responses at service boundaries. Set timeouts on outbound requests and return actionable errors. Retry only operations that are safe to repeat. Long-running indexing must return a job identifier and expose status, failure, and retry behavior through its API.

Keep database access and migrations inside the service that owns the data. Do not import another service's internal code, query its tables, or read its private storage to bypass its API. Shared contract definitions must not become a shared business-logic package.

Plan retries, reprocessing, and deletion across all affected services. Prevent duplicate searchable slides and prevent an in-flight job from recreating a deleted deck's content. Record any communication exception and its reason in the relevant plan before using it.

## Coding norms and verification

Use TypeScript for frontend code and Python for backend services. Enable strict TypeScript checking. Add Python type annotations to service interfaces and new or changed function signatures. Validate external data at runtime rather than treating type annotations as validation.

Use descriptive names and the smallest complete implementation. Reuse existing module patterns and installed dependencies before introducing new abstractions or packages. Keep business logic separate from HTTP handlers and external integrations. Comment only when the reason for a choice is not clear from the code.

Before changing behavior, inspect callers, API clients, tests, fixtures, configuration, and deployment instructions. Update every affected part in the same change. Keep secrets out of source control, API responses, and logs.

For new nontrivial behavior and bug fixes, add a focused automated check of observable behavior. For service integrations, check the API contract and relevant failure behavior. Run affected module checks and verify cross-service workflow changes on the running local system. For documentation-only changes, check the actual diff, links, and consistency with the constitution.

Keep dependency choices, endpoint details, implementation tasks, and measured performance results in the relevant module documentation or feature plan. Do not add them to the constitution.

## Current library documentation

Use the `ctx7` CLI for current library, framework, SDK, API, CLI, or cloud-service documentation. Resolve the library first with `npx ctx7@latest library <name> "<question>"`, then fetch documentation with `npx ctx7@latest docs <libraryId> "<question>"`. Use the full specific question, avoid sensitive data, and run no more than three commands per question. A supplied `/org/project` ID can skip resolution.

Prefer official, relevant, reputable matches and version-specific IDs when needed. Do not use Context7 for general programming concepts, business-logic debugging, code review, refactoring, or scripts written from scratch.

Run Context7 requests outside the default sandbox where permitted. If network restrictions prevent access, report the limitation. For quota errors, report them and suggest `npx ctx7@latest login` or `CONTEXT7_API_KEY`. Do not silently substitute remembered documentation.
