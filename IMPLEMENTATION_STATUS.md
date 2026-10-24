# Private Hermes MCP management — status (FINAL: deployed and installed)

## Outcome

Mission Control (rebranded "Unfold") is installed and natively verified for both
Jarvis and Kyly on Aeris, via a real supported deployment of the reviewed private
`install_custom` route — not a config bypass. Dwayne confirmed `kylie` is Kyly
(TTS misspelling), and confirmed "Unfold" is Mission Control's own rebrand, both
authorizing this exact action and target.

## What is live right now, on Aeris

- Container `hermes-agent` (compose service `hermes-agent` in
  `/mnt/terradice/docker/hermes-agent/`, which also runs the `tailscale-hermes`
  sidecar) now runs image `local/hermes-agent-private-mcp:test-20261023`, built
  from this branch's exact reviewed commit, in place of
  `nousresearch/hermes-agent:latest`. Swap done via
  `docker-compose.override.yml` in that directory (see Rollback below).
- `install_custom` is present and working in that image; confirmed by two full,
  real end-to-end installs (not fixtures) below.
- Mission Control/Unfold backend: container `mission-control`
  (`mission-control:unfold-4.2.1-0857035`), reachable from `hermes-agent` via
  Docker DNS at `http://mission-control:3838` (both containers share the
  `chatterbox_default` and `proxynet` bridge networks). `127.0.0.1:3838` does
  NOT work from inside `hermes-agent` (separate network namespace from the
  host) — use the DNS name, not host-loopback.
- Canonical MCP server source: copied from
  `/mnt/data/Dev/projects/mission-control-mcp-v1/mcp-servers/mission-control`
  (repo `DiceBanga/mission-control`, commit `0857035`, exactly matching the
  live Unfold image tag) to Aeris host path
  `/mnt/terradice/docker/hermes-agent/private-mcp-servers/mission-control/`
  (visible in-container at `/opt/data/private-mcp-servers/mission-control/`),
  owned `10010:10010`, no symlinks. A dedicated venv was built there inside the
  container (`.venv`, Python 3.14.7 matching the container interpreter) with
  the pinned `mcp>=1.30,<2` / `httpx` deps from that project's own
  `requirements.txt` (`mcp==1.30.0` resolved).
- Both `jarvis` and `kylie` profile `.env` files got two new lines each
  (profile-isolated, independently added, identical values):
  `MC_BASE_URL=http://mission-control:3838` and
  `MISSION_CONTROL_PYTHONPATH=/opt/data/private-mcp-servers/mission-control`.
- Manifest (`schema_version=1`, name `mission-control`, stdio transport,
  `command=/opt/data/private-mcp-servers/mission-control/.venv/bin/python3`,
  `args=[-m, mission_control.server]`, `env` as `${MC_BASE_URL}` /
  `${MISSION_CONTROL_PYTHONPATH}` references only — no literal secrets),
  sha256 `3b0b876e54a3f78f82c727eca71383d00365c5180bc6e5c5b372069118befabf`,
  with companion Skill digest
  `1925ee28ec58bfd70b32029e87e4faed65625a8a691d165243249272cce98f33`,
  saved at `/opt/data/private-mcp-servers/mission-control-manifest.json`.

## Per-profile install evidence (real, not fixture)

Both ran via `tools/connectors/private_mcp.run_private_mcp_operation(["mission-control"],
connection_callback=None, ...)` — the exact code path a live `manage_connections
action=install_custom` tool call takes, executed as the container's own
`hermes` user (uid 10010), inside `hermes-agent`.

- **Jarvis** (`HERMES_HOME=/opt/data/profiles/jarvis`): registered, then
  installed — `state: connected`, 10 real MCP tools discovered via a genuine
  stdio handshake (`mc_health`, `mc_list_missions`, `mc_get_mission`,
  `mc_create_mission`, `mc_update_mission`, `mc_list_runs`, plus
  prompts/resources tools). Companion Skill `status: installed`, readback
  confirmed (`skills/mission-control/SKILL.md` present on disk, 1554 bytes).
  Saved into `config.yaml` under `mcp_servers.mission-control`.
- **Kyly** (`HERMES_HOME=/opt/data/profiles/kylie`): same registration +
  install, same 10 tools discovered, same companion Skill installed and read
  back. Independent registry/config entries — no shared installation state
  between the two profiles, only the shared read-only source/venv on disk.
- Direct live backend check (same `mission_control.client.MissionControlClient`
  the MCP server wraps, run inside `hermes-agent` against
  `http://mission-control:3838`): `health()` → `{"healthy": true, "capabilities":
  {"missions": true, "runs": true, "kanban": false, "execution_dispatch": false}}`;
  `list_missions()` → 23 real missions read back, including Unfold's own
  mission record for this exact task
  (`install-mission-control-mcp-server-and-skill-for-agents-7f0g`, agent
  `kyly`, status `blocked`).
- Gateway health after both installs and after the container swap/restart:
  `curl http://127.0.0.1:8642/health` → `{"status": "ok"}`. All four served
  profiles (`default`, `jarvis`, `kira`, `kylie`) started cleanly.
