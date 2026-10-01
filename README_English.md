# Vertex Symmetry Picker 1.0 Beta

Independent plugin: `vertex_symmetry_picker.py`. Keep the previous Symmetry Center Picker installed and enabled; no replacement is required.

## Install
1. Open Plugins Folder from Painter's Python menu.
2. Copy `vertex_symmetry_picker.py` into it and restart Painter.
3. Enable `vertex_symmetry_picker` in the Python menu. Choose English at the top.

## Use
1. Click Read current project objects and check only the body or head you want to pick from.
2. Zoom in and click a centerline vertex in the tool's own preview. The yellow marker shows the selected real vertex; its model coordinates appear below.
3. Copy X. In Painter choose Mirror symmetry → Axis X. Double-click the Axis position X number, then Ctrl+A → Ctrl+V → Enter. Match the axis when using Y/Z.

## Preview controls
1. Mouse wheel: zoom. Right-drag: pan.
2. View dropdown: front, back, two sides, top and bottom. Six fixed orthographic views; no free orbit.
3. Fit view: center and fit the mesh. Float or enlarge the panel for easier picking.

## Limitations and data handling
- Pick in this plugin's preview, not Painter's main viewport. Materials and textures are not displayed.
- Clicks snap to a real vertex within 10 interface pixels. They do not create arbitrary surface points. Zoom in and inspect the yellow marker to avoid neighboring vertices. Clicking empty space clears the selection.
- Screen coordinates locate the vertex; the resulting position comes from the exported mesh, not screenshot measurement.
- Occlusion considers checked geometry only. Preview face drawing is simplified; intersecting, overlapping or nonplanar geometry can produce ambiguous display/picking. Isolate objects and check another view when needed.
- Vertex IDs refer to this OBJ export, not necessarily the original Maya/Blender IDs.
- A single point positions an axis-aligned plane; it does not define an inclined plane. You must choose the correct centerline point.
- Output uses six decimals and the existing scene-center/radius conversion. Z remains unverified in live Painter.
- Local real-Qt tests cover picking, clipboard, languages and simulated project changes. One user has tried this tool in their Painter environment and reported successful operation. Cross-version compatibility and every axis have not been individually confirmed. Requires Painter with PySide6; the previous tool's test environment was Windows / Painter 12.0.3.
- Preview limit: 180,000 face corners (approximately 60,000 triangles). Reduce selected objects if prompted. Geometry is not silently simplified. Loading and previewing large models may be slow.
- Read objects again after reimport. Does not save/replace the project, edit geometry or set native symmetry. Exports a temporary mesh and cleans up normally; abnormal termination may leave temporary files.
- No network requests, account access or license checks. Stores language preference locally; Copy replaces clipboard text. Independent third-party tool, not an official Adobe product.

For problems, provide a screenshot showing the preview, selected marker and coordinates, Painter symmetry settings, and your Painter version.
