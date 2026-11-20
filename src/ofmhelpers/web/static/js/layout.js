(() => {
    const group = document.querySelector(".ph-layout[data-layout-key]");
    if (!group) return;
    const key = group.dataset.layoutKey;
    const buttons = [...group.querySelectorAll("[data-layout]")];
    let current = "grid";

    function apply(layout = current) {
        current = layout;
        document.querySelectorAll(".ph-grid").forEach((grid) => { grid.dataset.layout = layout; });
        buttons.forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.layout === layout)));
    }

    try {
        const saved = window.localStorage.getItem(key);
        if (saved === "list" || saved === "grid") apply(saved);
    } catch {

    }
    buttons.forEach((button) => {
        button.addEventListener("click", () => {
            apply(button.dataset.layout);
            try {
                window.localStorage.setItem(key, button.dataset.layout);
            } catch {

            }
        });
    });
    window.Layout = { apply };
})();
