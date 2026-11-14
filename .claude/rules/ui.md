---
paths:
  - "src/ofmhelpers/web/templates/**"
  - "src/ofmhelpers/web/static/**"
  - "scripts/ui_screens.py"
---

Moved word for word from `CLAUDE.md` (2026-11-11); loads when a template, CSS or JS file is touched.

## UI (templates + CSS)
- Every page extends `base.html`; navigation comes from `schemas/nav.py` `NAV` (new page = one `NavItem`).
- Buttons, icon buttons, page headers, cards, notices, badges, stats, empty states, form fields: ONLY via `templates/components/ui.html` macros. A page never writes their classes; `tests/unit/web/test_templates_use_components.py` fails if it does. No `style=""` (except CSS custom properties carrying data), no `<style>`.
- Need a new element or variant: add the macro + its CSS role, show it on `/styleguide`, then use it.
- CSS is `static/css/tokens.css -> base.css -> components.css -> pages.css` (loaded via `_styles.html`). Colours only in the palette in `tokens.css` (`test_palette.py`); spacing/heights/radii only via the role tokens there. Change a token, not a rule.
- No help paragraphs, page leads that explain, or explanatory tooltips; `title` only for the full text of something truncated.
- Every UI change: `scripts/ui_screens.py rNN` (screenshots of every page into `reports/ui/`, side by side in `reports/ui/index.html`); the user checks dev before tests run.
- One exception to "no help paragraphs" (owner 2026-11-14): a page's guided tour, `ui.tour` + `static/js/tour.js` ("How to"): its steps are (selector, text) pairs the page template sets as `tour` before the toolbar include.
