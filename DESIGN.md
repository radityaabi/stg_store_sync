---
version: alpha
name: STG Store Sync
description: PySide6 desktop design system — shared suite tokens, indigo accents, pastel group boxes, dark console surfaces, fool-proof operator UX.
colors:
  primary: "#1F2937"
  secondary: "#6B7280"
  tertiary: "#4F46E5"
  neutral: "#F8F9FA"
  surface: "#FFFFFF"
  border-light: "#E5E7EB"
  border-medium: "#D1D5DB"
  border-hover: "#9CA3AF"
  accent-deep: "#4338CA"
  accent-darkest: "#3730A3"
  accent-soft: "#E0E7FF"
  accent-soft-text: "#3730A3"
  gradient-start: "#667EEA"
  gradient-end: "#764BA2"
  on-primary: "#FFFFFF"
  on-tertiary: "#FFFFFF"
  success: "#10B981"
  success-deep: "#059669"
  danger: "#EF4444"
  danger-deep: "#DC2626"
  warning: "#F59E0B"
  warning-deep: "#D97706"
  info: "#6366F1"
  teal: "#0D9488"
  teal-deep: "#0F766E"
  blue: "#2563EB"
  blue-deep: "#1D4ED8"
  green: "#16A34A"
  green-deep: "#15803D"
  display-value: "#111827"
  console-bg: "#0F172A"
  console-text: "#E2E8F0"
typography:
  body:
    fontFamily: "\"Segoe UI\", \"Roboto\", sans-serif"
    fontSize: 13px
    fontWeight: 400
    lineHeight: 1.4
  display:
    fontFamily: "\"Segoe UI\", \"Roboto\", sans-serif"
    fontSize: 29px
    fontWeight: 700
    lineHeight: 1.2
  stat-value:
    fontFamily: "\"Segoe UI\", \"Roboto\", sans-serif"
    fontSize: 24px
    fontWeight: 700
    lineHeight: 1.2
  title:
    fontFamily: "\"Segoe UI\", \"Roboto\", sans-serif"
    fontSize: 18px
    fontWeight: 700
    lineHeight: 1.2
  heading:
    fontFamily: "\"Segoe UI\", \"Roboto\", sans-serif"
    fontSize: 15px
    fontWeight: 600
    lineHeight: 1.3
  label:
    fontFamily: "\"Segoe UI\", \"Roboto\", sans-serif"
    fontSize: 13px
    fontWeight: 600
  label-caps:
    fontFamily: "\"Segoe UI\", \"Roboto\", sans-serif"
    fontSize: 12px
    fontWeight: 600
    letterSpacing: "0.02em"
  caption:
    fontFamily: "\"Segoe UI\", \"Roboto\", sans-serif"
    fontSize: 10px
    fontWeight: 600
    letterSpacing: "0.02em"
  console:
    fontFamily: "\"Consolas\", \"Cascadia Code\", \"JetBrains Mono\", monospace"
    fontSize: 13px
    fontWeight: 400
    lineHeight: 1.45
rounded:
  sm: 4px
  md: 6px
  lg: 8px
  pill: 10px
  full: 9999px
spacing:
  xs: 4px
  sm: 8px
  md: 16px
  lg: 24px
  xl: 48px
