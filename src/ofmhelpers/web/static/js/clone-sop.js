/*
 * /clone "SOP": a fixed handful of examples (features/clone/examples.py).
 * The toolbar's SOP button opens the dialog, filled from GET /clone/examples
 * each time; "Use this prompt" puts that example's prompt in the form (an
 * `input` event, so the add-on chips of clone-picks.js follow). Nothing here
 * adds or changes an example: they are set on the server.
 */
(() => {
    const ENDPOINT = "/clone/examples";

    function say(list, text) {
        const line = document.createElement("p");
        line.className = "sop-example-prompt";
        line.textContent = text;
        list.replaceChildren(line);
    }

    function load(list) {
        return fetch(ENDPOINT)
            .then((response) => {
                if (!response.ok) throw new Error(`examples ${response.status}`);
                return response.text();
            })
            .then((html) => {
                list.innerHTML = html;
            })
            .catch(() => say(list, "Could not load the examples. Try again."));
    }

    function usePrompt(dialog, example) {
        const prompt = document.querySelector('#clone-form [name="prompt"]');
        if (!prompt) return;
        prompt.value = example.dataset.prompt;
        prompt.dispatchEvent(new Event("input", { bubbles: true }));
        dialog.close();
        prompt.scrollIntoView({ block: "center" });
        prompt.focus();
    }

    document.addEventListener("DOMContentLoaded", () => {
        const dialog = document.getElementById("sop-dialog");
        const list = document.getElementById("sop-examples");
        if (!dialog || !list) return;
        dialog.addEventListener("click", (e) => {
            if (e.target.closest(".sop-close")) {
                dialog.close();
                return;
            }
            const example = e.target.closest(".sop-example");
            if (example && e.target.closest(".sop-use")) usePrompt(dialog, example);
        });
        // A video left playing must not go on behind the closed dialog.
        dialog.addEventListener("close", () => list.replaceChildren());
        document.querySelector(".sop-open")?.addEventListener("click", () => {
            dialog.showModal();
            load(list);
        });
    });
})();
