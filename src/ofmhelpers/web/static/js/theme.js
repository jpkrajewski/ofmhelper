/*
 * Light / dark: the header's `.theme-toggle` flips <html data-theme> and
 * remembers it in the `ofm_theme` cookie, which base.html reads so the
 * next page is rendered in that mode from the first paint (no flash).
 */
(() => {
    const toggle = document.querySelector(".theme-toggle");
    if (!toggle) return;
    const root = document.documentElement;
    const year = 60 * 60 * 24 * 365;
    toggle.addEventListener("click", () => {
        const dark = root.dataset.theme !== "dark";
        if (dark) root.dataset.theme = "dark";
        else delete root.dataset.theme;
        document.cookie = `ofm_theme=${dark ? "dark" : "light"}; path=/; max-age=${year}; SameSite=Lax`;
    });
})();
