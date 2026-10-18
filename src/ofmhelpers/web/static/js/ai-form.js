/*
 * /generate's provider -> model -> settings form. The model list comes from
 * the page's #model-catalog JSON (every model of every provider); picking a
 * model loads its settings and file pickers from /ai/fields, built from its
 * input schema server-side, so this file never names a model or a field.
 *
 * Files already picked follow a model switch (images to images, ...), the
 * shared prompt box is required / optional / hidden per model, and a model's
 * default prompt fills the box only while it holds no text of the user's.
 * "↻ Recreate" on a gallery card restores provider, model, settings and
 * reference files (reused from the server, nothing re-uploaded).
 */
(function () {
    const form = document.getElementById("generate-form");
    const providerSelect = document.getElementById("provider-select");
    const modelSelect = document.getElementById("model-select");
    let fieldset = document.getElementById("model-fields");
    const promptField = document.getElementById("prompt-field");
    const promptBox = form && form.querySelector('textarea[name="prompt"]');
    const catalogEl = document.getElementById("model-catalog");
    if (!form || !providerSelect || !modelSelect || !fieldset || !catalogEl) return;

    const MODELS = JSON.parse(catalogEl.textContent);
    const byKey = Object.fromEntries(MODELS.map((m) => [m.key, m]));
    // The model last picked per provider, so switching back keeps it.
    const lastModel = {};
    // Whatever default this file last put in the prompt box: still holding
    // it means the user typed nothing, so a new default may replace it.
    let lastPromptDefault = "";

    function fillModels(provider, wanted) {
        modelSelect.innerHTML = "";
        const groups = {};
        MODELS.filter((m) => m.provider === provider).forEach((m) => {
            if (!groups[m.family]) {
                groups[m.family] = document.createElement("optgroup");
                groups[m.family].label = m.family;
                modelSelect.appendChild(groups[m.family]);
            }
            const option = document.createElement("option");
            option.value = m.key;
            option.textContent = `${m.label} (${m.kind})`;
            groups[m.family].appendChild(option);
        });
        const keep = wanted || lastModel[provider];
        if (keep && byKey[keep] && byKey[keep].provider === provider) modelSelect.value = keep;
    }

    function applyPrompt(model) {
        promptField.style.display = model.prompt === "none" ? "none" : "";
        promptBox.disabled = model.prompt === "none";
        promptBox.required = model.prompt === "required";
        if (promptBox.value !== "" && promptBox.value !== lastPromptDefault) return;
        const next = model.default_prompt || "";
        promptBox.value = next;
        lastPromptDefault = next;
        promptBox.dispatchEvent(new Event("input", { bubbles: true }));
    }

    // Swap in the picked model's fields. Returns a promise that resolves once
    // they are on the page, which Recreate waits for before filling them.
    function loadModel() {
        const model = byKey[modelSelect.value];
        if (!model) return Promise.resolve();
        lastModel[model.provider] = model.key;
        form.dataset.resultKind = model.kind;
        form.dataset.toolLabel = model.label;
        applyPrompt(model);
        return fetch(`/ai/fields?model=${encodeURIComponent(model.key)}`)
            .then((r) => {
                if (!r.ok) throw new Error(`Could not load ${model.label}'s settings`);
                return r.text();
            })
            .then((html) => {
                if (modelSelect.value !== model.key) return; // switched again meanwhile
                const next = document.createElement("fieldset");
                next.className = fieldset.className;
                next.id = fieldset.id;
                next.innerHTML = html;
                if (window.FilePicker) {
                    next.querySelectorAll(".file-picker").forEach(window.FilePicker.init);
                    window.FilePicker.carryOver(fieldset, next);
                }
                fieldset.replaceWith(next);
                fieldset = next;
            })
            .catch((err) => {
                fieldset.innerHTML = "";
                const p = document.createElement("p");
                p.className = "generation-error";
                p.textContent = err.message;
                fieldset.appendChild(p);
            });
    }

    providerSelect.addEventListener("provider:change", () => {
        fillModels(providerSelect.value);
        loadModel();
    });
    modelSelect.addEventListener("change", loadModel);

    function fill(params) {
        for (const [key, value] of Object.entries(params)) {
            if (key === "provider" || key === "model") continue;
            if (Array.isArray(value)) {
                if (window.FilePicker) window.FilePicker.setExisting(key, value);
                continue;
            }
            const field =
                key === "prompt"
                    ? promptBox
                    : fieldset.querySelector(`[name="${key}"]:not([type="hidden"])`);
            if (!field) continue;
            if (field.type === "checkbox") field.checked = !!value;
            else field.value = value;
            // programmatic .value changes fire no events -- the prompt
            // highlighter (and anything else listening) needs this
            field.dispatchEvent(new Event("input", { bubbles: true }));
        }
    }

    document.querySelector("#results-panel .results").addEventListener("click", (e) => {
        if (!e.target.closest(".recreate-btn")) return;
        const card = e.target.closest(".result-item[data-task]");
        if (!card) return;
        const params = JSON.parse(card.dataset.params || "{}");
        const model = byKey[params.model];
        // A job from before /ai (or of a model no longer listed): only its
        // prompt carries over.
        if (!model) {
            fill({ prompt: params.prompt || "" });
            form.scrollIntoView({ behavior: "smooth" });
            return;
        }
        if (providerSelect.value !== model.provider) {
            providerSelect.value = model.provider;
            providerSelect.dispatchEvent(new Event("change"));
        }
        fillModels(model.provider, model.key);
        lastPromptDefault = promptBox.value; // let the job's prompt replace it
        loadModel().then(() => {
            fill(params);
            form.scrollIntoView({ behavior: "smooth" });
        });
    });

    fillModels(providerSelect.value);
    loadModel();
})();
