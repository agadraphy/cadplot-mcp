# Scale inference

CadPlot MCP derives plot geometry from a labelled rectangular frame. It does not ask a language
model to guess the scale.

For each frame it:

1. parses physical paper dimensions from labels such as `70x100`, `700x1000 mm`, or `A4`;
2. calculates the model-space rectangle width and height;
3. converts drawing units using `drawing_unit_mm`;
4. evaluates both 0-degree and 90-degree paper orientations;
5. requires the width-derived and height-derived scales to agree within
   `scale_tolerance_ratio`;
6. matches the result to the closest configured `scale_denominators` value using the same
   tolerance.

The publish plan records the exact plot window, rotation, selected denominator, derived raw
denominator, and drawing-unit conversion. A distorted frame or a nonstandard scale gets
`unsupported_scale` status and keeps the full plan `ready=false`.

Example: a `70x100` centimetre paper label in a 35,000 x 50,000 model-space rectangle with
`drawing_unit_mm: 1` resolves to a 700 x 1,000 millimetre sheet at 1:50.
