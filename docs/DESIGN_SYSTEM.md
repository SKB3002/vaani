# FinEye Design System

**Direction:** *Private banking meets sci-fi.* A high-end Bloomberg Terminal redesigned for 2030 — calm, confident, precise. Money feels serious and important, not playful. The product is a portfolio piece; this document is the source of truth for how every surface should look.

---

## Direction statement

1. **Editorial, not SaaS.** We take cues from the annual-report / private-wealth world rather than the fintech-startup world. That means warm paper, serif display type, and small, deliberate gold accents — never purple, never loud gradients on CTAs, never emoji.
2. **Precise, not playful.** Sharp-ish radii (cards 8 px, inputs/buttons 6 px, pills remain pill). Tabular monospaced numerals everywhere. Small-caps labels with tracked letterspacing on anything that isn't body copy.
3. **Dark mode is real elevation, not "light mode with the brightness down."** Three stacked surface layers, warm-toned onyx backgrounds, gold hairlines for borders, very subtle gold glow on modals + focused inputs.
4. **Motion has manners.** 240 ms state transitions on `cubic-bezier(0.22, 1, 0.36, 1)`. Hover lifts 1 px. Modals spring in at 360 ms. All motion gated on `prefers-reduced-motion: no-preference`.

---

## Palette — light ("daylight office")

| Token | Hex | Use |
|-------|-----|-----|
| `--surface-0` | `#FAF8F3` | Page background — warm paper |
| `--surface-1` | `#FFFFFF` | Cards, main surfaces |
| `--surface-2` | `#FDFCF8` | Elevated — modals, dropdowns |
| `--surface-3` | `#F3EFE6` | Hover fill / wells |
| `--text-1` | `#1A1613` | Primary (warm near-black) |
| `--text-2` | `#5C554E` | Secondary |
| `--text-3` | `#8F8780` | Muted / small-caps |
| `--border-1` | `rgba(30, 25, 20, 0.08)` | Hairline default |
| `--border-2` | `rgba(30, 25, 20, 0.14)` | Input borders |
| `--accent` | `#0F6B47` | Deep emerald — CTA, active nav |
| `--gold` | `#B8934A` | Subdued gold — focus, hero KPI rule, premium badges |
| `--success` | `#2C8A5E` |  |
| `--warn` | `#C4903A` |  |
| `--danger` | `#B54434` |  |
| `--info` | `#3E7B9E` |  |

**Brand chips (light):**
- Need — `#E3EAF2` on `#2D5F8A` (deep sapphire)
- Want — `#F3E1E5` on `#A03E4E` (mulled wine)
- Investment — `#E4EFE9` on `#0F6B47` (emerald)

**Category accents:** Food `#8A4F2D` (cinnamon) · Travel `#2D6B6B` (teal) · Enjoyment `#6B3E7A` (aubergine) · Misc `#5C5C4A` (olive-grey).

---

## Palette — dark ("Bloomberg Terminal 2030")

Three surface layers are visible. Cards sit above the page. Modals sit above cards. Elevation is communicated primarily by gold hairline borders and subtle inset-highlight top edges — not by shadow (shadows are mostly invisible on near-black).

| Token | Hex | Use |
|-------|-----|-----|
| `--surface-0` | `#0A0906` | Onyx page — warm near-black |
| `--surface-1` | `#14110D` | Cards — layer 1 |
| `--surface-2` | `#1C1814` | Elevated — modals, dropdowns, chip editors |
| `--surface-3` | `#24201A` | Hover fill on layer 2 |
| `--text-1` | `#F0EAE0` | Primary cream |
| `--text-2` | `#B8B0A4` | Secondary |
| `--text-3` | `#7A7166` | Muted |
| `--border-1` | `rgba(184, 147, 74, 0.12)` | Gold hairline, subtle |
| `--border-2` | `rgba(184, 147, 74, 0.18)` | Input borders |
| `--accent` | `#3FB58A` | Brighter emerald (readable on onyx) |
| `--gold` | `#D4AB67` | Brighter gold, never saturated |

