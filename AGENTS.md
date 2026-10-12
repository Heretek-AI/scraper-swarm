# AGENTS.md

## Evidence

Back every claim about behavior with something executed or read: test output,
command output, a file and line. State what was not run. A ticket closes with
a validation comment: before and after test evidence, drift found, follow-ups.

## Session protocol

- One session works one ticket. A ticket's "Session prompt" is self-contained;
  follow it, and start only when every ticket in its "Blocked by" list is closed.
- A ticket's "Current state" section was true at the commit it names.
  Re-verify it before acting on it.

## Canonical surfaces

- **The `/mcp` contract** is what outside clients depend on.
  Heretek-AI/IUMBTEMS vendors `contract/v1/fixtures/` at a recorded commit.
  Any change to `/mcp` behavior ships regenerated fixtures in the same change
  (`uv run python apps/gateway/tests/gen_contract_fixtures.py`). An additive
  change keeps the version; a breaking change needs a new contract version and
  advance notice on the consumer. The rules are in `contract/README.md`.
- **`.roadmap/<phase>/`** holds recorded factory evidence (`GOAL.md` and
  `dossier.json` in each phase directory).
  It stays byte-identical, and the hygiene check skips it. A
  `brain:<id>/<file>::<anchor>` citation in source points at a planning note
  kept outside the repository; the quoted text is in the matching dossier.
- **The release version** is `gateway.__version__` in
  `apps/gateway/src/gateway/__init__.py`. Contract versions are independent of it.
- **The three Python apps are one unit.** The gateway imports
  `panel_api.audit` and `panel_api.ssrf_guard`, panel-api imports
  `swarmd.catalog`, gateway and panel-api open the same SQLite file, and the
  Dockerfiles in `deploy/` build from the repo root. panel-api finds
  `catalog/` by walking up from its own file (`SWARM_REPO_ROOT` overrides), so
  moving a source file can break startup.

## Commands

- Run every command in `.github/workflows/ci.yml` before a push.
- CI's Python suite covers `apps/` only. `tests/security/test_ssrf.py` needs
  Docker and runs in `.github/workflows/ssrf-nightly.yml`, weekly and on demand.

## Invariants

| Invariant | Where |
| --- | --- |
| Published contract fixtures equal what the gateway produces now, compared as parsed JSON | `apps/gateway/tests/test_contract_drift.py` |
| Every fetch path passes a fail-closed SSRF pre-check | `apps/panel-api/src/panel_api/ssrf_guard.py`, `apps/panel-api/tests/test_ssrf_guard.py` |
| Engines reach the internet only through the Smokescreen egress proxy | `deploy/`, `tests/security/test_ssrf.py` |
| The audit log is hash-chained and scrubs secrets | `apps/panel-api/src/panel_api/audit.py` |

## Workflow

- Work on a topic branch from `origin/main` and open a pull request. A
  maintainer merges and cuts release tags; automation never pushes `main` or
  creates a tag.
- Commit history stays as it is: consumers pin fixtures by commit.
- Stage explicit paths.
- After a push, check every workflow run.
- Write repo-relative paths in tracked files, and `owner/name` for another
  repository.
