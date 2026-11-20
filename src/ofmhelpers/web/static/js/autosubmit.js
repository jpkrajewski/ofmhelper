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