components:
  header-gradient:
    backgroundColor: "linear-gradient(135deg, {colors.gradient-start} 0%, {colors.gradient-end} 100%)"
    textColor: "{colors.on-primary}"
    rounded: "{rounded.lg}"
    padding: 16px
  button-primary:
    backgroundColor: "{colors.tertiary}"
    textColor: "{colors.on-tertiary}"
    typography: "{typography.label}"
    rounded: "{rounded.md}"
    padding: "10px 20px"
  button-primary-hover:
    backgroundColor: "{colors.accent-deep}"
  button-primary-pressed:
    backgroundColor: "{colors.accent-darkest}"
  button-success:
    backgroundColor: "{colors.success}"
    textColor: "{colors.on-primary}"
    typography: "{typography.label}"
    rounded: "{rounded.md}"
    padding: "10px 20px"
  button-success-hover:
    backgroundColor: "{colors.success-deep}"
  button-danger:
    backgroundColor: "{colors.danger}"
    textColor: "{colors.on-primary}"
    typography: "{typography.label}"
    rounded: "{rounded.md}"
    padding: "10px 20px"
  button-danger-hover:
    backgroundColor: "{colors.danger-deep}"
  button-warning:
    backgroundColor: "{colors.warning}"
    textColor: "{colors.on-primary}"
    typography: "{typography.label}"
    rounded: "{rounded.md}"
    padding: "10px 20px"
  button-warning-hover:
    backgroundColor: "{colors.warning-deep}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.primary}"
    typography: "{typography.label}"
    rounded: "{rounded.md}"
    padding: "10px 20px"
  button-secondary-hover:
    backgroundColor: "#F3F4F6"
    textColor: "{colors.primary}"
  button-secondary-disabled:
    backgroundColor: "#F3F4F6"
    textColor: "{colors.border-hover}"
  text-input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.primary}"
    rounded: "{rounded.md}"
    padding: "8px"
  text-input-focus:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.primary}"
  group-box:
    backgroundColor: "{colors.surface}"
    textColor: "#374151"
    typography: "{typography.label}"
    rounded: "{rounded.lg}"
    padding: "24px 16px"
  tab-panel:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.primary}"
    rounded: "{rounded.md}"
  console-log:
    backgroundColor: "{colors.console-bg}"
    textColor: "{colors.console-text}"
    typography: "{typography.console}"
    rounded: "{rounded.md}"
    padding: "8px"
  summary-card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.primary}"
    rounded: "{rounded.pill}"
    padding: "12px 16px"
  summary-card-clickable:
    backgroundColor: "#FAFBFF"
    textColor: "{colors.primary}"
  summary-card-clickable-hover:
    backgroundColor: "#EEF2FF"
    textColor: "{colors.primary}"
  stat-card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.display-value}"
    borderColor: "{colors.border-light}"
    rounded: "{rounded.lg}"
    padding: "12px 16px"
  stat-card-value:
    typography: "{typography.stat-value}"
    textColor: "{colors.display-value}"
  stat-card-caption:
    typography: "{typography.caption}"
    textColor: "{colors.secondary}"
  queue-strip:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.secondary}"
    borderColor: "{colors.border-light}"
    typography: "{typography.label-caps}"
    rounded: "{rounded.md}"
    padding: "8px 12px"
  action-rail:
    backgroundColor: "{colors.neutral}"
    borderColor: "{colors.border-light}"
    padding: "12px 16px"
  run-summary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.display-value}"
    borderColor: "{colors.border-light}"
    rounded: "{rounded.lg}"
    padding: "12px 16px"
  severity-critical:
    backgroundColor: "#FEF2F2"
    textColor: "#B91C1C"
    typography: "{typography.caption}"
    rounded: "{rounded.pill}"
    padding: "2px 10px"
  severity-high:
    backgroundColor: "#FEF3C7"
    textColor: "#B45309"
    typography: "{typography.caption}"
    rounded: "{rounded.pill}"
    padding: "2px 10px"
  severity-medium:
    backgroundColor: "#FFF7ED"
    textColor: "#C2410C"
    typography: "{typography.caption}"
    rounded: "{rounded.pill}"
    padding: "2px 10px"
  severity-low:
    backgroundColor: "#ECFEFF"
    textColor: "#0E7490"
    typography: "{typography.caption}"
    rounded: "{rounded.pill}"
    padding: "2px 10px"
  severity-info:
    backgroundColor: "#F5F3FF"
    textColor: "#6D28D9"
    typography: "{typography.caption}"
    rounded: "{rounded.pill}"
    padding: "2px 10px"
  status-ok:
    backgroundColor: "#ECFDF5"
    textColor: "#047857"
    typography: "{typography.caption}"
    rounded: "{rounded.pill}"
    padding: "2px 10px"
  status-warn:
    backgroundColor: "#FFF7ED"
    textColor: "#C2410C"
    typography: "{typography.caption}"
    rounded: "{rounded.pill}"
    padding: "2px 10px"
  status-fail:
    backgroundColor: "#FEF2F2"
    textColor: "#B91C1C"
    typography: "{typography.caption}"
    rounded: "{rounded.pill}"
    padding: "2px 10px"
  status-cancelled:
    backgroundColor: "#F3F4F6"
    textColor: "#4B5563"
    typography: "{typography.caption}"
    rounded: "{rounded.pill}"
    padding: "2px 10px"
