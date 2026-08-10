# Secure MCP Tunnel administrator handoff

CadPlot supports the local half of an OpenAI Secure MCP Tunnel developer pilot. The recommended
target is the installed `cadplot-mcp` command over STDIO. That keeps the MCP server on the licensed
Windows workstation and opens no inbound firewall port. Loopback HTTP remains available when an
operator specifically needs it.

## Secret-free local preflight

Set `CADPLOT_CONFIG` to the external company configuration, then run:

```powershell
$env:PATH = "$commandRoot;$env:PATH" # current operator process only
cadplot-tunnel-preflight --transport stdio --probe-target
```

Here `$commandRoot` is the verified `CommandRoot` returned by `install-python.ps1`. The installer
deliberately does not modify global PATH; this session-only prefix lets both preflight and
`tunnel-client` resolve the exact installed `cadplot-mcp` launcher.

The JSON report validates the CadPlot configuration and installed command. With `--probe-target`,
it starts a temporary local CadPlot STDIO process, performs only MCP `initialize` and `list_tools`,
then requires the exact 20-tool identity, instructions, annotations, and closed output schemas. It
does not call any CadPlot tool or AutoCAD API. It only reports whether `CADPLOT_TUNNEL_ID` and
`CONTROL_PLANE_API_KEY` are present; their values, the config path, and all machine paths are
omitted. The command does not contact OpenAI, start `tunnel-client`, launch AutoCAD, or prove a live
publish. `tool_surface_sha256` fingerprints the probed local contract for retained handoff evidence.

`local_handoff_ready=true` means the local CadPlot target can be handed to an administrator.
`operator_prerequisites_present=true` additionally means the current process can find
`tunnel-client` and the two required environment values. Neither flag proves the external tunnel,
workspace association, ChatGPT app scan, AutoCAD 2016/2025 execution, or PDF output.
`local_target_proven=true` means the local protocol probe passed in this invocation.

Use `--output <new-file.json>` when retaining this report. The command writes BOM-free UTF-8 and
refuses an existing target. After the administrator finishes the external tunnel/app setup, run the
separate [ChatGPT tool-selection evaluation](chatgpt-evaluation.md); its plan is bound to this
report's `tool_surface_sha256` and does not replace licensed AutoCAD acceptance.

## Administrator-owned steps

The workspace and Platform administrators must separately:

1. Create a tunnel and associate it with the intended Platform organization and ChatGPT workspace.
2. Grant Tunnels Read + Use and ChatGPT developer-mode access to the operator.
3. Supply the runtime key through the company's approved ephemeral secret mechanism.
4. Allow outbound HTTPS to the OpenAI tunnel control plane.
5. Install the current official `tunnel-client` release on the CadPlot workstation.

Never put the runtime key, tunnel profile, company config, internal hostname, DWG, plot resource, or
output PDF in this repository or the release kit.

## Official operator sequence

After setting `CADPLOT_TUNNEL_ID` and `CONTROL_PLANE_API_KEY` in the operator process, use the
current official client's help and initialize the STDIO target:

```powershell
tunnel-client help quickstart
tunnel-client init --sample sample_mcp_stdio_local --profile cadplot-local --tunnel-id $env:CADPLOT_TUNNEL_ID --mcp-command "cadplot-mcp"
tunnel-client doctor --profile cadplot-local --explain
tunnel-client run --profile cadplot-local
```

Keep `run` healthy while creating the ChatGPT developer-mode app and choose **Tunnel** as the
connection. The administrator must retain the successful doctor result, workspace association, app
scan, and a one-sheet approval-gated CadPlot pilot as separate evidence. Do not interpret tunnel
health as plot success; CadPlot completion still requires final
`source_unchanged=true` and `publish_verified=true` evidence.

Then prepare and complete the 13-case evaluation in
[ChatGPT tool-selection evaluation](chatgpt-evaluation.md). It exercises direct, indirect,
follow-up, approval, adversarial, edge, cancellation, and recovery behavior while retaining only a
sanitized tool-name/result record.

For the optional local HTTP target, start `cadplot-mcp-http --port 8765`, rerun preflight with
`--transport http --port 8765 --probe-target`, and initialize with
`--mcp-server-url http://127.0.0.1:8765/mcp` instead of `--mcp-command`. Never change the bind
address or place this endpoint behind a generic public tunnel or reverse proxy.

## Live gates that remain external

- Platform tunnel creation, role grants, and organization/workspace association;
- runtime-key delivery and outbound network policy;
- `tunnel-client doctor` and continuous connection health;
- ChatGPT developer-mode app creation and scan;
- licensed AutoCAD 2016 and 2025 pilot evidence;
- explicit company approval for production use or public release.

Reference: [OpenAI Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels).
