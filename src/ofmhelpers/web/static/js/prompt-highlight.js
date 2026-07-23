/*
 * Colors asset references typed into a prompt -- tokens like [Image1],
 * [Video 2], @image_1, @audio1 -- per media type (image / video / audio).
 *
 * A <textarea> can't render colored text, so this uses the standard
 * backdrop-overlay technique: the textarea is wrapped in a .prompt-wrap, a
 * .prompt-backdrop div behind it renders the SAME text (transparent color)
 * with <mark> backgrounds under each reference token, and the textarea sits
 * on top with a transparent background. The backdrop must match the
 * textarea's font/padding/border metrics exactly (see .prompt-backdrop in
 * app.css) or the highlights drift out of alignment.
 *
 * Applies to every `textarea.prompt-input` on the page. Programmatic value
 * changes (e.g. click-to-reuse restoring a prompt) must dispatch an "input"
 * event for the highlight to refresh -- generate_form.html's restore code
 * does.
 */
(function () {
    // (?!\w) keeps "@imagery" from lighting up as "@image".
    const TOKEN_RE = /(\[\s*(image|video|audio)\s*_?\s*\d*\s*\]|@(image|video|audio)_?\d*(?!\w))/gi;

    function escapeHtml(text) {
        return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }

    function highlight(text) {
        return escapeHtml(text).replace(TOKEN_RE, (match, _full, t1, t2) => {
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
            // A trailing newline renders as zero height in a div but as a
            // visible extra line in a textarea -- pad it so heights match.
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
})();
