(() => {

    const toggle = document.querySelector(".nav-toggle");
    const nav = document.getElementById("site-nav");
    if (!toggle || !nav) return;

    function setOpen(open) {
        nav.classList.toggle("open", open);
        toggle.setAttribute("aria-expanded", open ? "true" : "false");
    }

    toggle.addEventListener("click", () => {
        setOpen(!nav.classList.contains("open"));
    });

    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && nav.classList.contains("open")) {
            setOpen(false);
            toggle.focus();
        }
    });

    const wide = window.matchMedia("(min-width: 980px)");
    wide.addEventListener("change", (e) => {
        if (e.matches) setOpen(false);
    });

    const more = nav.querySelector(".nav-more details");
    if (more) {
        document.addEventListener("click", (e) => {
            if (more.open && !more.contains(e.target)) more.open = false;
        });
    }
})();
