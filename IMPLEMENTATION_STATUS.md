# Private Hermes MCP management — status

## Scope and production boundary

Dwayne authorized a reversible, operator-approved private MCP management extension,
separate from the Nous-reviewed catalogue (`install`), to install Mission Control for
Jarvis on Aeris first, then KYLY on Aeris, inside the shared `hermes-agent` container.
Sharing the container shares integration *code*, never profile approval, credentials,
or installed state — every profile is bound, approved and installed independently.

No production installation, native configuration/Skill change, deployment, or restart
has been performed. The live session's `manage_connections` remains catalogue-only
(`unknown MCP server(s) for install: mission-control. Install works for catalog entries
only`) and this branch does not bypass that — it adds a separate `install_custom` route
that does not exist in the running production install.

## Source state (feat/private-mcp-management, base 525dbcad8d744710a43d01eb7366500e46eacbf4)

Worktree: `/mnt/data/Dev/projects/hermes-private-mcp-management`. All changes remain
uncommitted and unpublished.

Implemented and verified at the source level:
- `install_custom` connector action, strictly separate from catalogue `install`; the
  model-facing schema accepts registered names only — no arbitrary command, URL,
  config, credential or profile-binding fields (`tools/connectors/tool.py`,
  `tools/connectors/targets.py`, `tools/connectors/mcp.py`,
  `tools/connectors/private_mcp.py`).
- Operator digest-approved manifest registry and validation
  (`hermes_cli/private_mcp.py`, `hermes_cli/private_mcp_validation.py`):
  `${ENV_VAR}`-only credential references, profile-bound registration, artifact
  fingerprinting, collision checks against the catalogue.
- Profile-scoped secret resolution for probing — values come only from the active
  profile's own secret scope, never another profile's (`hermes_cli/private_mcp_secrets.py`).
- Receipt-backed lifecycle: bounded thread/process locks, pending-before-commit
  journal, exact drift detection against foreign/native config, disable/detach/restore,
  and operator-only unregistration that never deletes resources silently
  (`hermes_cli/private_mcp_lifecycle.py`, `hermes_cli/private_mcp_operator.py`).
  Disabled integrations are never silently re-enabled by a model-facing install call.
- Companion-Skill bundle install/backup/restore with profile-home binding, canonical
  tree-digest verification, symlink rejection, and owned/pending/drifted/preexisting
  state tracking (`hermes_cli/private_mcp_skill.py`).
- Desktop UI shows private/operator-approved/NOT-Nous-reviewed provenance before
  approval (including the zero-credential path) and in the settled result
  (`apps/desktop/src/components/assistant-ui/mcp-setup-tool.tsx`,
  `apps/desktop/src/lib/connector-tools.ts`).

## Verification performed

1. Targeted pytest suite (7 files): **42 tests passed, 0 failed.**
   `HERMES_PYTHON=.../hermes-private-mcp-test-env/bin/python scripts/run_tests.sh
   tests/hermes_cli/test_private_mcp.py tests/hermes_cli/test_private_mcp_hardening.py
   tests/hermes_cli/test_private_mcp_lifecycle.py tests/hermes_cli/test_private_mcp_secrets.py
   tests/hermes_cli/test_private_mcp_skill.py tests/tools/test_connectors_private_mcp.py
   tests/tools/test_connectors_private_mcp_card.py -q`
2. Desktop Vitest (2 files): **5 tests passed, 0 failed**
   (`mcp-setup-tool.test.tsx`, `connector-tools.test.ts`); `tsc --noEmit` clean.
