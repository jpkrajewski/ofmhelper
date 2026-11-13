/**
 * A form marked `data-submit-on-change` (ui.date_range) is sent as soon as
 * one of its fields changes: a filter with no Apply button. A field left
 * empty is not sent, so the address stays `?date_from=...`, with no
 * `&date_to=`. Its calendar icons (`.date-pick-btn`) open the browser's
 * calendar of the date box next to them: nobody types a date (owner
 * 2026-11-12).
 */
(() => {
    const FORM = "form[data-submit-on-change]";

    document.addEventListener("change", (e) => {
        const form = e.target.closest?.(FORM);
        if (form) form.requestSubmit();
    });

    document.addEventListener("submit", (e) => {
        const form = e.target;
        if (!form.matches?.(FORM)) return;
        for (const field of form.elements) {
            if (field.name && field.value === "") field.disabled = true;
        }
    });

    document.addEventListener("click", (e) => {
        const button = e.target.closest?.(`${FORM} .date-pick-btn`);
        const box = button?.closest(".date-pick")?.querySelector('input[type="date"]');
        if (!box) return;
        if (box.showPicker) box.showPicker();
        else box.focus();
    });
})();
