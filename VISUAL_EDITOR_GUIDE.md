# Visual Editor Guide

This document covers the current in-app visual editor used for template layout design.

Route:
- `/admin/template_editor/<template_id>?side=front`
- `/admin/template_editor/<template_id>?side=back`

The editor is side-aware for double-sided templates. Existing single-sided templates continue to use the front side only.

## What The Editor Controls

The visual editor can now control:
- field labels
- field values
- field colons
- photo box
- QR box
- custom text objects
- custom rectangle objects
- custom circle objects
- custom line objects
- custom image/logo objects

These settings are saved into the template settings and layout config, then used by:
- admin preview
- card generation
- edit/regenerate
- bulk generation
- PDF rendering

## Workspace Features

The editor workspace supports:
- zoom in / zoom out
- fit to canvas
- undo / redo
- snap movement
- optional grid overlay
- configurable grid size
- bleed guide
- safe area guide
- front / back side switching

## Field Editing

Each standard field is editable as separate parts:
- label
- colon
- value

Supported field behavior:
- drag label and value independently
- move colon independently
- hide label
- hide value
- compact layout so hidden full rows do not leave gaps
- set per-part color
- set per-part font size
- set per-part grow mode

Current standard fields:
- `NAME`
- `F_NAME`
- `CLASS`
- `DOB`
- `MOBILE`
- `ADDRESS`

## Colon Controls

The editor supports label-colon controls:
- show or hide label colon
- align colon near value
- change colon gap
- change colon color
- move colon directly in the canvas

Colon settings are saved in layout config and used in final rendering.

## Photo And QR Controls

The editor supports direct block control for:
- photo position
- photo size
- QR position
- QR size

These can be edited:
- by dragging on canvas
- by numeric inputs in the right panel

## Custom Objects

Custom objects can be added to the template:
- text
- rectangle
- circle
- line
- image/logo

Supported custom object properties:
- position
- width
- height
- opacity
- stroke width
- visibility
- lock state
- object name
- rotation angle for rotatable object types
- text content for text objects
- fill/stroke color where supported

Notes:
- image objects do not use fill/stroke color controls
- line objects currently do not use free rotation in the editor

## Selection And Layer Tools

The editor supports:
- select one object
- multi-select
- select all custom objects
- duplicate selected custom objects
- copy selected custom objects
- paste copied custom objects
- delete selected custom objects
- lock selected custom objects
- unlock selected custom objects
- bring to front
- send to back
- layer list view
- object rename shown in layers panel

## Alignment And Distribution

The following alignment actions are available:
- left
- horizontal center
- right
- top
- vertical center
- bottom

Distribution actions:
- distribute horizontally
- distribute vertically

## Rotation

Rotatable custom objects support:
- toolbar rotate `-15`
- toolbar rotate `+15`
- exact angle entry in inspector

Rotation is persisted and used in:
- normal card rendering
- PDF rendering

Currently intended for:
- text
- rectangles
- circles
- image/logo objects

## Inspector Panel

The selected object inspector now supports:
- item type
- custom object name
- text content
- color
- lock toggle
- X
- Y
- width
- height
- font size
- angle
- opacity
- stroke width

Inspector behavior:
- fields and custom objects show different controls as appropriate
- unsupported controls are disabled automatically
- multi-selection shows a summarized state

## Keyboard Shortcuts

Current shortcuts:
- `Delete` / `Backspace`
  - delete selected custom object(s)
  - hide selected field part(s)
- `Arrow Keys`
  - nudge selection by 1
- `Shift + Arrow Keys`
  - nudge selection by 10
- `Ctrl/Cmd + 0`
  - fit canvas
- `Ctrl/Cmd + D`
  - duplicate selected custom object(s)
- `Ctrl/Cmd + L`
  - lock/unlock selected custom object(s)
- `Ctrl/Cmd + A`
  - select all custom objects
- `Ctrl/Cmd + C`
  - copy selected custom object(s)
- `Ctrl/Cmd + V`
  - paste copied custom object(s)

## Double-Sided Template Support

The editor supports:
- front side editing
- back side editing
- side-specific settings
- side-specific layout config
- side-specific preview background

This means front and back can have different:
- field positions
- photo position
- QR position
- custom objects

## Rendering Support

Visual editor output is not just preview-only. Saved editor changes are applied in:
- normal card generation
- admin preview
- student edit/regenerate
- bulk generation
- compiled PDF rendering

Supported render behavior includes:
- custom text objects
- shapes
- logo/image objects
- opacity
- rotation for supported object types

## Data Storage

The editor primarily stores data in:
- template font settings
- template photo settings
- template QR settings
- `layout_config`
- `back_layout_config`

Custom objects are stored in:
- `layout_config.objects`
- `back_layout_config.objects`

Field-specific positions and visibility are stored in:
- `layout_config.fields`
- `back_layout_config.fields`

## Current Limitations

The visual editor is now significantly stronger, but it is still not a full desktop DTP editor.

Not fully supported yet:
- group / ungroup
- ruler guides
- bezier/path editing
- advanced node editing
- object flip tools
- rounded-rectangle shape editor
- full text-on-path features
- arbitrary vector boolean operations

## Recommended Usage

Best use of the visual editor:
- define front and back template layout
- place labels, values, photo, and QR
- add logos and simple design shapes
- align and distribute custom objects
- prepare final template structure before generation

For most ID-card layout work, the visual editor should now be the primary in-app design tool.
