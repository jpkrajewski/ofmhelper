(() => {
    const form = document.getElementById("generate-form");
    const providerSelect = document.getElementById("provider-select");
    const modelSelect = document.getElementById("model-select");
    let fieldset = document.getElementById("model-fields");
    const promptField = document.getElementById("prompt-field");
    const promptBox = form?.querySelector('textarea[name="prompt"]');
    const catalogEl = document.getElementById("model-catalog");
    if (!form || !providerSelect || !modelSelect || !fieldset || !catalogEl) return;

    const MODELS = JSON.parse(catalogEl.textContent);
    const byKey = Object.fromEntries(MODELS.map((m) => [m.key, m]));

    const lastModel = {};

    let lastPromptDefault = "";

    const fieldsCache = new Map();

    let currentLoad = Promise.resolve();

    let recreateSeq = 0;

    function fieldsHtml(model) {
        if (!fieldsCache.has(model.key)) {
            const pending = fetch(`/ai/fields?model=${encodeURIComponent(model.key)}`).then((r) => {
                if (!r.ok) throw new Error(`Could not load ${model.label}'s settings`);
                return r.text();
            });

            pending.catch(() => fieldsCache.delete(model.key));
            fieldsCache.set(model.key, pending);
        }
        return fieldsCache.get(model.key);
    }

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

    function loadModel() {
        const model = byKey[modelSelect.value];
        if (!model) return Promise.resolve();
        lastModel[model.provider] = model.key;
        form.dataset.resultKind = model.kind;
        form.dataset.toolLabel = model.label;

        const batchField = document.getElementById("batch-field");
        if (batchField) batchField.hidden = model.kind !== "image";
        applyPrompt(model);
        currentLoad = fieldsHtml(model)
            .then((html) => {
                if (modelSelect.value !== model.key) return;
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
        return currentLoad;
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

            field.dispatchEvent(new Event("input", { bubbles: true }));
        }
    }

    document.querySelector("#results-panel .results").addEventListener("click", (e) => {
        if (!e.target.closest(".recreate-btn")) return;
        const card = e.target.closest(".result-item[data-task]");
        if (!card) return;
        const params = JSON.parse(card.dataset.params || "{}");
        const seq = ++recreateSeq;

        fill({ prompt: params.prompt || "" });
        if (params.prompt) lastPromptDefault = null;
        const model = byKey[params.model];

        if (!model) {
            form.scrollIntoView({ behavior: "smooth" });
            return;
        }
        lastModel[model.provider] = model.key;
        if (providerSelect.value !== model.provider) {

            providerSelect.value = model.provider;
            providerSelect.dispatchEvent(new Event("change"));
        } else if (modelSelect.value !== model.key) {
            fillModels(model.provider, model.key);
            loadModel();
        }
        currentLoad.then(() => {
            if (seq !== recreateSeq) return;
            fill(params);
            form.scrollIntoView({ behavior: "smooth" });
        });
    });

    fillModels(providerSelect.value);
    loadModel();
})();
