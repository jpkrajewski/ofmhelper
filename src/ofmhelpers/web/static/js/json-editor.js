/*
 * Turns a plain <textarea data-json-editor> into a JSON editor: syntax
 * colouring, a Format button (JSON.parse -> re-stringify with 2-space
 * indent), a live valid/invalid readout, and a submit guard so a broken
 * prompt never reaches the generator.
 *
 * Same backdrop-overlay technique as prompt-highlight.js -- a <textarea>
 * cannot render coloured text, so a .prompt-backdrop behind it renders the
 * same text with coloured spans while the textarea's own text is
 * transparent (caret-color keeps the caret visible). The backdrop must match
 * the textarea's font/padding/border metrics exactly (see .json-backdrop in
 * app.css) or the colours drift out of alignment.
 *
 * Deliberately hand-rolled rather than pulling in CodeMirror/Ace: this is one
 * textarea on one page, and the app ships no bundler or vendored JS.
 */
(function () {
    const TOKEN_RE =
        /("(?:\\.|[^"\\])*")(\s*:)?|(-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b)|\b(true|false|null)\b/g;

    function escapeHtml(text) {
        return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }

    function highlight(text) {
        return escapeHtml(text).replace(
            TOKEN_RE,
            (match, str, colon, num, lit) => {
                if (str && colon) return `<span class="jk">${str}</span>${colon}`;
                if (str) return `<span class="js">${str}</span>`;
                if (num) return `<span class="jn">${num}</span>`;
                if (lit) return `<span class="jl">${lit}</span>`;
                return match;
            },
        );
    }

    function initJsonEditor(textarea) {
        const wrap = document.createElement("div");
        wrap.className = "prompt-wrap json-wrap";
        textarea.parentNode.insertBefore(wrap, textarea);

        const backdrop = document.createElement("div");
        backdrop.className = "prompt-backdrop json-backdrop";
        wrap.appendChild(backdrop);
        wrap.appendChild(textarea);

        const bar = document.createElement("div");
        bar.className = "json-bar";
        bar.innerHTML =
            '<button type="button" class="btn btn-ghost json-format">Format</button>' +
            '<span class="json-status"></span>';
        wrap.parentNode.insertBefore(bar, wrap);

        const status = bar.querySelector(".json-status");
        const form = textarea.form;

        function validate() {
            try {
                JSON.parse(textarea.value);
                status.className = "json-status ok";
                status.textContent = "valid JSON";
                return true;
            } catch (err) {
                status.className = "json-status bad";
                status.textContent = err.message;
                return false;
            }
        }

        function sync() {
            let text = textarea.value;
            // A trailing newline renders as zero height in a div but as a
            // visible extra line in a textarea -- pad it so heights match.
            if (text.endsWith("\n")) text += " ";
            backdrop.innerHTML = highlight(text);
            backdrop.scrollTop = textarea.scrollTop;
            backdrop.scrollLeft = textarea.scrollLeft;
            validate();
        }

        bar.querySelector(".json-format").addEventListener("click", () => {
            try {
                textarea.value = JSON.stringify(JSON.parse(textarea.value), null, 2);
            } catch (err) {
                // Nothing to format -- validate() already shows why.
            }
            sync();
        });

        // Tab indents instead of leaving the field: this textarea is the
        // page's main editing surface, not one input among many.
        textarea.addEventListener("keydown", (event) => {
            if (event.key !== "Tab" || event.shiftKey) return;
            event.preventDefault();
            const { selectionStart: start, selectionEnd: end, value } = textarea;
            textarea.value = value.slice(0, start) + "  " + value.slice(end);
            textarea.selectionStart = textarea.selectionEnd = start + 2;
            sync();
        });

        textarea.addEventListener("input", sync);
        textarea.addEventListener("scroll", () => {
            backdrop.scrollTop = textarea.scrollTop;
            backdrop.scrollLeft = textarea.scrollLeft;
        });

        if (form) {
            // Capture phase: generation.js submits on the same event, and an
            // invalid prompt must not reach it.
            form.addEventListener(
                "submit",
                (event) => {
                    if (validate()) return;
                    event.preventDefault();
                    event.stopImmediatePropagation();
                    status.textContent = "fix the JSON before generating: " + status.textContent;
                    textarea.focus();
                },
                true,
            );
        }

        sync();
    }

    document.addEventListener("DOMContentLoaded", () => {
        document.querySelectorAll("textarea[data-json-editor]").forEach(initJsonEditor);
    });
})();