tool-accents:
  # Store Sync keeps the shared suite shell (gradient-header + MODERN_STYLESHEET
  # base) so it reads as one platform with the other tools; only its header
  # accent may differ. Set the gradient to the shared suite indigo until a
  # distinct identity is chosen.
  store-sync:
    kind: header-gradient
    start: "#667EEA"
    end: "#764BA2"
---

## Overview

PySide6 desktop design system for **STG Store Sync**, the standalone tool that
full-syncs selected columns from a SurveyToGo interview pull into an STG Subject
Store. It shares the SurveyToGo (STG/Dooblo) Automation Suite's visual language
so an operator moving between the suite and this tool sees one cohesive
platform, not a foreign script. The aesthetic is a light, professional panel app
with indigo interaction accents, pastel group boxes, and a dark CLI-style
console for the run log. The tone is fool-proof: smart defaults, locked invalid
inputs, no dead buttons, dry-run is the default.

The workspace is a stacked navigation (Dashboard → Store detail → Run detail)
under a shared gradient header, per the GUI-redesign spec
(`docs/superpowers/specs/2026-10-08-gui-registry-redesign-design.md` §6).

## Colors

- **Primary ({colors.primary}):** Core text, headlines, high-emphasis surfaces.
- **Secondary ({colors.secondary}):** Supporting text, stat captions, metadata,
  queue-strip text.
- **Tertiary ({colors.tertiary}):** Indigo — the sole interaction driver. Primary
  buttons, focus rings, selected tabs, checkboxes. Apply sparingly.
- **Neutral ({colors.neutral}):** Page background and the action-rail fill.
- **Surface ({colors.surface}):** Card, stat-card, queue-strip and input fills —
  pure white to separate content from the neutral page.
- **Gradient ({colors.gradient-start} → {colors.gradient-end}):** Header banner
  gradient. `linear-gradient(135deg, #667eea 0%, #764ba2 100%)` — applied to the
  top `QFrame` of the window. White text only.
- **Display value ({colors.display-value}):** `#111827` — the big numbers on
  stat cards, and page titles.
- **Console ({colors.console-bg} / {colors.console-text}):** Dark terminal
  surface for the run log. `#0F172A` background, `#E2E8F0` monospace text.
- **Semantic buttons:**
  - Success ({colors.success} / {colors.success-deep}): `#btn_success`
  - Danger ({colors.danger} / {colors.danger-deep}): `#btn_danger`
  - Warning ({colors.warning} / {colors.warning-deep}): `#btn_warning`
- **Status pills:** OK (green), Warn (orange), Fail (red), Cancelled (grey) —
  applied to the header status label and to the queue strip via `setObjectName`.

## Typography

`"Segoe UI", "Roboto", sans-serif` for everything. Hierarchy comes from size and
weight, not family changes. Desktop base is 13px. Stat-card values use the
`stat-value` token (24px / 700) to draw the eye to store/enabled/running/failed
counts; the suite's larger `display` (29px) is reserved for full-width headline
numbers. Console panels switch to monospace (`Consolas` / `Cascadia Code` /
`JetBrains Mono`) for log alignment. `label-caps` and `caption` carry
`letterSpacing: 0.02em` for small uppercase (stat captions, queue strip) and hint
text.

## Layout

4px baseline spacing scale. Use `sm` (8px) inside a component, `md` (16px)
between controls in a group, `lg` (24px) between groups, `xl` (48px) for major
section breaks. The action rail sits at the bottom of every page — it must never
scroll off-screen.

**Workspace layout (mandatory for all pages):**

1. Gradient header `QFrame` (top, fixed height) with title · breadcrumb · status
   dot — owned by `MainWindow`, shared by every page.
2. `QStackedWidget` (`Dashboard` / `JobDetail` / `RunDetail`) below the header.
3. Page body: stat cards + table (Dashboard) or a `QTabWidget` (Store detail).
4. Action rail `QFrame` (bottom) — color-coded buttons.

**Dashboard** lays out stat cards in a row, then the queue strip, then the store
`QTableView` (stretch), with the toolbar in the action rail.

**Store detail** is a `QTabWidget`: *Config · Columns · Schedule · History ·
Conflicts · Blacklist · Proposed updates*.

## Elevation & Depth

Flat by default. No drop shadows. Depth is communicated by border contrast
(`{colors.border-light}` vs `{colors.border-medium}`) and background fills
(white surfaces on a neutral page). The gradient header is the only dimensional
element. This keeps the app calm on small laptop screens.

