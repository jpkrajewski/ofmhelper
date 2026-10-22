/*
 * /links page controller.
 *
 * Server-rendered from the query string: a select in form[data-autosubmit]
 * (period, provider, model) submits it on change, blank fields left out of
 * the URL; the per-page select follows its option's link. The search box
 * filters the rendered rows instantly (their data-search text). Writes post
 * JSON:
 *   Generate links -> POST /links/generate, then reload (rows being made)
 *   Sync           -> POST /links/sync
 *   selected rows  -> Copy links (clipboard), POST /links/creator,
 *                     /links/adult, /links/delete
 * While a row is being made or deleted the list reloads every POLL_MS,
 * unless something on it is in use.
 */
(() => {

    const POLL_MS = 5000;
    const $ = (id) => document.getElementById(id);
    const statusEl = $("links-status");

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
            body: JSON.stringify(body ?? {}),
        });
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

    async function copy(text) {
        await navigator.clipboard.writeText(text);
    }

    // --- filters and period -------------------------------------------------
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
    const perPage = $("links-per-page");
    if (perPage) {
        perPage.addEventListener("change", () => { window.location.href = perPage.value; });
    }

    // --- copy one ------------------------------------------------------------
    document.querySelectorAll(".links-copy").forEach((button) => {
        button.addEventListener("click", async () => {
            try {
                await copy(button.closest("tr[data-url]").dataset.url);
                setStatus(statusEl, "Link copied");
            } catch (error) {
                setStatus(statusEl, error.message, true);
            }
        });
    });

    // --- sync ----------------------------------------------------------------
    $("links-sync").addEventListener("click", async () => {
        try {
            const result = await postJson("/links/sync");
            setStatus(statusEl, result.count ? "Syncing… reload in a moment" : "Nothing to sync: add an API key on Account");
            if (result.count) window.setTimeout(() => window.location.reload(), POLL_MS);
        } catch (error) {
            setStatus(statusEl, error.message, true);
        }
    });

    // --- generate dialog -----------------------------------------------------
    const dialog = $("links-generate");
    const form = $("links-generate-form");
    const genStatus = $("links-generate-status");
    const accountRows = [...document.querySelectorAll("#links-gen-accounts label")];
    const allBox = $("links-gen-all");

    function showDomains() {
        const provider = form.provider.value;
        let first = null;
        [...form.domain.options].forEach((option) => {
            const mine = option.dataset.provider === provider;
            option.hidden = !mine;
            option.disabled = !mine;
            if (mine && !first) first = option;
        });
        if (form.domain.selectedOptions[0]?.disabled && first) first.selected = true;
    }

    // How many accounts may be picked: the provider's free live slots (as
    // the last sync saw them), at most one Generate's maximum.
    const picker = document.querySelector(".links-picker");
    function pickLimit() {
        const room = form.provider.selectedOptions[0]?.dataset.room;
        const max = Number(picker.dataset.max);
        return room === undefined ? max : Math.min(Number(room), max);
    }
    const shownRows = () => accountRows.filter((row) => !row.hidden);
    const boxOf = (row) => row.querySelector("input");

    // At the limit, the unpicked boxes lock; under it, all are free again.
    function applyLimit() {
        const limit = pickLimit();
        $("links-gen-limit").textContent = String(limit);
        const picked = shownRows().filter((row) => boxOf(row).checked).length;
        shownRows().forEach((row) => {
            const box = boxOf(row);
            box.disabled = !box.checked && picked >= limit;
        });
        allBox.checked = picked > 0 && picked === Math.min(limit, shownRows().length);
    }

    // The first `limit` shown accounts picked, the rest not.
    function pickFirst(on) {
        const limit = pickLimit();
        shownRows().forEach((row, i) => { boxOf(row).checked = on && i < limit; });
        applyLimit();
    }

    function showAccounts() {
        const creator = form.creator.value;
        accountRows.forEach((row) => {
            row.hidden = !(creator && row.dataset.creator === creator);
            boxOf(row).checked = false;
        });
        $("links-gen-none").hidden = !creator || shownRows().length > 0;
        pickFirst(true);
    }

    // The target link: its prefix filled in, checked as it is typed (the
    // server checks the same pattern).
    const destination = form.destination;
    const destinationProblem = $("links-gen-destination-problem");
    const target = () => form.target.selectedOptions[0];
    const prefixes = [...form.target.options].map((o) => o.dataset.prefix);

    function destinationError() {
        const option = target();
        const value = destination.value.trim();
        let url;
        try {
            url = new URL(value);
        } catch {
            return `Paste the ${option.text} profile link`;
        }
        const host = url.hostname.toLowerCase();
        const hosts = option.dataset.hosts.split(" ");
        if (!hosts.some((h) => host === h || host.endsWith(`.${h}`))) {
            return `Not a ${option.text} link`;
        }
        if (!new RegExp(option.dataset.profile).test(url.pathname)) {
            return `Add the ${option.text} profile after ${option.dataset.prefix}`;
        }
        return "";
    }

    function checkDestination(show) {
        const problem = destinationError();
        destinationProblem.textContent = problem;
        destinationProblem.hidden = !(show && problem);
        return problem;
    }

    function prefillDestination() {
        const value = destination.value.trim();
        // Only a blank field or another target's bare prefix is replaced.
        if (!value || prefixes.includes(value)) destination.value = target().dataset.prefix;
        checkDestination(false);
    }

    form.target.addEventListener("change", () => {
        prefillDestination();
        checkDestination(destination.value !== target().dataset.prefix);
    });
    destination.addEventListener("input", () => checkDestination(true));

    $("links-generate-open").addEventListener("click", () => {
        setStatus(genStatus, "");
        const usable = [...form.provider.options].find((o) => !o.disabled);
        if (usable && form.provider.selectedOptions[0]?.disabled) usable.selected = true;
        showDomains();
        showAccounts();
        prefillDestination();
        dialog.showModal();
    });
    $("links-generate-cancel").addEventListener("click", () => dialog.close());
    form.provider.addEventListener("change", () => {
        showDomains();
        pickFirst(true);
    });
    form.creator.addEventListener("change", showAccounts);
    allBox.addEventListener("change", () => pickFirst(allBox.checked));
    accountRows.forEach((row) => boxOf(row).addEventListener("change", applyLimit));

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const ids = accountRows
            .filter((row) => !row.hidden && row.querySelector("input").checked)
            .map((row) => Number(row.querySelector("input").value));
        if (!ids.length) {
            setStatus(genStatus, "Pick at least one account", true);
            return;
        }
        if (checkDestination(true)) {
            destination.focus();
            return;
        }
        const provider = form.provider.selectedOptions[0];
        const room = provider.dataset.room;
        if (room !== undefined && ids.length > Number(room)) {
            setStatus(genStatus, `${provider.text.replace(/ \(.*$/, "")} has room for ${room} more live link(s): pick fewer accounts`, true);
            return;
        }
        const submit = form.querySelector("[type=submit]");
        submit.disabled = true;
        setStatus(genStatus, "Generating…");
        try {
            const result = await postJson("/links/generate", {
                provider: form.provider.value,
                creator: form.creator.value,
                domain: form.domain.value,
                target: form.target.value,
                destination: form.destination.value,
                account_ids: ids,
            });
            setStatus(genStatus, `Making ${result.count} link(s)…`);
            window.location.reload();
        } catch (error) {
            setStatus(genStatus, error.message, true);
            submit.disabled = false;
        }
    });

    // --- instant search ------------------------------------------------------
    const search = $("links-search");
    const noMatch = $("links-no-match");
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

    // --- selection + bulk actions -------------------------------------------
    const bulk = $("links-bulk");
    const picks = () => [...document.querySelectorAll("tr:not([hidden]) .links-pick:checked")]
        .map((box) => box.closest("tr[data-id]"));
    if (bulk) {
        const bulkCount = $("links-bulk-count");
        const pickAll = $("links-pick-all");
        const ids = () => picks().map((row) => Number(row.dataset.id));
        const refreshBulk = () => {
            const n = picks().length;
            bulk.hidden = n === 0;
            bulkCount.textContent = `${n} selected`;
        };
        const run = async (url, body, done) => {
            try {
                const result = await postJson(url, body);
                setStatus(statusEl, done(result));
                window.setTimeout(() => window.location.reload(), 800);
            } catch (error) {
                setStatus(statusEl, error.message, true);
            }
        };
        document.querySelectorAll(".links-pick").forEach((box) => box.addEventListener("change", refreshBulk));
        pickAll.addEventListener("change", () => {
            document.querySelectorAll("tr:not([hidden]) .links-pick").forEach((box) => { box.checked = pickAll.checked; });
            refreshBulk();
        });
        $("links-bulk-copy").addEventListener("click", async () => {
            const urls = picks().map((row) => row.dataset.url).filter(Boolean);
            try {
                await copy(urls.join("\n"));
                setStatus(statusEl, `${urls.length} link(s) copied`);
            } catch (error) {
                setStatus(statusEl, error.message, true);
            }
        });
        $("links-bulk-assign").addEventListener("click", () => run(
            "/links/creator",
            { ids: ids(), creator: $("links-bulk-creator").value },
            (r) => `${r.count} link(s) updated`,
        ));
        $("links-bulk-adult").addEventListener("click", () => run(
            "/links/adult", { ids: ids() }, (r) => `18+ turning on for ${r.count} link(s)…`,
        ));
        $("links-bulk-delete").addEventListener("click", () => {
            const n = picks().length;
            if (!window.confirm(`Delete ${n} link(s) on their provider? Their addresses stop working.`)) return;
            run("/links/delete", { ids: ids() }, (r) => `Deleting ${r.count} link(s)…`);
        });
    }

    // --- rows being made or deleted -------------------------------------------
    if (document.querySelector('tr[data-status="creating"], tr[data-status="deleting"]')) {
        window.setInterval(() => {
            const busy = dialog.open || picks().length
                || document.activeElement.matches("input, textarea, select");
            if (!busy && !document.hidden) window.location.reload();
        }, POLL_MS);
    }
})();
