# Design System Strategy: The Intelligent Cockpit

## 1. Overview & Creative North Star
**The Creative North Star: "The Decisive Navigator"**

This design system moves away from the "empty white space" of generic SaaS and embraces the density of a high-end aviation cockpit or a financial terminal. Recruitment is an act of high-stakes navigation; the interface must feel like an instrument of precision, not a marketing landing page.

We achieve a "High-End Editorial" feel through **Commanding Density**. Instead of hiding data behind clicks, we surface it using a sophisticated hierarchy of "Tonal Islands." We break the "Bootstrap template" look by using intentional asymmetry—pairing heavy, authoritative headlines (Manrope) with tight, data-rich functional zones (Inter). The result is a UI that feels "heavy" in its authority but "light" in its execution.

---

## 2. Colors: Tonal Architecture
The palette is rooted in `primary` (#002046), but its power comes from the interplay of neutral surfaces.

### The "No-Line" Rule
Traditional 1px borders create visual noise that exhausts the recruiter's eye. **Explicitly prohibit 1px solid borders for sectioning.** Boundaries are defined by background shifts:
*   **The Canvas:** Uses `surface` (#f6fafe).
*   **The Workspace:** Use `surface_container_low` (#f0f4f8) to carve out the main dashboard area.
*   **The Active Unit:** Place `surface_container_lowest` (#ffffff) cards inside the workspace. This "Natural Lift" provides clarity without a single line of "ink."

### Surface Hierarchy & Nesting
Treat the UI as a series of stacked, physical layers.
*   **Level 0 (Foundation):** `surface`
*   **Level 1 (Sub-navigation/Sidebars):** `surface_container`
*   **Level 2 (Main Content Cards):** `surface_container_low`
*   **Level 3 (Floating Modals/Popovers):** `surface_container_highest` with a Glassmorphism effect.

### Glass & Signature Textures
To avoid a flat, "cheap" feel, main CTAs and the "AI Suggestion" zones should utilize a subtle gradient transition from `primary` (#002046) to `primary_container` (#1b365d). For AI-driven insights, use `tertiary_container` (#003a55) with a 12px `backdrop-blur` to create a "glass" lens over the candidate data.

---

## 3. Typography: Editorial Authority
We utilize a dual-typeface system to separate **Narrative** (The Brand) from **Data** (The Work).

*   **Display & Headlines (Manrope):** Use `display-md` and `headline-lg` for candidate names and high-level stats. This typeface carries a geometric, modern weight that signals "High-End."
*   **Functional Interface (Inter):** All labels, inputs, and data points use Inter.
    *   **High-Density Body:** Use `body-sm` (0.75rem) for secondary candidate metadata to maximize information density without sacrificing legibility.
    *   **The Label Scale:** Use `label-md` in All Caps with +0.05em tracking for table headers to create a "terminal" aesthetic.

---

## 4. Elevation & Depth: Tonal Layering
We reject the 2010s "Drop Shadow." Depth is communicated through light and transparency.

*   **The Layering Principle:** Place a `surface_container_lowest` card on top of a `surface_container_high` background. The contrast in "brightness" serves as the border.
*   **Ambient Shadows:** For floating elements (like a candidate "Quick View"), use a shadow with a 40px blur, 0% spread, and an opacity of 6% using the `on_surface` (#171c1f) color. This mimics natural light rather than digital "glow."
*   **The "Ghost Border" Fallback:** If a divider is mandatory for accessibility, use the `outline_variant` (#c4c6cf) at **15% opacity**. It should be felt, not seen.
*   **Glassmorphism:** For the "Recruitment Cockpit" feel, overlay AI Suggestions using a semi-transparent `tertiary_fixed_dim` with a `backdrop-blur-md` (16px). This integrates the AI "layer" into the data beneath it.

---

## 5. Components: Precision Instruments

### Buttons: The Decision Drivers
*   **Primary (The Hire):** Solid `primary` (#002046). Roundedness: `md` (0.375rem). Use a subtle inner-glow (1px top border at 10% white) to give it a "physical button" feel.
*   **Tertiary (The Ghost):** No background. Use `on_secondary_container` text. These are for non-destructive, secondary navigation.

### Cards & Lists: The No-Line Standard
*   **Candidate Cards:** Forbid divider lines. Use `8px` of vertical white space and a shift from `surface_container_low` to `surface_container_lowest` on hover to indicate interactivity.
*   **Split-View Layouts:** The left pane (list) should be `surface_container` while the right pane (detail) is `surface`. The "seam" is the color shift, not a line.

### Status Badges: The "Signal" Scale
*   **Pass:** `on_tertiary_container` text on a background of Emerald Green (at 15% opacity).
*   **Risk:** `on_error_container` text on a background of `error_container`.
*   **AI Suggestion:** Use `tertiary_fixed` (#c9e6ff) with a small "sparkle" icon.

### Input Fields: The Terminal Style
*   Use `surface_container_high` as the input background. Remove the bottom border. On focus, transition the background to `surface_container_lowest` and add a 2px "Ghost Border" of `primary`.

---

## 6. Do's and Don'ts

### Do:
*   **Do** embrace density. Recruiter's need to see 20+ data points at once. Use `body-sm` and `label-sm` aggressively.
*   **Do** use asymmetrical layouts. A narrow left column for status and a wide right column for the "Resume Deep-Dive" creates an editorial feel.
*   **Do** use `rounded-xl` (0.75rem) for main dashboard containers but `rounded-sm` (0.125rem) for data tags to maintain a "technical" edge.

### Don't:
*   **Don't** use 100% black text. Always use `on_surface` (#171c1f) for better eye-strain management during 8-hour screening sessions.
*   **Don't** use "Marketing Blue." Avoid bright, saturated blues. Stick to the `primary` Deep Navy to maintain the "Professional Trust" mandate.
*   **Don't** use drop shadows on cards that are sitting on the base canvas. Reserve shadows only for elements that truly "float" (modals, tooltips).