## Shapes

Rounded corners are modest:
- `sm` (4px): checkbox indicators, menu items, scrollbar handles.
- `md` (6px): inputs, buttons, tabs, lists, tables, dropdowns, queue strip.
- `lg` (8px): group boxes, stat cards, run summary — container-sized.
- `pill` (10px): status pills, summary cards.
- `full` (9999px): radio button indicators only.

## Components

- **header-gradient**: `QFrame#GradientHeader` with
  `linear-gradient(135deg, #667eea, #764ba2)`. Top of the window. White title
  (`typography.title`, 18px / 700), `#e2e8f0` breadcrumb, status dot `#86efac` → recolored per state.
  Fixed height.
- **Status indicator**: header `QLabel` with a colored dot + label matching the
  current state (`status-ok`, `status-warn`, `status-fail`, `status-cancelled`).
  Set via `MainWindow.set_status(status, color)`.
- **button-primary**: `#btn_primary` — indigo, white text, bold. The single
  high-emphasis action per screen (Save, Add column). Hover → `accent-deep`;
  pressed → `accent-darkest`.
- **button-success**: `#btn_success` — green, white text. Run / apply / approve
  actions.
- **button-danger**: `#btn_danger` — red, white text. Delete / stop actions. Use
  a confirmation dialog.
- **button-warning**: `#btn_warning` — amber, white text. Caution / retry.
- **button-secondary**: default non-ID'd `QPushButton` — white surface, dark
  text, light border. Hover lifts surface to `#F3F4F6`. Disabled fades text to
  `{colors.border-hover}`.
- **button-icon**: every colored button (`#btn_primary` / `#btn_success` /
  `#btn_danger` / `#btn_warning`) gets a one-glyph Unicode icon prefixed to its
  label — `"▶ Run now"`, `"🗑 Delete"`. Plain `button-secondary` (Back/Cancel)
  stays text-only except universally unambiguous pairings (Refresh). Pick the
  icon by the verb, not the color class:

  | Verb (button text starts with…) | Icon | Example |
  |---|---|---|
  | New / Add / Create | ➕ | `"➕ New"` |
  | Edit | ✏️ | `"✏️ Edit"` |
  | Save | 💾 | `"💾 Save"` |
  | Run / Start | ▶ | `"▶ Run now"` |
  | Stop | ⏹ | `"⏹ Stop"` |
  | Delete / Remove | 🗑 | `"🗑 Delete"` |
  | Export | 📤 | `"📤 Export mapping"` |
  | Import | 📥 | `"📥 Import mapping"` |
  | Refresh | 🔄 | `"🔄 Refresh"` |
  | Schedule | ⏰ | `"⏰ Schedules"` |
  | Back | ← | `"← Back"` |
  Navigation keeps its existing arrow convention (`"← Back"`) — don't double up
  with a verb icon too.
- **text-input**: `QLineEdit` / `QTextEdit` — white surface, light border, 6px
  radius, 8px padding. Focus → 2px indigo border (no background shift).
- **spin-box / combo**: `QAbstractSpinBox` family and `QComboBox` — same
  treatment as text-input. Without an explicit rule these fall through to the OS
  palette and render black-on-dark under a dark GTK/GNOME theme.
- **group-box**: `QGroupBox` — white surface, light border, 8px radius, bold
  title in `#374151`. Title sits top-left in the margin gap.
- **tab-panel**: `QTabWidget` workspace (Store detail). Selected tab: white,
  indigo bold text. Unselected: `#F3F4F6`, grey text. Tab pane: white, 6px
  radius, thin border.
- **table**: `QTableView` over a QAbstractTableModel — white surface, thin
  border, `md` radius, alternating row tint from the neutral palette. Bind the
  view to the model; never edit a stale copy.
- **console-log**: `QTextEdit` read-only. Dark `#0F172A` background, `#E2E8F0`
  monospace text. Holds the captured run log on Run detail.
- **stat-card**: `QFrame#StatCard` — white, 8px radius, light border, 12px/16px
  padding. Big value (`stat-value`, `#111827`) over an uppercase caption
  (`caption`, `#6B7280`). Row on the Dashboard: total · enabled · running ·
  failed.
