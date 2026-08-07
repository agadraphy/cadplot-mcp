# Frame detection

CadPlot MCP inspects closed, axis-aligned four-vertex model-space polylines and paper-size text
inside them. Detection is deterministic and does not use an LLM.

The detector evaluates candidates per paper label:

1. collect rectangles containing the label insertion point;
2. retain rectangles whose aspect ratio matches the parsed paper size;
3. choose the smallest valid nested rectangle;
4. suppress duplicate labels that select the same frame;
5. skip equal-area competing rectangles as ambiguous.

A single candidate receives confidence `0.9`. A deterministic nested selection receives `0.8`
and a warning. The default `minimum_frame_confidence` is `0.85`, so nested results remain plan
blockers until an operator fixes the source ambiguity or deliberately changes office policy.

Current limitation: rotated/non-axis-aligned frames and complex block-based borders are not yet
automatic candidates. They must be handled by an approved office-specific detector or reviewed
manually rather than guessed.

If the office has a frame-layer standard, configure `frame_layers`. Matching is case-insensitive;
any detected candidate on another layer receives `disallowed_frame_layer` and blocks the plan.
An empty list keeps the general-purpose allow-all behavior.
