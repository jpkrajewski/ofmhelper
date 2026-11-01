/*
 * /pictures: the preset select shows that preset's pictures and prompt; the
 * preset dialog makes a new preset or edits one's own (name, prompt, shape,
 * which current pictures to keep, new ones), Delete removes it; Recreate on a card
 * picks its preset again and puts its pictures back in the picker. The
 * Generate submit itself is generation.js's (the form's data-prefix).
 */
(() => {
    const select = document.getElementById("pictures-preset");
    const dialog = document.getElementById("preset-dialog");
    const presetForm = document.getElementById("preset-form");
    const error = document.getElementById("preset-error");
    let editing = null;

    function current() {
        return document.querySelector(`.pictures-preset[data-preset="${select?.value}"]`);
    }

    function show() {
        document.querySelectorAll(".pictures-preset").forEach((el) => {
            el.hidden = el.dataset.preset !== select.value;
        });
    }

    function keepList(preset) {
        const list = document.getElementById("preset-keep");
        const template = document.getElementById("preset-keep-item");
        list.innerHTML = "";
        if (!preset) return;
        for (let i = 0; i < Number(preset.dataset.count); i += 1) {
            const item = template.content.firstElementChild.cloneNode(true);
            item.querySelector("input").value = String(i);
            item.querySelector("img").src = `/pictures/presets/${preset.dataset.preset}/images/${i}`;
            list.append(item);
        }
    }

    function open(preset) {
        editing = preset ? preset.dataset.preset : null;
        presetForm.reset();
        error.hidden = true;
        document.getElementById("preset-dialog-title").textContent = preset ? "Edit preset" : "New preset";
        document.getElementById("preset-name").value = preset ? preset.dataset.name : "";
        document.getElementById("preset-prompt").value = preset ? preset.dataset.prompt : "";
        document.getElementById("preset-aspect").value = preset ? preset.dataset.aspect : "";
        keepList(preset);
        dialog.showModal();
    }

    function fail(message) {
        error.textContent = message;
        error.hidden = false;
    }

    async function save(e) {
        e.preventDefault();
        const url = editing ? `/pictures/presets/${editing}` : "/pictures/presets";
        const submit = presetForm.querySelector('button[type="submit"]');
        submit.disabled = true;
        try {
            const r = await fetch(url, { method: "POST", body: new FormData(presetForm) });
            const body = await r.json().catch(() => ({}));
            if (!r.ok) {
                fail(typeof body.detail === "string" ? body.detail : "Fill in the name, the prompt and at least one picture");
                return;
            }
            window.location.href = `/pictures?preset=${body.id}`;
        } catch {
            fail("Could not save, try again");
        } finally {
            submit.disabled = false;
        }
    }

    async function remove(preset) {
        if (!window.confirm(`Delete the preset ${preset.dataset.name}?`)) return;
        const r = await fetch(`/pictures/presets/${preset.dataset.preset}/delete`, { method: "POST" });
        if (r.ok) window.location.href = "/pictures";
    }

    function recreate(card) {
        const params = JSON.parse(card.dataset.params || "{}");
        if (select && params.preset && select.querySelector(`option[value="${params.preset}"]`)) {
            select.value = String(params.preset);
            show();
        }
        window.FilePicker.setExisting("pictures", params.pictures || []);
        document.getElementById("pictures-form").scrollIntoView({ behavior: "smooth" });
    }

    document.addEventListener("DOMContentLoaded", () => {
        select?.addEventListener("change", show);
        document.getElementById("preset-new")?.addEventListener("click", () => open(null));
        presetForm.addEventListener("submit", save);
        document.addEventListener("click", (e) => {
            if (e.target.closest("[data-close]")) dialog.close();
            else if (e.target.closest(".preset-edit")) open(current());
            else if (e.target.closest(".preset-delete")) remove(current());
            else if (e.target.closest(".recreate-btn")) {
                const card = e.target.closest(".result-item[data-params]");
                if (card) recreate(card);
            }
        });
    });
})();