- **queue-strip**: `QFrame#QueueStrip` — white, 6px radius, light border. Shows
  `▶ running: <name>` and `… pending: a, b` (live), or `idle`. Text in
  `label-caps`.
- **action-rail**: `QFrame#ActionRail` — neutral fill, top border, 12px/16px
  padding. Bottom of every page; never scrolls off.
- **run-summary**: `QFrame#RunSummary` — white, 8px radius, light border. Page
  title + status for one run.
- **status-pill**: `QLabel#status_<state>` — pastel pill, saturated text.
  States: ok, warn, fail, cancelled.
- **severity-badge**: `QLabel#sev_<level>` — pastel pill, saturated text.
  Levels: critical, high, medium, low, info. Used for conflict rows.
- **menu / menubar / dropdown**: forced light palette — white background,
  `#111827` text, selected item blue. Prevents dark-mode bleed on Linux
  GNOME / KDE.

## Do's and Don'ts

### Token & style discipline

- **Do** use ID selectors (`#btn_primary`, `#GradientHeader`, `#StatCard`) to
  prevent dark-mode bleed. Object names set via `setObjectName()`.
- **Do** force transparent backgrounds on `QLabel` so underlying surfaces don't
  get opaque fills.
- **Do** use token references (`{colors.primary}`) instead of literal hex in
  component definitions.
- **Do** extend the palette/token scale first — never introduce a color or font
  size outside this file. If a component has no clear mapping, add a token here,
  then implement.
- **Don't** nest component variants. `button-primary-hover` is a sibling entry,
  not a nested child.
- **Don't** use drop shadows. Depth = border + fill only.
- **Don't** hardcode the Qt system palette. Linux dark themes render grey-on-grey
  menus. Always set explicit QSS for menus, menubars, and dropdowns.

### GUI architecture (redesign spec §6)

- **Do** keep pages pure views — `Dashboard`, `JobDetail`, `RunDetail` emit
  intent signals; the `Controller` owns `Registry` + `RunQueue` + threads.
- **Do** drive the Columns grid through its `JobTableModel` (edits go through
  the model; the Pull-term column is read-only and derived).
- **Do** run syncs on the single global `RunQueue` — one store at a time — and
  make Stop cancel the active run cooperatively (status `cancelled`).
- **Do** marshal queue/scheduler callbacks back onto the GUI thread before
  touching widgets (Qt objects are not thread-safe).
- **Do** stop owned threads + close `State`/`Registry` in `closeEvent`
  (AGENTS-style resource hygiene).
- **Don't** block the GUI thread on network or export work.
- **Don't** auto-map columns — binding is an operator-approved
  `source → store` alias pair, or an uploaded `.xlsx`. Never map silently.

### Safety & correctness

- **Do** default to **dry-run**; a live write is an explicit operator choice.
- **Do** treat an absent/empty source value as `""` and write it — never skip.
- **Do** send the store's **full column set** on every `AddRow`/`UpdateRow`
  (omitted columns are reset by STG).
- **Do** fail closed: a store-side duplicate match key halts the job; a
  source-side duplicate skips those rows and marks the run `partial`.
- **Don't** guess an id/tag column — it is declared per job.

### Resource hygiene

- **Do** `quit()` + `wait(timeout)` QThreads in the parent's `closeEvent`, with a
  bounded join.
- **Do** use `daemon=True` threads unless explicitly `join()`ed.
- **Do** use `with open(...)` for files; `os.unlink()` temp files after use.
- **Do** close DB connections (`State` / `Registry`) and HTTP sessions
  (`requests.Session` inside `STGClient`) at exit.
- **Don't** delete global paths. Delete only files created by this session.

### UX behavior

- **Do** lock invalid inputs dynamically. A control that can't be used yet
  should be disabled, not silently ignored.
- **Do** show a live queue strip and status dot so the operator always knows
  what is running, pending, or finished.
- **Do** log ALL outcomes to the run log — applied counts, errors, skips — not
  just failures.
- **Don't** run two syncs at once — one global queue only.
- **Don't** let the action rail scroll off-screen.

### Workflow

- **Do** run `pytest` before and after changes.
- **Do** update `README.md` / specs / `tasks/` docs when behavior changes.
- **Don't** mark a task complete without proving it works (tests pass, logs
  checked).
- **Don't** commit, push, or rewrite history unless explicitly asked.
- **Don't** read, print, or commit secrets (`.env`, credential files).
