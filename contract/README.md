# Contract versions

The gateway's `/mcp` contract is versioned independently of package
versions. Every `/mcp` response carries `X-Swarm-Contract: N` and
`initialize` reports `serverInfo.contractVersion: N`.

## Rules

- **Additive** (new optional fields, new tools, new error codes, new
  fixtures): same version. Consumers keep working; fixtures grow.
- **Breaking** (remove/rename a field, change semantics, new required
  params): new version (`X-Swarm-Contract: N+1`, new `contract/vN/`
  directory), announced in advance on the consumer ticket
  (Heretek-AI/IUMBTEMS#107). The old version stays served until consumers
  migrate (decided at bump time).
- Every behaviour change ships regenerated fixtures in the same change
  (`test_contract_drift.py` enforces this).

## Layout

`contract/vN/fixtures/<area>/<case>.json` — golden
`{contract_version, request, response_status, response_headers,
response_body}` pairs; each version has a `README.md` with vendoring
instructions. Current: [`v1/`](v1/README.md).