3. `ruff check` on every touched Python module: all checks passed.
4. `ty check` on the private-mcp source modules: all checks passed.
5. `git diff --check`: clean (no whitespace errors) across all 24 changed/added files.
6. **Isolated real native-consumer probe** (`scripts/probe_private_mcp_native.py`,
   evidence `~/.hermes/profiles/jarvis/cache/scratch/private-mcp-native-probe-PASS.json`
   equivalent log): spins up two fresh fixture Hermes profiles, each independently
   registers the real canonical Mission Control manifest (sha256-pinned,
   `rev 3ba012791dd7c5b45d46b1def8d6126302eb1764` of the postmerge-docs worktree),
   approves it per-profile through the real `install_custom` → operation →
   `apply_answer` path (asserting the private/NOT-Nous-reviewed notice is present
   BEFORE approval), then against the *actual* Mission Control FastMCP stdio server
   (backed by a synthetic read-only local HTTP fixture, zero application mutations):
   - real `install_custom` → `connected`, 13 MCP tools discovered per profile
   - real native Skill discovery (`/mission-control` slash command), prompt
     preloading, and `references/read-first.md` resource loading
   - a real safe tool call (`mc_health`) returns healthy
   - **disable** (config only) correctly leaves an already-running consumer live
     (config edits do not retroactively kill a resumed MCP connection — verified,
     not assumed)
   - **reconcile** (the supported route that actually tears down removed/disabled
     servers) then denies the tool and removes it from discovery
   - **restore** brings the server, its tools and its health call back
   - **detach** removes MCP ownership while preserving the installed Skill files
     on disk
   - Independently reproduced for `fixture-profile-a` and `fixture-profile-b`
     (profile isolation).

   Two real bugs were found and fixed to make this probe pass (not framework bugs,
   environment/compat bugs):
   - the private-mcp Python test venv's own interpreter symlink had been deleted by
     an earlier `rm -rf` cleanup pass; repointed to the shared Hermes-managed
     CPython 3.14 toolchain.
   - the test venv had `mcp==2.0.0` installed (incompatible with Mission Control's
     pinned `mcp>=1.30,<2`); synced exactly to Mission Control's own
     `requirements-linux-cp314.lock`.
   - launching the canonical server via `runpy.run_module(..., run_name='__main__')`
     breaks FastMCP's deferred pydantic forward-ref rebuild for a
     `from __future__ import annotations` module; switched the probe's launcher to
     `python -m mission_control.server` (what Mission Control's own
     `tests/test_stdio_fixture.py` already uses) — same canonical server, same
     mcp/pydantic versions pinned in Mission Control's own lock file, no probe-side
     stubbing.

## Independent reviews (read-only, both completed)

- `~/.hermes/profiles/jarvis/cache/scratch/private-mcp-final-security-review-v2.json`:
  verdict "substantially fail-closed for registered-name-only access, profile/digest
  binding, lifecycle ownership, and secret provenance. No direct source-level blocker.
  NOT APPROVED for production deployment: immutable executable/dependency provenance
  is unproven and runtime/deployment behavior was not verified [at review time —
  since closed by the native probe above, which remains a fixture/offline-signed
  artifact rather than a sealed production image]."
- `~/.hermes/profiles/jarvis/cache/scratch/private-mcp-final-skill-ui-review-v2.json`:
  no findings; flagged real native Skill context loading as previously unproven by
  fixtures — closed by the native probe above (same canonical Skill bundle, real
  `skill_view`/`scan_skill_commands`/`build_preloaded_skills_prompt`).

Remaining, still-open limitation from both reviews: the manifest digest binds
*declared config + on-disk artifact fingerprints*, not an immutable dependency
closure. A sealed/content-addressed runtime would be needed to claim full artifact
immutability; this is a deployment-hardening item, not a source defect.

## Blocked: production activation

Aeris is unreachable right now: `ssh aeris` times out and `tailscale status` reports
it **offline** (`100.98.241.98 aeris ... offline, last seen 2m ago`). This is an
infrastructure/network condition, not a code or review gate. It blocks:

- confirming whether the Aeris `hermes-agent` container's live Hermes build exposes
  `install_custom` at all (last known-good exec into the container showed profile
  directories `/opt/data/profiles/jarvis` and `/opt/data/profiles/kylie` exist, but
  `hermes --version` was not on PATH inside the container and KYLY identity was not
  yet confirmed beyond the directory name — do not treat "kylie" as proven-KYLY).
- any backup, install, or rollback exercise against the real Aeris profiles.

**Exact next operator/agent action once Aeris is reachable again:**
1. `ssh aeris 'docker exec hermes-agent sh -lc "<check>"'` to confirm the running
   Hermes build/version and whether `install_custom` is exposed there (it is not
   available in the current CLI session used throughout this work).
2. Confirm `/opt/data/profiles/kylie` is actually bound to the KYLY identity (grep its
   config/persona files, do not infer from the directory name alone).
3. Back up each profile's `mcp_servers` config and skills directory before any write.
4. If `install_custom` is available there: register the Mission Control manifest with
   its sha256, approve per-profile (Jarvis first, then kylie/KYLY), verify tool
   discovery + Skill loading + `mc_health`, exactly as the native probe above did
   against the fixture profiles.
5. If `install_custom` is NOT available there: this whole branch needs a reviewed
   merge/deploy to Aeris's Hermes build before any install can happen — flag that
   back to Dwayne; do not hand-edit `mcp_servers` or invoke dev code directly against
   the production container as a workaround.

## Not yet done

- Commit the reviewed source (strip caches/credentials/transcripts) — not yet
  committed; still sitting as uncommitted worktree changes.
- Any backup, install, restore/rollback exercise against the real Aeris Jarvis or
  kylie/KYLY profiles — blocked by Aeris being offline.
- Confirming kylie == KYLY.
- Updating Memory Galaxy with final per-profile installation status (pending actual
  installation).
