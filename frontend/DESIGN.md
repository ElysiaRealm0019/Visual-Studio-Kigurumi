---
version: "beta"
name: "Visual Studio Kigurumi IDE"
description: "An IDE-style workspace for designing kigurumi head shells, themed after the V.S.K logo."
colors:
  logo-blue: "#1890F8"
  logo-navy: "#003880"
  logo-deep-navy: "#071630"
  dark-accent: "#2E95F8"
  dark-background: "#0A1426"
  light-accent: "#116CED"
  light-background: "#F4F7FC"
typography:
  ui:
    fontFamily: Inter, PingFang SC, Microsoft YaHei, system-ui
    fontSize: 13px
---

## Overview

The app looks like a code editor: a title bar, an activity bar with tools, an explorer, a tabbed canvas, the assistant panel and a status bar. It should feel like a precise maker tool, not a dashboard. Images, masks and landmarks must stay visually exact; chrome stays quiet so the artwork stands out.

## Colors

The palette comes from the logo: bright blue on navy. Both themes are defined as CSS variables in `src/styles/index.css` (`--ide-*`) and mirrored in `src/app/theme.ts` for MUI. Keep the two in sync and use the variables instead of hard-coded colours.

| Token | Dark | Light | Use |
| --- | --- | --- | --- |
| `accent` | `#2E95F8` | `#116CED` | primary buttons, active tabs, focus, sliders |
| `accent-contrast` | `#04122A` | `#FFFFFF` | text on accent (navy on blue in dark mode for contrast) |
| `bg` | `#0A1426` | `#F4F7FC` | window background |
| `panel` / `panel-raised` | `#0E1A30` / `#14223B` | `#FFFFFF` / `#EDF2F9` | side panels, cards, menus |
| `border` / `border-strong` | `#1E2D48` / `#2B3D5E` | `#D9E2EF` / `#C1CEE1` | dividers and outlines |
| `text` / `text-muted` / `text-faint` | `#E3EAF5` / `#8796B0` / `#566684` | `#0F1F3D` / `#5A6A86` / `#93A1B8` | text levels |
| `danger` / `success` / `warning` | `#F26D6D` / `#43C38C` / `#E5B454` | `#D64545` / `#25966A` / `#B7791F` | status only |

Body text and accent text meet WCAG AA on their backgrounds; `text-faint` is for non-essential hints only.

## Typography

Use the system UI stack (`uiFontFamily` in `theme.ts`) at 13px. Keep letter spacing at `0`. In compact controls, reduce font size before letting text wrap.

## Layout

- Desktop: activity bar, explorer and tool panels on the left, tabbed canvas in the middle, assistant on the right, status bar at the bottom.
- Use CSS Grid and Flex with explicit min/max widths for panels; prevent horizontal overflow on narrow screens.
- Avoid nested cards. Use full panels, repeated item cards or tool surfaces.

## Components

- Buttons: `ide-button`, with `ide-button-primary` for the main action of a view. One primary action per area.
- Icon buttons: `ide-icon-button` with Tabler icons and a tooltip/`aria-label`. Do not use emoji as icons.
- Badges: `ide-badge` with `data-tone="accent" | "success"` for short states.
- Inputs: label above the field, `ide-input`, accent border on focus.

## Rules

- Do not introduce a second visual system for new pages or tools; reuse the `ide-*` classes and variables.
- Do not use pure black, neon colours, decorative blobs or heavy gradients.
- Keep image canvases, crop previews, masks and landmarks visually precise.
- Preserve existing workflow state, upload behaviour, editor tools, landmark detection and model assets.
