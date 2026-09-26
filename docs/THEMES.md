# Themes

Antiphon's look is set by a theme: a `.css` file of CSS custom properties.
The same variables drive the Qt style sheet (lists, menus, inputs) and the
custom-painted chrome (transport buttons, glowing sliders, EQ faders and
knobs, the display panel, the EQ curve). Change one file and the whole player
follows.

## Picking a theme

**View ▸ Theme** lists the built-in themes and any of your own. Your choice is
remembered.

| Theme | Look |
|---|---|
| RealPlayer Classic *(default)* | charcoal and blue chrome, after RealPlayer 10 |
| One Dark Pro | neutral grey, blue and purple |
| Dracula | purple-grey, pink and green |
| Monokai Pro | warm charcoal, yellow and pink |
| Night Owl | deep navy, teal |
| SynthWave '84 | neon pink and cyan on purple, heavy glow |
| Cobalt2 | saturated blue, yellow |
| Nord | arctic blue-grey, soft frost |
| Gruvbox Dark | earthy brown, orange |
| GitHub Light | white, blue and green |
| Catppuccin Latte | pastel light, mauve and pink |

The VS Code themes were chosen from the most-installed list on
[vscodethemes.com](https://vscodethemes.com/), picking ones that look clearly
different from each other. Their palettes are adapted, not copied wholesale.
Each one is Antiphon's own interpretation of that theme's colors.

## Making your own

1. Pick the theme closest to what you want.
2. **View ▸ Theme ▸ Customize Current Theme…** saves an editable copy to
   `~/.config/antiphon/themes/` and switches to it.
3. Edit it in any text editor. **Antiphon reloads it every time you save.**

Or drop any `.css` file into that folder yourself. A theme only needs the
variables it changes; everything else falls back to RealPlayer Classic:

```css
:root {
  --name: "Midnight Orange";
  --dark: true;
  --accent: #ff8800;
  --groove-fill: var(--accent);
  --glow: #ffaa33;
}
```

A file with the same name as a built-in theme (e.g. `nord.css`) replaces it.

### Syntax

- Variables go in one `:root { … }` block as `--name: value;`.
- `var(--other)` and `var(--other, fallback)` work, including nesting.
- Colors: `#rgb`, `#rrggbb`, `#rrggbbaa` (CSS order), `rgb()`, `rgba()` with a
  0–1 alpha, or a name like `white` / `transparent`.
- After the `:root` block you can add any Qt style sheet rules; they can use
  `var(--…)` too. Useful object names: `#Transport`, `#NavRail`,
  `#QueuePane`, `#BrowseTree`, `#PaneTitle`, `#EqPeak`, `#EqValue`,
  `#VolumeLabel`.

```css
#NavRail::item:selected { color: #36f9f6; border-left: 3px solid #ff7edb; }
```

### Variables

| Group | Variable | Used for |
|---|---|---|
| Meta | `--name`, `--dark` | menu name; whether it's a dark theme |
| General | `--window` | main background |
| | `--surface`, `--surface-alt` | lists and tables, alternating rows |
| | `--panel` | nav rail, queue, browse tree, headers |
| | `--border` | dividers and outlines |
| | `--text`, `--text-muted` | text, secondary text |
| | `--accent`, `--accent-text` | highlights, checked buttons |
| | `--selection`, `--selection-text` | selected rows |
| | `--hover` | row and menu hover |
| | `--input` | text fields and combo boxes |
| | `--button`, `--button-hover`, `--button-text` | push buttons |
| | `--scrollbar`, `--scrollbar-hover` | scrollbar handles |
| | `--tooltip`, `--tooltip-text` | tooltips |
| | `--warning` | EQ clipping hint |
| Transport | `--chrome-top`, `--chrome-bottom`, `--chrome-edge`, `--chrome-text` | the strip's gradient, bottom edge and labels |
| | `--btn-top`, `--btn-bottom`, `--btn-ring`, `--btn-icon` | small round buttons |
| | `--play-top`, `--play-bottom`, `--play-icon` | the big Play button |
| | `--gloss` | highlight across the top of buttons (use alpha) |
| Sliders | `--groove`, `--groove-fill` | track, and the played / lit part |
| | `--thumb`, `--glow` | slider thumb and its glow |
| Display | `--lcd`, `--lcd-text`, `--lcd-dim`, `--lcd-border` | the now-playing readout |
| Visualizer | `--vis-low`, `--vis-mid`, `--vis-high`, `--vis-peak` | spectrum analyzer gradient (bottom → top) and peak markers; default to the theme's `--groove-fill`, `--lcd-text` and `--warning` |
| Equalizer | `--eq-curve`, `--eq-fill`, `--eq-grid` | response curve, its shading, grid |
| | `--fader-cap`, `--knob-top`, `--knob-bottom` | fader caps and knob bodies |
| Type | `--font`, `--font-size`, `--lcd-font` | UI font list, base size (px), display font |
| Shape | `--radius` | corner radius for inputs, buttons, panels |

### Layout tokens

Spacing, sizes and the type scale are variables too, defined once in
RealPlayer Classic and inherited by every theme. Override them to change the
density of the whole app:

```css
:root {
  --row-height: 36px;        /* roomier library rows */
  --control-height: 36px;    /* taller buttons and fields */
  --space-4: 20px;           /* wider view padding */
}
```

| Variable | Default | Used for |
|---|---|---|
| `--space-1` … `--space-6` | 4, 8, 12, 16, 24, 32 px | the spacing scale: gaps, padding, margins |
| `--control-height` | 32px | buttons, text fields, dropdowns |
| `--row-height` | 30px | list and table rows |
| `--nav-item-height` | 38px | navigation rail items |
| `--text-xs` … `--text-2xl` | 11, 12, 13, 15, 18, 22 px | type scale: labels, secondary text, body, headings, view titles, Now Playing title |

Layout changes apply the next time Antiphon starts; colors apply immediately.

### Contrast

Built-in themes are tested to keep normal text at 4.5:1 contrast or better
(WCAG 2.2 AA) against their backgrounds. That's why some muted colors are a
little brighter than in the editor themes they're based on.
