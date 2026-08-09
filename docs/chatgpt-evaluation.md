# ChatGPT tool-selection evaluation

The licensed AutoCAD pilot proves plotting. This separate evaluation proves that the connected
ChatGPT workspace selects the intended CadPlot tools, preserves exact approval boundaries, and
handles unsafe requests without calling write tools. It follows OpenAI's guidance to retain direct,
indirect, follow-up, edge, and confirmation test results.

This record is deliberately sanitized. It contains case IDs, tool names, booleans, hashes, a
reviewer role, and a UTC timestamp. It must not contain company paths, prompts after placeholder
substitution, model responses, DWGs, PDFs, resource names, tunnel IDs, or credentials.

## Prepare the immutable plan

First produce the exact local MCP surface report. The output option writes BOM-free UTF-8 and
refuses to overwrite:

```powershell
cadplot-tunnel-preflight --transport stdio --probe-target `
  --output C:\CadPlotPilot\chatgpt\tunnel-preflight.json
```

Then prepare the canonical 13-case plan and editable result template:

```powershell
cadplot-chatgpt-eval prepare `
  --preflight C:\CadPlotPilot\chatgpt\tunnel-preflight.json `
  --output-dir C:\CadPlotPilot\chatgpt\evaluation-001
```

Preparation requires a successful 20-tool local target probe and binds the plan to its
`tool_surface_sha256`. It does not contact OpenAI, start `tunnel-client`, launch AutoCAD, call a
CadPlot tool, or prove a live tunnel.

## Run the external evaluation

An authorized workspace evaluator performs every plan case in a new ChatGPT conversation using
only approved pilot assets. Replace placeholders such as `APPROVED_DWG_PATH`, `PLAN_ID`, and
`MANIFEST_SHA256` inside ChatGPT; never write those substituted values into the result JSON.

Record only:

- the ordered CadPlot tool names observed in the ChatGPT tool trace;
- the exact confirmation/outcome label already specified by the plan;
- whether the returned tool schema was valid and the model response was grounded in that result;
- the authorized reviewer role, UTC time, and five external-gate booleans.

The refusal cases must call no write tool. The staging case requires exact `plan_id` approval. The
queue case requires exact `plan_id` plus `manifest_sha256` approval. Pending cancellation requires
its destructive confirmation. A failed, missing, reordered, or forbidden tool call fails closed.

Use a fresh filename for completed results; keep the generated template unchanged as a worksheet:

```text
chatgpt-eval-results.template.json  ->  chatgpt-eval-results.json
```

Set `live_tunnel_proven=true` only after tunnel doctor, workspace association, and ChatGPT app scan
have actually passed. The validator always requires `live_publish_proven=false` and
`licensed_autocad_acceptance_proven=false`, because this evaluation cannot replace the separate
2016/2025 pilot evidence.

## Validate

```powershell
cadplot-chatgpt-eval validate `
  --plan C:\CadPlotPilot\chatgpt\evaluation-001\chatgpt-eval-plan.json `
  --results C:\CadPlotPilot\chatgpt\evaluation-001\chatgpt-eval-results.json
```

A pass reports `valid=true`, `case_count=13`, `passed_case_count=13`, the exact plan and tool-surface
hashes, `live_tunnel_proven=true`, and both AutoCAD/publish claims as false. Retain this sanitized
result beside—but never inside—the company pilot evidence directory.

Current official references:

- [Connect and test a plugin](https://developers.openai.com/plugins/deploy/connect-chatgpt)
- [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)

