/*
 * /phones page controller.
 *
 * The page carries no password or recovery code. Show, Copy and Edit fetch
 * one login's from GET /phones/logins/{id}/secret each time and keep it
 * only in the element showing it; hiding empties it again.
 *
 * Everyone: instant search over the rendered cards (data-search: phone,
 * model, people, login names -- never a secret); filters and sort submit
 * the toolbar form; grid/list layout (remembered in localStorage); copy
 * login names; reveal / copy passwords; open recovery codes; "N more".
 * Admins (dialogs only rendered for them), posting JSON then reloading:
 *   Add phone / Edit      -> POST /phones/add | /phones/{id}
 *   Retire / Restore      -> POST /phones/{id}/retire | /restore
 *   Delete                -> POST /phones/{id}/delete
 *   Add login / Edit      -> POST /phones/{id}/logins | /phones/logins/{id}
 *   Remove login          -> POST /phones/{id}/logins/{login}/delete
 */
(() => {

    const $ = (id) => document.getElementById(id);
    const statusEl = $("ph-status");
    const LAYOUT_KEY = "phones-layout";

    function setStatus(el, text, isError) {
        if (!el) return;
        el.innerHTML = "";
        if (!text) return;
        const line = document.createElement("div");
        if (isError) line.className = "generation-error";
        line.textContent = text;
        el.appendChild(line);
    }

    async function request(url, body) {
        const options = body === undefined ? {} : {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        };
        const response = await fetch(url, options);
        let data = null;
        try {
            data = await response.json();
        } catch {
            // not JSON: the status line below says enough
        }
        if (!response.ok) {
            const detail = data?.detail;
            const message = typeof detail === "string" ? detail
                : Array.isArray(detail) && detail.length ? detail[0].msg.replace(/^Value error, /, "")
                : `Request failed (${response.status})`;
            throw new Error(message);
        }
        return data;
    }

    const cardOf = (el) => el.closest(".ph-card");
    const rowOf = (el) => el.closest(".ph-login");
    const secretOf = (row) => request(`/phones/logins/${row.dataset.loginId}/secret`);

    function flash(button) {
        button.classList.add("is-done");
        window.setTimeout(() => button.classList.remove("is-done"), 1200);
    }

    // The Clipboard API exists only on https or localhost; anywhere else
    // (the app over plain http on a LAN address) copy through a selected,
    // off-screen textarea instead.
    function copyFallback(text) {
        const area = document.createElement("textarea");
        area.value = text;
        area.setAttribute("readonly", "");
        area.style.position = "fixed";
        area.style.opacity = "0";
        document.body.appendChild(area);
        area.select();
        const ok = document.execCommand("copy");
        area.remove();
        return ok;
    }

    async function copy(button, text) {
        let ok = false;
        try {
            if (navigator.clipboard) {
                await navigator.clipboard.writeText(text);
                ok = true;
            } else {
                ok = copyFallback(text);
            }
        } catch {
            ok = copyFallback(text);
        }
        if (ok) flash(button);
        else setStatus(statusEl, "Copy failed: the browser blocked the clipboard.", true);
    }

    // --- search ---------------------------------------------------------------
    const search = $("ph-search");
    if (search) {
        const noMatch = $("ph-no-match");
        search.addEventListener("input", () => {
            const needle = search.value.trim().replace(/^@/, "").toLowerCase();
            let shown = 0;
            document.querySelectorAll("#ph-grid .ph-card").forEach((card) => {
                const match = !needle || card.dataset.search.includes(needle);
                card.hidden = !match;
                if (match) shown += 1;
            });
            if (noMatch) noMatch.hidden = shown > 0;
        });
    }

    // --- filters / sort -------------------------------------------------------
    document.querySelectorAll("form[data-autosubmit]").forEach((form) => {
        // Blank fields and the default sort (the server's, in data-default)
        // stay out of the URL: the server reads them as unset too.
        form.addEventListener("submit", () => {
            form.querySelectorAll("select").forEach((field) => {
                field.disabled = field.value === "" || field.value === field.dataset.default;
            });
        });
        form.querySelectorAll("select").forEach((select) => {
            select.addEventListener("change", () => form.requestSubmit());
        });
    });

    // --- layout ---------------------------------------------------------------
    function setLayout(layout) {
        document.querySelectorAll(".ph-grid").forEach((grid) => { grid.dataset.layout = layout; });
        document.querySelectorAll(".ph-layout [data-layout]").forEach((b) => {
            b.setAttribute("aria-pressed", String(b.dataset.layout === layout));
        });
    }
    try {
        const saved = window.localStorage.getItem(LAYOUT_KEY);
        if (saved === "list" || saved === "grid") setLayout(saved);
    } catch {
        // storage blocked: grid
    }
    document.querySelectorAll(".ph-layout [data-layout]").forEach((button) => {
        button.addEventListener("click", () => {
            setLayout(button.dataset.layout);
            try {
                window.localStorage.setItem(LAYOUT_KEY, button.dataset.layout);
            } catch {
                // not remembered
            }
        });
    });

    // --- login rows -----------------------------------------------------------
    // Copy = the whole login in one line: username:password:codes (codes
    // space-joined; parts that are empty are left out).
    document.querySelectorAll(".ph-copy-all").forEach((button) => {
        const row = rowOf(button);
        button.addEventListener("click", async () => {
            const username = JSON.parse(row.dataset.login).username;
            let parts = [username];
            if (row.querySelector(".ph-revealed")) {
                try {
                    const secret = await secretOf(row);
                    const codes = secret.backup_codes.split(/\s*\n\s*/).filter(Boolean).join(" ");
                    parts = [username, secret.password, codes].filter(Boolean);
                } catch (error) {
                    setStatus(statusEl, error.message, true);
                    return;
                }
            }
            await copy(button, parts.join(":"));
        });
    });

    // Show = password and recovery codes together, below the row; the row's
    // icons stay where they are.
    document.querySelectorAll(".ph-reveal").forEach((button) => {
        const row = rowOf(button);
        const panel = row.querySelector(".ph-revealed");
        const password = panel.querySelector("[data-secret]");
        const codes = panel.querySelector("[data-codes]");
        button.addEventListener("click", async () => {
            const shown = button.getAttribute("aria-pressed") === "true";
            if (!shown) {
                try {
                    const secret = await secretOf(row);
                    if (password) password.textContent = secret.password;
                    if (codes) codes.textContent = secret.backup_codes;
                } catch (error) {
                    setStatus(statusEl, error.message, true);
                    return;
                }
            } else {
                if (password) password.textContent = "";
                if (codes) codes.textContent = "";
            }
            panel.hidden = shown;
            button.setAttribute("aria-pressed", String(!shown));
            button.querySelector(".ph-when-hidden").hidden = !shown;
            button.querySelector(".ph-when-shown").hidden = shown;
        });
    });

    document.querySelectorAll(".ph-more").forEach((button) => {
        const card = cardOf(button);
        const more = button.dataset.more;
        button.addEventListener("click", () => {
            const open = button.getAttribute("aria-expanded") === "true";
            card.querySelectorAll(".ph-login[data-extra]").forEach((row) => { row.hidden = open; });
            button.setAttribute("aria-expanded", String(!open));
            button.textContent = open ? `${more} more` : "Show less";
        });
    });

    // --- the ⋯ menu: one open at a time, closes on outside click / Escape -----
    const menus = [...document.querySelectorAll(".ph-menu")];
    document.addEventListener("click", (event) => {
        menus.forEach((menu) => {
            if (menu.open && !menu.contains(event.target)) menu.open = false;
        });
    });
    document.addEventListener("keydown", (event) => {
        if (event.key !== "Escape") return;
        menus.filter((m) => m.open).forEach((menu) => {
            menu.open = false;
            menu.querySelector("summary").focus();
        });
    });

    // --- admin ----------------------------------------------------------------
    const phoneDialog = $("ph-dialog");
    const loginDialog = $("ph-login-dialog");
    if (!phoneDialog) return;

    const catalog = JSON.parse($("ph-catalog").textContent);
    const models = new Map();
    catalog.families.forEach((family) => {
        family.models.forEach((model) => models.set(model.id, { family, model }));
    });

    async function act(url, question) {
        if (question && !window.confirm(question)) return;
        try {
            await request(url, {});
            window.location.reload();
        } catch (error) {
            setStatus(statusEl, error.message, true);
        }
    }

    function submitTo(form, statusBox, getTarget, getBody) {
        form.addEventListener("submit", async (event) => {
            event.preventDefault();
            const submit = form.querySelector("[type=submit]");
            submit.disabled = true;
            setStatus(statusBox, "Saving…");
            try {
                await request(getTarget(), getBody());
                window.location.reload();
            } catch (error) {
                setStatus(statusBox, error.message, true);
                submit.disabled = false;
            }
        });
        form.closest("dialog").querySelectorAll("[data-close]").forEach((b) => {
            b.addEventListener("click", () => form.closest("dialog").close());
        });
    }

    // Phone dialog: family -> model -> color swatch, the drawing following
    // the picked model and color.
    const form = $("ph-form");
    const family = $("ph-family");
    const model = $("ph-model");
    const colors = $("ph-colors");
    const drawing = $("ph-drawing");
    let phoneTarget = "/phones/add";

    function option(select, value, label) {
        const el = document.createElement("option");
        el.value = value;
        el.textContent = label;
        select.appendChild(el);
    }

    function pickedColor() {
        return colors.querySelector("input:checked")?.value ?? "";
    }

    function updatePreview() {
        const params = new URLSearchParams({ model: model.value, color: pickedColor() });
        drawing.src = `/phones/drawing.svg?${params}`;
    }

    function fillModel(selected) {
        model.innerHTML = "";
        const fam = catalog.families.find((f) => f.id === family.value);
        const catalogOnly = Boolean(fam);
        phoneDialog.querySelectorAll("[data-catalog-only]").forEach((el) => { el.hidden = !catalogOnly; });
        phoneDialog.querySelectorAll("[data-custom-only]").forEach((el) => { el.hidden = catalogOnly; });
        if (fam) fam.models.forEach((m) => option(model, m.id, m.label));
        if (selected) model.value = selected;
    }

    function fillVariants(color) {
        colors.innerHTML = "";
        const entry = models.get(model.value);
        if (entry) {
            catalog.palettes[entry.model.palette].forEach((c, i) => {
                const label = document.createElement("label");
                label.className = "ph-swatch";
                label.title = c.label;
                const input = document.createElement("input");
                input.type = "radio";
                input.name = "color_id";
                input.value = c.id;
                input.checked = color ? c.id === color : i === 0;
                input.addEventListener("change", updatePreview);
                const dot = document.createElement("span");
                dot.style.setProperty("--finish", c.hex);
                const name = document.createElement("span");
                name.className = "ph-swatch-name";
                name.textContent = c.label;
                label.append(input, dot, name);
                colors.appendChild(label);
            });
        }
        updatePreview();
    }

    family.addEventListener("change", () => { fillModel(); fillVariants(); });
    model.addEventListener("change", () => fillVariants());

    function openPhone(title, target, values) {
        phoneTarget = target;
        phoneDialog.querySelector("h2").textContent = title;
        form.reset();
        ["label", "owner", "phone_number", "notes", "custom_model"].forEach((name) => {
            form.elements[name].value = values[name] ?? "";
        });
        const entry = models.get(values.model_id);
        family.value = entry ? entry.family.id : (values.label ? "" : catalog.families[0].id);
        fillModel(values.model_id);
        fillVariants(values.color_id);
        setStatus($("ph-form-status"), "");
        form.querySelector("[type=submit]").disabled = false;
        phoneDialog.showModal();
    }

    submitTo(form, $("ph-form-status"), () => phoneTarget, () => {
        const inCatalog = family.value !== "";
        return {
            label: form.elements.label.value,
            model_id: inCatalog ? model.value : "",
            color_id: inCatalog ? pickedColor() : "",
            custom_model: inCatalog ? "" : form.elements.custom_model.value,
            owner: form.elements.owner.value,
            phone_number: form.elements.phone_number.value,
            notes: form.elements.notes.value,
        };
    });

    $("ph-add-open").addEventListener("click", () => openPhone("Add phone", "/phones/add", {}));
    document.querySelectorAll(".ph-edit").forEach((button) => {
        button.addEventListener("click", () => {
            const card = cardOf(button);
            const values = JSON.parse(card.dataset.phone);
            openPhone(`Edit ${values.label}`, `/phones/${card.dataset.id}`, values);
        });
    });
    document.querySelectorAll(".ph-retire").forEach((button) => {
        button.addEventListener("click", () => act(`/phones/${cardOf(button).dataset.id}/retire`));
    });
    document.querySelectorAll(".ph-restore").forEach((button) => {
        button.addEventListener("click", () => act(`/phones/${cardOf(button).dataset.id}/restore`));
    });
    document.querySelectorAll(".ph-delete").forEach((button) => {
        button.addEventListener("click", () => {
            const card = cardOf(button);
            const name = JSON.parse(card.dataset.phone).label;
            act(`/phones/${card.dataset.id}/delete`, `Delete ${name} for good? Logins no other phone has are deleted with it. Retire keeps everything instead.`);
        });
    });

    // Login dialog: a custom service asks for its company / site.
    const loginForm = $("ph-login-form");
    const service = $("ph-login-service");
    const providerField = loginDialog.querySelector("[data-provider]");
    const shared = $("ph-login-shared");
    let loginTarget = "";

    function syncProvider() {
        const custom = service.selectedOptions[0]?.dataset.custom === "true";
        providerField.hidden = !custom;
        loginForm.elements.provider.required = custom;
    }
    service.addEventListener("change", syncProvider);

    function openLogin(title, target, values, secrets, others) {
        loginTarget = target;
        loginDialog.querySelector("h2").textContent = title;
        loginForm.reset();
        service.value = values.service ?? service.options[0].value;
        ["provider", "username", "notes"].forEach((name) => {
            loginForm.elements[name].value = values[name] ?? "";
        });
        loginForm.elements.password.value = secrets?.password ?? "";
        loginForm.elements.backup_codes.value = secrets?.backup_codes ?? "";
        shared.hidden = !others;
        shared.textContent = others ? `Also on ${others}: changes apply there too.` : "";
        syncProvider();
        setStatus($("ph-login-status"), "");
        loginForm.querySelector("[type=submit]").disabled = false;
        loginDialog.showModal();
    }

    loginDialog.addEventListener("close", () => {
        // The decrypted values leave the page with the dialog.
        loginForm.elements.password.value = "";
        loginForm.elements.backup_codes.value = "";
    });

    submitTo(loginForm, $("ph-login-status"), () => loginTarget, () => Object.fromEntries(
        ["service", "provider", "username", "password", "backup_codes", "notes"].map((f) => [f, loginForm.elements[f].value]),
    ));

    document.querySelectorAll(".ph-add-login").forEach((button) => {
        button.addEventListener("click", () => {
            const card = cardOf(button);
            const name = JSON.parse(card.dataset.phone).label;
            openLogin(`Add login to ${name}`, `/phones/${card.dataset.id}/logins`, {}, null, "");
        });
    });
    document.querySelectorAll(".ph-login-edit").forEach((button) => {
        button.addEventListener("click", async () => {
            const row = rowOf(button);
            try {
                const secrets = await secretOf(row);
                openLogin("Edit login", `/phones/logins/${row.dataset.loginId}`, JSON.parse(row.dataset.login), secrets, row.dataset.others);
            } catch (error) {
                setStatus(statusEl, error.message, true);
            }
        });
    });
    document.querySelectorAll(".ph-login-remove").forEach((button) => {
        button.addEventListener("click", () => {
            const row = rowOf(button);
            const card = cardOf(button);
            const name = JSON.parse(row.dataset.login).username;
            act(
                `/phones/${card.dataset.id}/logins/${row.dataset.loginId}/delete`,
                `Remove ${name} from this phone? If no other phone has it, its password and recovery codes are deleted for good.`,
            );
        });
    });
})();