**Brand chips (dark):** Need `#1E3A5C` / `#B8D4F0` · Want `#4A1E28` / `#F0C4CC` · Investment `#1E4A38` / `#B8E0CC`.

### Elevation diagram (dark)

```
Z = 0  ┌─────────────────────────────── #0A0906 page onyx ────────
       │
Z = 1  │    ┌──────── #14110D card ────────┐   ← 1 px gold hairline
       │    │  inset-highlight on top edge │       + shadow-2
       │    └──────────────────────────────┘
       │
Z = 2  │         ┌──── #1C1814 modal ────┐   ← 1 px gold hairline
       │         │  + very soft gold glow│       + shadow-4 + glow-gold
       │         └───────────────────────┘
       │
Z = 3  │    #24201A = hover on modal / listbox row
```

### Contrast (dark) — passes WCAG AA

| Pair | Ratio | Verdict |
|------|------:|---------|
| `text-1` `#F0EAE0` on `surface-0` `#0A0906` | **15.9 : 1** | AAA |
| `text-1` on `surface-1` `#14110D` | **14.4 : 1** | AAA |
| `text-1` on `surface-2` `#1C1814` | **12.5 : 1** | AAA |
| `text-2` `#B8B0A4` on `surface-0` | **9.0 : 1** | AAA |
| `text-2` on `surface-1` | **8.2 : 1** | AAA |
| `text-2` on `surface-2` | **7.1 : 1** | AAA |
| `text-3` `#7A7166` on `surface-0` | **4.7 : 1** | AA (body) · AAA (large) |
| `text-3` on `surface-1` | **4.3 : 1** | AA (large only) — reserved for small-caps labels, not body |
| `accent` `#3FB58A` on `surface-1` | **8.1 : 1** | AAA (large text, icons) |
| `gold` `#D4AB67` on `surface-1` | **8.4 : 1** | AAA |

For the muted small-caps labels (10–11 px) we keep contrast ≥ 4.5:1 by pairing `text-3` with `surface-0` or `surface-1`, never with `surface-2`/`surface-3`. All large-text and interactive states use `text-1`, `accent`, or `gold`, each comfortably above 7:1.

---

## Typography

Three families do everything:

1. **Fraunces** (variable serif, weight 300–600, optical size 9–144) — display, headings, KPI numbers, page hero, modal titles. Italic used sparingly inside hero titles for a single emphasised word.
2. **Inter Tight** (weight 400–700) — body, navigation, forms, table cells, chips.
3. **JetBrains Mono** (weight 400–600) — numbers in tables and inline mono spans. Always `font-variant-numeric: tabular-nums lining-nums`.

### Samples (serif display)

| Size | Sample | Use |
|------|--------|-----|
| `--fs-hero` clamp 2.5 → 4.5 rem | *The ledger, refined.* | Marketing hero |
| `--fs-2xl` clamp 2.25 → 3.25 rem | **₹12,46,500** | KPI value |
| `--fs-xl` clamp 1.7 → 2.25 rem | **Good evening.** | Page H1 |
| `--fs-lg` clamp 1.3 → 1.55 rem | **Last 5 expenses** | Section heading / modal title |

### Samples (sans body)

| Size | Use |
|------|-----|
| `--fs-md` clamp 1.05 → 1.18 rem | Card titles, lead paragraphs |
| `--fs-base` clamp 0.95 → 1.02 rem | Body, buttons |
| `--fs-sm` clamp 0.82 → 0.88 rem | Table cells, forms |
| `--fs-xs` clamp 0.70 → 0.76 rem | Hints, empty-state messages |
| 0.66 rem (small-caps + 0.12em tracking) | Table headers, KPI labels, eyebrow overlines |

Letter-spacing: **−0.02 em** on display headings (tighter, editorial); **+0.12 em** on small-caps labels (overline / eyebrow).

---

## Component gallery (inline HTML)

### KPI — the hero shot

