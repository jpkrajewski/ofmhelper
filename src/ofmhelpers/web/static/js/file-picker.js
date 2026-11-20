(() => {

    function displayName(path) {
        const base = path.split(/[\\/]/).pop();
        const sep = base.indexOf("__");
        return sep > 0 ? base.slice(sep + 2) : base;
    }

    function isActive(picker) {
        return !picker.closest("fieldset:disabled");
    }

    function buildPreview(kind, item) {

        const existing = item.kind !== "new";
        const src = !existing
            ? URL.createObjectURL(item.file)
            : kind === "audio"
              ? `/refs/file?path=${encodeURIComponent(item.path)}`
              : `/refs/thumb?path=${encodeURIComponent(item.path)}&size=150`;
        if (kind === "image" || (kind === "video" && existing)) {
            const thumb = document.createElement("img");
            thumb.className = "thumb-small";
            thumb.loading = "lazy";
            thumb.src = src;
            return thumb;
        }
        if (kind === "video") {
            const vid = document.createElement("video");
            vid.className = "thumb-small";
            vid.src = src;
            vid.muted = true;
            vid.controls = true;
            vid.preload = "metadata";
            return vid;
        }
        if (kind === "audio") {
            const aud = document.createElement("audio");
            aud.className = "thumb-audio";
            aud.src = src;
            aud.controls = true;
            aud.preload = "metadata";
            return aud;
        }
        return null;
    }

    function checkRequired(picker) {
        const waiting = picker._items.some((item) => item.restoring);
        const required = "required" in picker.dataset;
        if (!required && !waiting && !picker._waited) return;
        picker._waited = waiting;
        let message = "";
        if (waiting) message = "Wait: a video is downloading again";
        else if (required && !picker._items.length) {
            message = `Pick at least one ${picker.dataset.kind}`;
        }
        picker.querySelector(".file-add-input").setCustomValidity(message);
    }

    function renderList(picker) {
        picker.querySelector(".file-picker-missing")?.remove();

        const max = Number(picker.dataset.max || 0);
        if (max && picker._items.length > max) picker._items = picker._items.slice(-max);
        const kind = picker.dataset.kind;
        const listEl = picker.querySelector(".file-order-list");
        listEl.innerHTML = "";
        picker._items.forEach((item, idx) => {
            const li = document.createElement("li");

            li.dataset.kind = kind;
            const name = item.kind === "new" ? item.file.name : item.name;

            const preview = item.restoring ? null : buildPreview(kind, item);
            if (preview) li.appendChild(preview);

            const span = document.createElement("span");
            span.className = "item-name";
            span.textContent = `${idx + 1}. ${name}`;
            span.title = name;
            let badge = null;
            if (item.kind === "existing") {
                badge = document.createElement("span");
                badge.className = "reused-badge";
                badge.textContent = item.restoring ? "Downloading again…" : "reused";
            }
            const up = document.createElement("button");
            up.type = "button";
            up.textContent = "↑";
            up.onclick = () => {
                if (idx === 0) return;
                [picker._items[idx - 1], picker._items[idx]] = [picker._items[idx], picker._items[idx - 1]];
                renderList(picker);
            };
            const down = document.createElement("button");
            down.type = "button";
            down.textContent = "↓";
            down.onclick = () => {
                if (idx >= picker._items.length - 1) return;
                [picker._items[idx], picker._items[idx + 1]] = [picker._items[idx + 1], picker._items[idx]];
                renderList(picker);
            };
            const remove = document.createElement("button");
            remove.type = "button";
            remove.textContent = "✕";
            remove.onclick = () => {
                picker._items.splice(idx, 1);
                renderList(picker);
            };

            li.appendChild(span);
            if (badge) li.appendChild(badge);
            li.append(up, down, remove);
            listEl.appendChild(li);
        });
        checkRequired(picker);

        picker.dispatchEvent(new CustomEvent("filepicker:change", { bubbles: true }));
    }

    const REF_MAX = 60;

    function loadRefBrowser(picker, browser, limit) {
        const kind = picker.dataset.kind;
        const query = limit ? `/refs?kind=${kind}&limit=${limit}` : `/refs?kind=${kind}`;
        browser.textContent = "loading…";
        fetch(query)
            .then((r) => r.json())
            .then((files) => {
                browser.innerHTML = "";
                if (!files.length) {
                    const empty = document.createElement("span");
                    empty.className = "ref-empty";
                    empty.textContent = `No ${kind}s uploaded yet.`;
                    browser.appendChild(empty);
                    return;
                }
                let group = null;
                files.forEach((f) => {

                    const inUsed = f.used_at !== null && f.used_at !== undefined;
                    if (!limit && group !== inUsed) {
                        group = inUsed;
                        const label = document.createElement("span");
                        label.className = "ref-group-label";
                        label.textContent = inUsed
                            ? "Recently used"
                            : "Recently uploaded";
                        browser.appendChild(label);
                    }

                    const isAudio = kind === "audio";
                    const tile = document.createElement(isAudio ? "div" : "button");
                    if (!isAudio) tile.type = "button";
                    tile.className = isAudio ? "ref-tile ref-tile--audio" : "ref-tile";
                    tile.title = f.name;

                    if (kind === "image" || kind === "video") {
                        const img = document.createElement("img");
                        img.src = `/refs/thumb?path=${encodeURIComponent(f.path)}&size=200`;
                        img.loading = "lazy";
                        img.alt = f.name;
                        tile.appendChild(img);
                    } else if (isAudio) {

                        const aud = document.createElement("audio");
                        aud.className = "ref-tile-audio";
                        aud.src = `/refs/file?path=${encodeURIComponent(f.path)}`;
                        aud.controls = true;
                        aud.preload = "none";
                        tile.appendChild(aud);
                    } else {
                        const icon = document.createElement("div");
                        icon.className = "ref-tile-icon";
                        icon.textContent = "🎵";
                        tile.appendChild(icon);
                    }

                    const name = document.createElement(isAudio ? "button" : "span");
                    if (isAudio) name.type = "button";
                    name.className = "ref-tile-name";
                    name.textContent = f.name;
                    tile.appendChild(name);

                    const addTarget = isAudio ? name : tile;
                    addTarget.addEventListener("click", () => {
                        picker._items.push({
                            kind: "existing",
                            path: f.path,
                            name: f.name,
                        });
                        renderList(picker);
                        tile.classList.add("added");
                        setTimeout(() => tile.classList.remove("added"), 600);
                    });
                    browser.appendChild(tile);
                });

                if (!limit) {
                    const more = document.createElement("button");
                    more.type = "button";
                    more.className = "ref-toggle";
                    more.textContent = "Show older";
                    more.addEventListener("click", () => {
                        loadRefBrowser(picker, browser, REF_MAX);
                    });
                    browser.appendChild(more);
                }
            })
            .catch(() => {
                browser.textContent = "Could not load uploaded files.";
            });
    }

    function ensureNamedFile(file, kind) {
        if (file.name) return file;
        const ext = (file.type.split("/")[1] || kind || "png").split("+")[0];
        return new File([file], `pasted-${Date.now()}.${ext}`, { type: file.type });
    }

    document.addEventListener("paste", (e) => {
        const items = e.clipboardData?.items;
        if (!items) return;

        const picker = [...document.querySelectorAll(".file-picker")].find((p) => {
            if (!p._items || !isActive(p)) return false;
            const kind = p.dataset.kind;
            return [...items].some(
                (item) => item.kind === "file" && item.type.startsWith(`${kind}/`)
            );
        });
        if (!picker) return;

        const kind = picker.dataset.kind;
        const matched = [];
        for (const item of items) {
            if (item.kind === "file" && item.type.startsWith(`${kind}/`)) {
                const file = item.getAsFile();
                if (file) matched.push(ensureNamedFile(file, kind));
            }
        }
        if (!matched.length) return;

        e.preventDefault();
        matched.forEach((file) => picker._items.push({ kind: "new", file }));
        renderList(picker);
        reuseStored(picker);
    });

    const HASH_MAX_BYTES = 256 * 1024 * 1024;

    const STORED_MAX = 50;

    async function digestOf(file) {
        const hash = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
        return [...new Uint8Array(hash)].map((b) => b.toString(16).padStart(2, "0")).join("");
    }

    async function reuseStored(picker) {
        if (!window.crypto?.subtle) return;
        const fresh = (picker._items || [])
            .filter((item) => item.kind === "new" && item.file.size <= HASH_MAX_BYTES)
            .slice(0, STORED_MAX);
        if (!fresh.length) return;
        try {
            for (const item of fresh) item.digest ??= await digestOf(item.file);
            const query = fresh.map((item) => `digest=${item.digest}`).join("&");
            const r = await fetch(`/refs/stored?${query}`);
            if (!r.ok) return;
            const paths = new Map((await r.json()).stored.map((s) => [s.digest, s.path]));
            let swapped = false;
            for (const item of fresh) {
                const path = paths.get(item.digest);
                if (!path || item.kind !== "new") continue;
                item.name = item.file.name;
                item.path = path;
                item.kind = "existing";
                item.file = undefined;
                swapped = true;
            }
            if (swapped) renderList(picker);
        } catch {

        }
    }

    document.addEventListener("uploads:done", () => {
        document.querySelectorAll(".file-picker").forEach(reuseStored);
    });

    function initPicker(picker) {
        picker._items = [];

        const addInput = picker.querySelector(".file-add-input");
        addInput.addEventListener("change", () => {
            for (const file of addInput.files) {
                picker._items.push({ kind: "new", file });
            }
            addInput.value = "";
            renderList(picker);
            reuseStored(picker);
        });

        checkRequired(picker);

        const toggle = picker.querySelector(".ref-toggle");
        const browser = picker.querySelector(".ref-browser");
        toggle?.addEventListener("click", () => {
            if (!browser.hidden) {
                browser.hidden = true;
                return;
            }
            browser.hidden = false;
            loadRefBrowser(picker, browser);
        });
    }

    function initPreviewInputs() {
        document.querySelectorAll(".preview-input").forEach((input) => {
            const img = document.getElementById(input.dataset.preview);
            let currentUrl = null;
            input.addEventListener("change", () => {
                if (currentUrl) URL.revokeObjectURL(currentUrl);
                const file = input.files[0];
                if (!file) {
                    img.style.display = "none";
                    return;
                }
                currentUrl = URL.createObjectURL(file);
                img.src = currentUrl;
                img.style.display = "block";
            });
        });
    }

    function collectFormData(formData) {
        document.querySelectorAll(".file-picker").forEach((picker) => {
            if (!picker._items || !isActive(picker)) return;
            const field = picker.dataset.field;
            const manifest = [];
            for (const item of picker._items) {
                if (item.kind === "new") {
                    formData.append(field, item.file, item.file.name);
                    manifest.push({ kind: "new" });
                } else if (!item.restoring) {

                    manifest.push({ kind: "existing", path: item.path });
                }
            }
            formData.append(`${field}_manifest`, JSON.stringify(manifest));
        });
        return formData;
    }

    function clearPicker(picker) {
        if (!picker._items) return;
        picker._items = [];
        renderList(picker);
    }

    function setExisting(field, paths) {
        const pickers = [...document.querySelectorAll(`.file-picker[data-field="${field}"]`)];
        const picker = pickers.find(isActive) || pickers[0];
        if (!picker || !picker._items) return;
        picker._items = paths.map((p) => ({
            kind: "existing",
            path: p,
            name: displayName(p),
        }));
        return dropMissing(picker);
    }

    function setItems(field, saved) {
        const pickers = [...document.querySelectorAll(`.file-picker[data-field="${field}"]`)];
        const picker = pickers.find(isActive) || pickers[0];
        if (!picker || !picker._items) return;
        picker._items = saved.map((item) =>
            item.file
                ? { kind: "new", file: item.file }
                : { kind: "existing", path: item.path, name: displayName(item.path) }
        );
        return dropMissing(picker);
    }

    async function dropMissing(picker) {
        const restored = picker._items.filter((item) => item.kind === "existing");
        let missing = [];
        let restorable = [];
        if (restored.length) {
            const query = restored
                .map((item) => `path=${encodeURIComponent(item.path)}`)
                .join("&");
            try {
                const r = await fetch(`/refs/missing?${query}`);
                if (r.ok) {
                    const body = await r.json();
                    missing = body.missing;
                    restorable = body.restorable || [];
                }
            } catch {
                missing = [];
            }
        }
        const back = new Map(restorable.map((ref) => [ref.path, ref]));
        const gone = restored.filter(
            (item) => missing.includes(item.path) && !back.has(item.path)
        );
        const fetching = restored.filter((item) => back.has(item.path));
        fetching.forEach((item) => {
            item.restoring = true;
        });
        picker._items = picker._items.filter((item) => !gone.includes(item));
        renderList(picker);
        fetching.forEach((item) => restore(picker, item, back.get(item.path)));
        noteMissing(picker, gone);
        return gone.map((item) => item.name);
    }

    function noteMissing(picker, gone, why) {
        if (!gone.length) return;
        const note = document.createElement("p");
        note.className = "generation-error file-picker-missing";
        const names = gone.map((item) => item.name).join(", ");
        note.textContent = why
            ? `${names} could not be downloaded again: ${why}`
            : gone.length === 1
                ? `${names} is not on this server: pick it again`
                : `${gone.length} files are not on this server (${names}): pick them again`;
        picker.querySelector(".file-order-list").after(note);
    }

    const RESTORE_POLL_MS = 2000;

    const NOT_HERE = "Not on this server";

    async function downloaded(jobId) {
        for (;;) {
            await new Promise((resolve) => setTimeout(resolve, RESTORE_POLL_MS));
            let r;
            try {
                r = await fetch(`/download-media/jobs/${encodeURIComponent(jobId)}/status`);
            } catch {
                continue;
            }
            if (r.status === 404) throw new Error("download gone");
            if (!r.ok) continue;
            const job = await r.json();
            if (job.status === "failed") throw new Error(job.error || "download failed");
            if (job.status === "done") return;
        }
    }

    async function restore(picker, item, ref) {
        const job = encodeURIComponent(ref.job_id);
        try {
            const start = await fetch(`/download-media/${job}/replays`, { method: "POST" });
            if (!start.ok) throw new Error(`replay refused (${start.status})`);
            await downloaded(ref.job_id);
            const r = await fetch(`/download-media/${job}/files/${ref.index}/stored`);
            if (!r.ok) {
                const refusal = await r.json().catch(() => ({}));
                const failure = new Error(`not stored (${r.status})`);
                if (refusal.detail !== NOT_HERE) failure.why = refusal.detail;
                throw failure;
            }
            item.path = (await r.json()).path;
            item.name = displayName(item.path);
            item.restoring = false;
            if (picker._items.includes(item)) renderList(picker);
        } catch (err) {
            console.error("could not download it again", err);
            if (!picker._items.includes(item)) return;
            picker._items = picker._items.filter((other) => other !== item);
            renderList(picker);
            noteMissing(picker, [item], typeof err.why === "string" ? err.why : "");
        }
    }

    function carryOver(fromRoot, toRoot) {
        fromRoot.querySelectorAll(".file-picker").forEach((src) => {
            const dst = toRoot.querySelector(`.file-picker[data-kind="${src.dataset.kind}"]`);
            if (!dst || dst === src || !src._items || !dst._items) return;
            dst._items = [...src._items];
            renderList(dst);
        });
    }

    document.addEventListener("DOMContentLoaded", () => {
        document.querySelectorAll(".file-picker").forEach(initPicker);
        initPreviewInputs();
    });

    function items(picker) {
        return (picker._items || [])
            .filter((item) => !item.restoring)
            .map((item) =>
                item.kind === "new"
                    ? { name: item.file.name, file: item.file }
                    : { name: item.name, path: item.path }
            );
    }

    function restoring() {
        return [...document.querySelectorAll(".file-picker")].some(
            (picker) => isActive(picker) && picker._items?.some((item) => item.restoring)
        );
    }

    window.FilePicker = {
        init: initPicker,
        restoring,
        collectFormData,
        clearPicker,
        setExisting,
        setItems,
        carryOver,
        items,
    };
})();
