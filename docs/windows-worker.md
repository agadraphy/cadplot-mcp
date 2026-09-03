# Windows workstation worker

`cadplot-worker` is the phase-1, read-only bridge between the public CadPlot gateway and one
interactive Windows user session running a separately installed and licensed AutoCAD product. It
does not install, sublicense, or attest an Autodesk license.

The worker opens outbound HTTPS connections only. It exposes no listener, accepts no paths or
runtime settings from the gateway, and handles only the closed environment, project-list, drawing
scan, drawing-inspection, and dry-run planning commands. Plot staging, queuing, and cancellation
remain unavailable.

## Local configuration

Pass one local YAML file with `--config`. CLI flags cannot override its gateway identity, key
material, CadPlot configuration, state database, TLS policy, or project roots.

```yaml
version: 1
gateway_origin: https://mcp.example.com
tenant_id: tnt_00000000-0000-4000-8000-000000000001
user_id: usr_00000000-0000-4000-8000-000000000002
device_id: ws_00000000-0000-4000-8000-000000000003
key_id: wkey_00000000-0000-4000-8000-000000000004

cadplot_config_path: 'C:\CadPlot\config.yaml'
state_database_path: 'C:\CadPlot\state\worker.sqlite3'
request_private_key_pem_path: 'C:\CadPlot\keys\worker-ed25519.pem'
gateway_dispatch_public_key_pem_path: 'C:\CadPlot\keys\gateway-ed25519.pub.pem'

policy_version: 1
poll_interval_seconds: 5
request_timeout_seconds: 30

tls:
  # Omit to use the Windows/system trust roots.
  ca_bundle_path: 'C:\CadPlot\keys\private-ca.pem'
  # These two fields are optional but must be configured together.
  client_certificate_path: 'C:\CadPlot\keys\worker-client.pem'
  client_private_key_path: 'C:\CadPlot\keys\worker-client-key.pem'

projects:
  - project_id: prj_00000000-0000-4000-8000-000000000005
    alias: headquarters
    root: 'D:\ApprovedCAD\Headquarters'
```

Unknown fields, duplicate YAML keys, non-canonical HTTP origins, malformed opaque IDs, coerced
numeric values, incomplete client-certificate pairs, filesystem redirects, and project roots
outside the referenced CadPlot `allowed_roots` fail startup. At most 100 projects may be
configured. Polling and request timeouts are each restricted to 1–60 seconds.

Relative paths resolve from the worker YAML directory. Their parent directories and every
referenced input file must already exist; the SQLite state file itself may be created on first
start. Project registration is idempotent only for an exact tenant/device/project/alias/root
mapping. A changed or colliding mapping fails closed.

Protect the worker YAML, private keys, gateway public key, and SQLite state with an NTFS ACL scoped
to the interactive Windows account and administrators. The current noninteractive loader accepts
an unencrypted PKCS#8 Ed25519 request key and, when mTLS is configured, an unencrypted client key;
do not place either file in the repository or a shared directory. The SQLite database contains
local path mappings and must remain workstation-local.

## Run

Install the project with the Windows AutoCAD extra, then validate one outbound iteration before
configuring a user-session service or scheduled task:

```powershell
cadplot-worker --config 'C:\CadPlot\worker.yaml' --once
```

Run continuously with the configured bounded polling interval:

```powershell
cadplot-worker --config 'C:\CadPlot\worker.yaml'
```

Output is limited to fixed status/error codes and never includes configuration values, local
paths, PEM material, gateway response bodies, or raw exceptions. `Ctrl+C` exits cleanly. A live run
on a non-Windows platform fails before creating local state or making a network request.
