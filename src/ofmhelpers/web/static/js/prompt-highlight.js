(() => {

    const TOKEN_RE = /(\[\s*(image|video|audio)\s*_?\s*\d*\s*\]|@(image|video|audio)_?\d*(?!\w))/gi;

    function escapeHtml(text) {
        return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }

    function markAddons(html) {
        const texts = new Set([...document.querySelectorAll(".addon-chip")].map((c) => escapeHtml(c.dataset.text)));
        return [...texts].reduce((marked, t) => marked.replace(t, () => `<mark class="ref-mark">${t}</mark>`), html);
    }

    function highlight(text) {
        return markAddons(escapeHtml(text)).replace(TOKEN_RE, (match, _full, t1, t2) => {
            const type = (t1 || t2).toLowerCase();
            return `<mark class="ref-mark ref-mark-${type}">${match}</mark>`;
        });
    }

    function initPromptHighlight(textarea) {
        const wrap = document.createElement("div");
        wrap.className = "prompt-wrap";
        textarea.parentNode.insertBefore(wrap, textarea);

        const backdrop = document.createElement("div");
        backdrop.className = "prompt-backdrop";
        wrap.appendChild(backdrop);
        wrap.appendChild(textarea);

        function sync() {
            let text = textarea.value;

            if (text.endsWith("\n")) text += " ";
            backdrop.innerHTML = highlight(text);
            backdrop.scrollTop = textarea.scrollTop;
            backdrop.scrollLeft = textarea.scrollLeft;
        }

        textarea.addEventListener("input", sync);
        textarea.addEventListener("scroll", () => {
            backdrop.scrollTop = textarea.scrollTop;
            backdrop.scrollLeft = textarea.scrollLeft;
        });
        sync();
    }

    document.addEventListener("DOMContentLoaded", () => {
        document.querySelectorAll("textarea.prompt-input").forEach(initPromptHighlight);
    });

    window.PromptHighlight = { html: highlight };
})();
