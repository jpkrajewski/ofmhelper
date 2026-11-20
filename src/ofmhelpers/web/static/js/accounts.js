(() => {

    const POLL_MS = 8000;
    const $ = (id) => document.getElementById(id);
    const statusEl = $("accounts-status");

    function setStatus(el, text, isError) {
        if (!el) return;
        el.innerHTML = "";
        if (!text) return;
        const line = document.createElement("div");
        if (isError) line.className = "generation-error";
        line.textContent = text;
        el.appendChild(line);
    }

    async function postJson(url, body) {
        const response = await fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        let data = null;
        try {
            data = await response.json();
        } catch {

        }
        if (!response.ok) {
            const detail = data?.detail;
            const message = typeof detail === "string" ? detail
                : Array.isArray(detail) && detail.length ? detail[0].msg
                : `Request failed (${response.status})`;
            throw new Error(message);
        }
        return data;
    }

    function rowId(el) {
        return Number(el.closest("tr[data-id]").dataset.id);
    }

    document.querySelectorAll("form[data-autosubmit]").forEach((form) => {

        form.addEventListener("submit", () => {
            form.querySelectorAll("input, select").forEach((field) => {
                field.disabled = field.value === "";
            });
        });
        form.querySelectorAll("select").forEach((select) => {
            select.addEventListener("change", () => form.requestSubmit());
        });
    });
    const perPage = $("accounts-per-page");
    if (perPage) {
        perPage.addEventListener("change", () => { window.location.href = perPage.value; });
    }

    const dialog = $("accounts-add");
    if (dialog) {
        const addForm = $("accounts-add-form");
        const addStatus = $("accounts-add-status");
        $("accounts-add-open").addEventListener("click", () => {
            setStatus(addStatus, "");
            dialog.showModal();
        });
        $("accounts-add-cancel").addEventListener("click", () => dialog.close());
        addForm.addEventListener("submit", async (event) => {
            event.preventDefault();
            const submit = addForm.querySelector("[type=submit]");
            submit.disabled = true;
            setStatus(addStatus, "Adding…");
            try {
                const result = await postJson("/accounts/add", {
                    platform: addForm.platform.value,
                    handles: addForm.handles.value,
                    creator: addForm.creator.value,
                });
                const existing = result.existing ? `, ${result.existing} already tracked` : "";
                setStatus(addStatus, `Added ${result.added.length}${existing}. Syncing…`);
                window.location.reload();
            } catch (error) {
                setStatus(addStatus, error.message, true);
                submit.disabled = false;
            }
        });
    }

    const search = $("accounts-search");
    if (search) {
        const noMatch = $("accounts-no-match");
        search.addEventListener("input", () => {
            const needle = search.value.trim().replace(/^@/, "").toLowerCase();
            let shown = 0;
            document.querySelectorAll("tr[data-search]").forEach((row) => {
                const match = !needle || row.dataset.search.includes(needle);
                row.hidden = !match;
                if (match) shown += 1;
            });
            if (noMatch) noMatch.hidden = shown > 0;
        });
    }

    const bulk = $("accounts-bulk");
    const picks = () => [...document.querySelectorAll("tr:not([hidden]) .accounts-pick:checked")];
    if (bulk) {
        const bulkCount = $("accounts-bulk-count");
        const pickAll = $("accounts-pick-all");
        const refreshBulk = () => {
            const n = picks().length;
            bulk.hidden = n === 0;
            bulkCount.textContent = `${n} selected`;
        };
        document.querySelectorAll(".accounts-pick").forEach((box) => {
            box.addEventListener("change", refreshBulk);
        });
        if (pickAll) {
            pickAll.addEventListener("change", () => {
                document.querySelectorAll("tr:not([hidden]) .accounts-pick").forEach((box) => { box.checked = pickAll.checked; });
                refreshBulk();
            });
        }
        $("accounts-bulk-assign").addEventListener("click", async () => {
            const creator = $("accounts-bulk-creator").value;
            try {
                await postJson("/accounts/creator", { ids: picks().map(rowId), creator });
                window.location.reload();
            } catch (error) {
                setStatus(statusEl, error.message, true);
            }
        });
        $("accounts-bulk-delete").addEventListener("click", async () => {
            const ids = picks().map(rowId);
            if (!window.confirm(`Remove ${ids.length} account(s) everywhere? They leave their phones (password kept under Archived on Phones) and their Drive folders go to the trash.`)) return;
            try {
                await postJson("/accounts/delete", { ids });
                window.location.reload();
            } catch (error) {
                setStatus(statusEl, error.message, true);
            }
        });
    }

    const sync = $("accounts-sync");
    if (sync) {
        sync.addEventListener("click", async () => {
            sync.disabled = true;
            setStatus(statusEl, "Syncing…");
            try {
                await postJson("/accounts/sync");
                window.location.reload();
            } catch (error) {
                setStatus(statusEl, error.message, true);
                sync.disabled = false;
            }
        });
    }

    if (document.querySelector("[data-syncing]")) {
        window.setInterval(() => {
            const busy = (dialog?.open) || picks().length
                || document.activeElement.matches("input, textarea, select");
            if (!busy && !document.hidden) window.location.reload();
        }, POLL_MS);
    }
})();
