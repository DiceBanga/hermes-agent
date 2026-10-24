# Private, operator-approved MCP entries

The shipped MCP catalog is curated by Nous. These profile-local entries are a separate trust category: operator-approved custom configuration, **not Nous-reviewed, catalog entries, or public endorsements**. They live under the active profile's `private_mcp/` registry and never enter the catalog picker.

## Register

Use a JSON manifest with `schema_version: 1`, a unique local `name`, and `server` configuration:

```json
{
  "schema_version": 1,
  "name": "my-indexer",
  "server": {
    "command": "/opt/my-indexer/bin/mcp-server",
    "args": ["--stdio"],
    "env": {"API_TOKEN": "${MY_INDEXER_TOKEN}"}
  }
}
```

The stdio executable must be an existing absolute executable path. Arguments are argv values, never shell commands. Environment and header values may contain only `${ENV_VAR}` references; no literal credentials are accepted. Custom endpoint trust is operator responsibility. HTTP manifests require an `https://` URL; explicitly approved network endpoints can still reach sensitive/privileged services, so assess SSRF exposure before approval.

Registration hashes exact manifest bytes and requires a TTY confirmation of that digest, or an explicit matching digest for scripted approval:

```sh
sha256sum my-indexer.json
python -m hermes_cli.private_mcp register my-indexer.json \
  --sha256 <sha256> --approve-digest <same-sha256>
```

Registration writes only the profile-local private registry. It does not install packages, alter `config.yaml`, or run the server. During a model-requested install, only the registered name is supplied; the backend snapshots the registered manifest and profile and rejects drift, catalog/config name collisions, and missing referenced credentials. Missing secrets must be configured in that profile by the operator before installation; the model approval card must not collect or persist them.

The registry is profile-isolated: `-p <profile>` uses that profile's registry, with no cross-profile inheritance. Disable/detach and receipt-backed restore should be performed only through operator lifecycle commands that compare ownership and update the exact entry; never replace the whole `mcp_servers` map. A disabled/detached config does not necessarily terminate already-running or resumed server processes; confirm runtime refresh semantics before claiming it has stopped.

Private MCP executables and endpoints are powerful and not reviewed by Nous. Keep them clearly distinguished from catalog entries in UI and user-facing explanations.
