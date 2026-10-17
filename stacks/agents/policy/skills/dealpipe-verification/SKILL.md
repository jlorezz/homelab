---
name: dealpipe-verification
description: Select and report existing Dealpipe checks for an implementation or requested review.
---

Read Dealpipe's current `AGENTS.md` and relevant area/rule documents. Its commands
and runtime policy supersede examples here. Run existing checks; test changes need
the authorization required by that repository.

Match verification to the change: Ultracite/lint and affected typechecks; tenant,
authorization, GraphQL, migration and billing checks when touched; a production
build and real-app browser evidence for visible behavior. Respect the shared
workload guard. Do not start or reset database stacks without authorization.

The NAS image provides Bun and Node; its versions must match the application's
declared package manager/runtime before installing dependencies. Heavy browser
and integration verification runs on CI's disposable services. The T3 preview is
the first browser choice for interactive inspection when available.

Report commit, check scope, failures, explicit skips, and evidence. A passing draft
profile is not full regression. AI review findings need a location, concrete
failure scenario, severity and evidence; never turn an aggregate score into
permission to merge. Review priorities: cross-org access, German RBAC role floors,
trusted-document compatibility, safe migrations, webhook/workflow replay, and
API/web deployment ordering.
