---
name: svg-graphics
description: "Create vector graphics — diagrams, charts, icons, UI mockups and animated SVG — when precise shapes, labels and layout matter more than a generated image."
allowed-tools: write_document
---

# SVG graphics

Use this skill when the result is a **drawing made of shapes and text**: an architecture
or flow diagram, a chart, an icon, a simple UI mockup, an annotated plan, an animated
explainer. Use image generation instead for photos, paintings, characters and anything
where texture matters more than exact labels. Use ordinary HTML when the result must be
interactive (clicks, hover, forms).

## How to deliver it

Save the drawing as a document:

```
write_document(name="Queue architecture", format="svg", content="<svg ...>...</svg>")
```

The call carries plain JSON values: `name` is a file name and `format` is the bare word `svg`.
Never put `<parameter>`, `<function=` or any other tag inside the call — a model that also prints
the call as text mixes the two shapes, and the server refuses the value.

The file appears in the user's document library and its card previews it as an image;
CSS animations play there. Do not paste a wall of markup into the chat — describe the
result and let the preview show it. SVG documents are not indexed: the markup is not
useful to document search.

A page is the exception: when the user asks for an HTML page, deliver it as a ```html
block in the answer. `write_document` has no html format, so a call for one is refused.

`content` is a complete, self-contained SVG document. Always include
`xmlns="http://www.w3.org/2000/svg"`, a `viewBox` and explicit `width`/`height`.

## Shape and layout

- Set a canvas: diagrams `800×600` or `1200×800`, icons `24×24`, animated clips `800×450`.
- Leave a 24–32 px margin. Align boxes and text to a grid; do not let labels overlap.
- Font: `font-family="system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"`.
  Diagrams: 14–18 px; headings 20–24 px; icons no text.
- Escape `&` and `<` inside text as `&amp;` and `&lt;`; raw markup makes the file invalid.

A minimal flow:

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 360 120" width="360" height="120">
  <defs>
    <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
      <path d="M0 0L10 5L0 10Z" fill="#475569"/>
    </marker>
  </defs>
  <g font-family="system-ui, sans-serif" font-size="14" text-anchor="middle">
    <rect x="10" y="40" width="90" height="40" rx="6" fill="#e0e7ff" stroke="#4f46e5"/>
    <text x="55" y="65">Router</text>
    <rect x="135" y="40" width="90" height="40" rx="6" fill="#dcfce7" stroke="#16a34a"/>
    <text x="180" y="65">Queue</text>
    <rect x="260" y="40" width="90" height="40" rx="6" fill="#fee2e2" stroke="#dc2626"/>
    <text x="305" y="65">Model</text>
    <path d="M100 60H130" stroke="#475569" stroke-width="2" marker-end="url(#arrow)"/>
    <path d="M225 60H255" stroke="#475569" stroke-width="2" marker-end="url(#arrow)"/>
  </g>
</svg>
```
- Use a small palette with strong contrast. Do not encode meaning by colour alone.
- Arrows: define one `<marker>` in `<defs>` and reuse it with `marker-end="url(#arrow)"`.
- Group related shapes in `<g>` and give groups an `id` when it helps readability.
- Give the drawing `<title>` and `<desc>` children for accessibility.

## Charts

- Draw axes, ticks, units and a zero baseline. Label both axes.
- Use only the numbers the user gave; never invent data points.
- Prefer bar, line, scatter or donut; no 3D, no gradients that hide the data.
- Put the legend where it does not cover marks; keep it in reading order.
- Write the value next to a bar when there are few bars; otherwise rely on the axis.

## Icons

- 24×24 grid, `fill="none"`, `stroke-width="2"`, `stroke-linecap="round"`,
  `stroke-linejoin="round"`.
- Use explicit colours (`#1f2937`, `#2563eb`, …), not `currentColor`: inside an `<img>`
  there is no inherited colour.
- Keep one visual metaphor per icon; no text inside an icon.

## Animation

Use CSS inside a `<style>` element. SMIL animation (`<animate>`, `<set>`,
`<animateTransform>`) is refused by the writer, so do not use it.

```svg
<style>
  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: .35; } }
  .pulse { animation: pulse 2s ease-in-out infinite; transform-box: fill-box; transform-origin: center; }
</style>
```

Animate `transform`, `opacity`, `stroke-dashoffset`, `fill` and `stroke`. Keep the loop
short and calm; use `transform-box: fill-box` and `transform-origin` for rotations and
scales. For a step-by-step explainer, stagger `animation-delay` rather than adding
scripts.

## What the writer refuses

SVG is executable content, so the document writer validates it:

- no `<script>`, no `on*` attributes, no `javascript:`;
- no external `href` or `xlink:href` — only `#local-id`; no external images, fonts or
  stylesheets;
- no `<foreignObject>`, `<iframe>`, `<object>`, `<embed>`, `<audio>`, `<video>`;
- no `<!DOCTYPE>` or `<!ENTITY>`, no `<?xml-stylesheet?>`;
- in CSS and presentation attributes, `url(...)` must point to `#local-id`; no `@import`,
  `@font-face`, `expression()`, `behavior:` or external URLs.

If a write is refused, remove the offending construct and try again. Do not try to
bypass the check.

## Size

- Keep one drawing under about 8 KB of markup: a few dozen elements, not a photograph made of
  circles. Measured, a 19 KB scene took minutes to generate and broke its own tool call; the
  same subject at 5–7 KB went through first time.
- Prefer fewer, larger shapes and short repeated groups over thousands of coordinates.

## Before you answer

- Does every label fit inside its shape at the chosen font size?
- Is the drawing readable without colour?
- Are the user's numbers reproduced exactly?
- Is the animation CSS-only and does it loop cleanly?
- Is the file self-contained — no CDN, no external font, no remote image?
