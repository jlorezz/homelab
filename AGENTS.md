# Homelab agent configuration

Read `CLAUDE.md` for repository architecture and operational commands. Preserve
existing uncommitted work. Infrastructure activation and application deployment
are separate from editing their definitions.

The shared runtime policy is `stacks/agents/policy/AGENTS.md`; read it when working
on the agent platform. `stacks/agents/README.md` documents deployment and recovery.
Custom skills are authored under `stacks/agents/policy/skills/` and exposed through
`.agents/skills/`. Do not copy upstream skill libraries or edit generated runtime
copies. Validate agent changes with `python3 stacks/agents/doctor.py --source-only`
and `python3 -m unittest discover -s stacks/agents/tests -v`.

Dealpipe owns its application instructions and CI workflows. Read its current
`AGENTS.md` before application work. Never change its production or branch
protection settings as a side effect of a homelab deployment.
