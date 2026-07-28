// Mobile navigation drawer for base.html's sticky header.
//
// Desktop (>=980px) needs no JS at all -- the CSS puts the links back into
// a plain inline row and hides the toggle -- so this only manages the
// small-screen open/closed state and the aria-expanded that goes with it.
(function () {
    "use strict";

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

    // Escape closes and returns focus to the button, so keyboard users
    // aren't stranded inside an open drawer.
    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && nav.classList.contains("open")) {
            setOpen(false);
            toggle.focus();
        }
    });

    // Crossing the breakpoint while the drawer is open would otherwise
    // leave aria-expanded="true" on a button the CSS has just hidden.
    const wide = window.matchMedia("(min-width: 980px)");
    wide.addEventListener("change", (e) => {
        if (e.matches) setOpen(false);
    });
})();
