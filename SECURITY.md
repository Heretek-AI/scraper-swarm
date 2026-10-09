# Security policy

## Reporting

**Do not open a public issue for a suspected vulnerability.** Use GitHub's
private vulnerability reporting ("Security" tab → "Report a vulnerability")
on this repository so details stay confidential until a fix ships.

Include: affected component/version/commit, reproduction steps or proof of
concept, impact assessment, and any suggested fix. GPG-encrypted mail is
accepted if you already have a maintainer key; otherwise the private
advisory channel is preferred over email.

## Scope

In scope: the gateway (`/mcp` auth, scopes, SSRF/egress enforcement),
`panel-api` (sessions, key issuance, audit chain), `swarmd` rendering and
policy checks, `deploy/` proxy/compose wiring, and the OpenCode plugin
guard. Out of scope: upstream engine CVEs (report those upstream, but tell
us if our defaults expose them), and social engineering.

## Response

Maintainers acknowledge within 3 business days, ship a fix on a private
branch, and credit the reporter in the CHANGELOG unless anonymity is
requested. Automated scanners: test against your own deployment, never
against shared infrastructure.