```html
<div class="kpi kpi--hero">
  <div class="kpi__label">This month</div>
  <div class="kpi__value">
    <span class="kpi__value-currency">₹</span>12,46,500
  </div>
  <div class="kpi__delta kpi__delta--up">
    <span class="kpi__delta__triangle"></span> +4.8% vs last month
  </div>
</div>
```

The gold vertical rule on the left, the Fraunces numeral, the tabular alignment, and the small-caps label together carry the "private banking" tone.

### Buttons

```html
<button class="btn btn--primary">Quick add</button>
<button class="btn btn--secondary">Import CSV</button>
<button class="btn btn--ghost">Cancel</button>
```

- **Primary** — emerald fill, `on-accent` text, soft halo on hover.
- **Secondary** — transparent with a **gold hairline** border. The "refined" variant for the dark theme especially.
- **Ghost** — transparent.

### Chip row

```html
<span class="chip chip--need">Need</span>
<span class="chip chip--want">Want</span>
<span class="chip chip--investment">Investment</span>
<span class="chip chip--premium">Premium</span>
```

### Card variants

- `card` — default, 8 px radius, `shadow-1` + 1 px border. On dark, border becomes gold-hairline, shadow becomes inset-highlight + soft drop.
- `card--interactive` — adds hover lift, deepens shadow, brightens border slightly.
- `card--glass` — `backdrop-filter: blur(14px) saturate(1.4)` for topbar + hero panels. Gated behind `@supports (backdrop-filter)`.

---

## Motion principles

- **Default duration** 240 ms (`--dur-3`) for state transitions.
- **Easing** `cubic-bezier(0.22, 1, 0.36, 1)` — steep start, long settle. Feels *expensive*.
- **Spring** (`cubic-bezier(0.34, 1.56, 0.64, 1)`) reserved for modal + toast entrances.
- **Hover lift** — cards and buttons translateY(−1 to −2 px), shadow deepens. Never a colour flip, never a saturation spike.
- **Focus ring** — 2 px gold, 4 px offset, fades in over 160 ms. Never snaps.
- **KPI entrance** — staggered 60–80 ms delays between siblings in `.grid-4`/`.grid-2`, 560 ms fade + 12 px rise.
- **Background flourish** — three 520 px blurred blobs (gold / emerald / sapphire) at 3-5 % opacity, drifting over 62–90 s. Pauses entirely under reduced-motion.
- **Everything wrapped in `@media (prefers-reduced-motion: no-preference)` or neutralised via the token override in `tokens.css`.**

---

## Radii, shadows, glow

- **Radii:** cards 8 px, inputs/buttons 6 px, modals 12 px, pills 999 px. No 16–24 px playful radii anywhere.
- **Shadows (light):** two faint layers, never darker than 10 % alpha. Cards look printed on paper.
- **Shadows (dark):** `inset 0 1px 0 rgba(240, 234, 224, 0.04)` on top edge for the highlight + `0 4–24px rgba(0,0,0, 0.6)` below. Modals add `0 0 0 1px rgba(212, 171, 103, 0.22), 0 0 28px rgba(212, 171, 103, 0.10)` — the gold glow.
- **Glassmorphism** is reserved for topbar + modal backdrop + dropdown editor. `backdrop-filter: blur(12-14 px) saturate(1.3-1.4)`, always gated on `@supports`.

---

## Data density rules

- Grid rows: 40 px height, 10–12 px cell padding.
- Amount cells: right-aligned, JetBrains Mono, tabular-nums.
- Table headers: `font-size: 0.66rem`, uppercase, `letter-spacing: 0.12em`, `text-3` colour.
- Chips in grid cells: 22 px tall, 10 px horizontal padding, 0.68 rem type, medium weight.
- Sidebar: 260 px wide, `heading` labels in small-caps at 0.65 rem with +0.12 em tracking.

---

## Iconography

No emoji, ever. Icons inline SVG (stroke 1.5, `currentColor`). If adding more icons, use Lucide via CDN as a module script and keep stroke weight consistent.

---

## What this system replaces

The earlier direction (trust-green-only, sharp radii everywhere, slate neutrals, Inter body) is superseded. See `docs/DECISIONS.md` for why each change was made.