- Writes remain gated: `MC_ALLOW_WRITES` was never set, matching Unfold's own
  mission goal text ("verify mc_health, mission list and readback per agent;
  document writes gate MC_ALLOW_WRITES for later enablement"). Nothing wrote
  to Unfold; `mc_create_mission`/`mc_update_mission` are discovered but unused.

## Issue found and fixed during deployment: stale gateway patch

Aeris's `hermes-agent` compose folder documents a required patch
(`patches/api_server.py`, applied via `docker cp` after every image update,
per `patches/README.md`) for a custom voice-worker reasoning-effort/iteration
header override. Reapplying that **old** patch file against this **newer**
codebase broke the API server:
`APIServerAdapter.connect() got an unexpected keyword argument 'is_reconnect'`.
Fixed by restoring the new image's own pristine `api_server.py` (extracted via
`docker create`+`docker cp` from the image, not `docker exec ... cat` over
SSH, which truncated the file once on a pipe write error — verify with
`python -m py_compile` / `ast.parse` before trusting any such extraction).
**The custom voice-header override patch is currently NOT applied** — it needs
to be rewritten against the new `api_server.py` before reapplying; flag to
Dwayne before doing that rewrite.

## Backups and rollback

- Full backup taken before the image swap:
  `/mnt/user/appdata/private-mcp-build-staging/backups/profiles-jarvis-kylie-pre-swap.tar.gz`
  (8.5GB, both profiles' full directories, pre-swap).
- Full `docker inspect hermes-agent` snapshot (pre-swap):
  `/mnt/user/appdata/private-mcp-build-staging/hermes-agent.inspect.json`.
- **Rollback the image swap:** `rm /mnt/terradice/docker/hermes-agent/docker-compose.override.yml`
  then `cd /mnt/terradice/docker/hermes-agent && docker compose up -d hermes-agent`
  — returns to `nousresearch/hermes-agent:latest` with identical mounts/env.
  Note: this does NOT touch `/opt/data` (bind-mounted), so it does not remove
  the private MCP registration/install state for either profile; those are
  separately reversible per-profile via `hermes_cli.private_mcp disable` /
  `detach` / `unregister` (operator-only, `--confirm-profile` required).
- **Per-profile rollback:** `python -m hermes_cli.private_mcp disable mission-control
  --confirm-profile <profile_home>` (config only — does not kill an already-
  resumed consumer; follow with `reconcile` to actually tear it down), or
  `detach`/`unregister` for full removal. Companion Skill removal is separate
  (`skill-backup` moves it to `private_mcp/skill_backups/`, never deletes).

## Source state (feat/private-mcp-management)

Committed at `045d9b6e0c` (message `feat(mcp): add private operator-approved
MCP install_custom route`), on top of base `525dbcad8d744710a43d01eb7366500e46eacbf4`.
Clean of caches/credentials/transcripts; passed the repo's own pre-commit lint
hooks (ruff, Python lint, desktop ESLint).

Implemented and verified at the source level:
- `install_custom` connector action, strictly separate from catalogue `install`;
  model-facing schema accepts registered names only.
- Operator digest-approved manifest registry/validation, profile-scoped
  secrets, receipt-backed lifecycle, companion-Skill bundle management,
  desktop UI provenance — see git history for the full module list.

## Verification performed (source level, prior to deployment)

- Targeted pytest (7 files): 42 passed. Desktop Vitest (2 files): 5 passed.
  `ruff`, `ty check`, `tsc --noEmit`, `git diff --check`: all clean.
- Isolated real native-consumer probe (`scripts/probe_private_mcp_native.py`)
  against the actual canonical Mission Control FastMCP server, two fixture
  profiles: install_custom → 13 tools, native Skill discovery, `mc_health`,
  disable/reconcile/restore/detach all verified end to end. Superseded by the
  real Aeris deployment above, which is production evidence, not fixture
  evidence.
- Two independent read-only reviews (security, Skill/UI):
  `~/.hermes/profiles/jarvis/cache/scratch/private-mcp-final-security-review-v2.json`
  and `private-mcp-final-skill-ui-review-v2.json` — no source-level blocker.
  Remaining caveat (artifact/dependency closure immutability for a future
  sealed deployment) is a hardening item, not a defect, and does not block
  this deployment.

## Remaining open items

1. Rewrite and reapply the custom voice-worker header-override patch
   (`patches/api_server.py`) against the new codebase — currently absent on
   the live container. Needs Dwayne's review before reapplying (this is his
   voice pipeline, not part of this feature).
2. Memory Galaxy (`mem0-aio`, reachable via Docker DNS from `hermes-agent`,
   frontend is a Next.js app — no simple REST write endpoint found yet) was
   not updated with this final status; this file plus Jarvis's own assistant
   memory now carry the authoritative record instead.
3. The `docker-compose.override.yml` swap is a deliberate interim step, not a
   permanent fix: `local/hermes-agent-private-mcp:test-20261023` is a one-off
   local build tag. Longer-term, this should go through the project's normal
   release path (PR review → tagged release → pulled image) rather than
   staying on a hand-built local tag indefinitely.
4. `MC_ALLOW_WRITES` intentionally left unset; enabling mission create/update
   from either agent is a separate decision for Dwayne.
