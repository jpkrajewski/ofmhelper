/*
 * ⋯ menus (components/ui.html `menu`): one open at a time; an outside
 * click, Escape, or picking an item closes it. Delegated on the document,
 * so menus added after load (gallery cards) work too.
 */
(() => {
    const openMenus = () => [...document.querySelectorAll("details.menu[open]")];

    document.addEventListener("click", (event) => {
        const picked = event.target.closest(".menu-item");
        openMenus().forEach((menu) => {
            if (!menu.contains(event.target) || (picked && menu.contains(picked))) {
                menu.open = false;
            }
        });
    });

    document.addEventListener("toggle", (event) => {
        const menu = event.target;
        if (!menu.matches?.("details.menu") || !menu.open) return;
        openMenus().forEach((other) => { if (other !== menu) other.open = false; });
    }, true);

    document.addEventListener("keydown", (event) => {
        if (event.key !== "Escape") return;
        openMenus().forEach((menu) => {
            menu.open = false;
            menu.querySelector("summary").focus();
        });
    });
})();
