# Frame detection

CadPlot MCP inspects closed, axis-aligned four-vertex model-space polylines and paper-size text
inside them. It also accepts an `AcDbBlockReference` as a frame when its own editable or constant
attributes contain exactly one physical paper size, its bounding box has that aspect ratio, it has
a stable handle, and its rotation is an exact multiple of 90 degrees. Detection is deterministic
and does not use an LLM.

The detector evaluates candidates per paper label:

1. collect rectangles containing the label insertion point;
2. retain rectangles whose aspect ratio matches the parsed paper size;
3. choose the smallest valid nested rectangle;
4. suppress duplicate labels that select the same frame;
5. skip equal-area competing rectangles as ambiguous.

A single candidate receives confidence `0.9`. A deterministic nested selection receives `0.8`
and a warning. The default `minimum_frame_confidence` is `0.85`, so nested results remain plan
blockers until an operator fixes the source ambiguity or deliberately changes office policy.

Attribute-backed block candidates fail closed when attributes contain conflicting paper sizes,
the bounds do not match, rotation is non-orthogonal, or equal-size blocks compete. Arbitrarily
rotated frames and blocks whose paper label exists only inside nested block geometry are not yet
automatic candidates. They must be handled by an approved office-specific detector or reviewed
manually rather than guessed.

If the office has a frame-layer standard, configure `frame_layers`. Matching is case-insensitive;
any detected candidate on another layer receives `disallowed_frame_layer` and blocks the plan.
An empty list keeps the general-purpose allow-all behavior.

Autodesk references:

- [BlockReference object and methods](https://help.autodesk.com/cloudhelp/2025/ENU/AutoCAD-LT-ActiveX-Reference/files/GUID-88EEBCA3-8AF5-4776-9D54-520B05AB9129.htm)
- [Editable block attributes](https://help.autodesk.com/cloudhelp/2022/ENU/AutoCAD-ActiveX-Reference/files/GUID-3E8E1756-F45D-4CCE-838B-00FBC0374278.htm)
- [Constant block attributes](https://help.autodesk.com/cloudhelp/2023/DEU/AutoCAD-ActiveX-Reference/files/GUID-2655DC82-1DD4-42EC-888C-5F6E32A7342B.htm)
- [Rotation is expressed in radians](https://help.autodesk.com/cloudhelp/2024/ENU/AutoCAD-ActiveX-Reference/files/GUID-ADD5CBF4-4C4A-4DE2-A686-4F4DD0CB8734.htm)
