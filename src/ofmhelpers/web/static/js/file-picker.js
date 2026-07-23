/*
 * Ordered, reusable multi-file picker widget.
 *
 * Auto-discovers every `.file-picker[data-field][data-kind]` element on the
 * page -- no per-template JS needed. Each item added is either
 * {kind: 'new', file: File} (from the file input) or
 * {kind: 'existing', path, name} (reused from the `/refs` dropdown or
 * restored by click-to-reuse -- never re-uploaded, since it already lives
 * on the server. That's what prevents duplicate uploads).
 *
 * State lives on each picker ELEMENT (picker._items), not in a shared map
 * keyed by field name -- two tools' fieldsets may legitimately use the same
 * field names (seedance and fake_ai both have reference_images/videos/audio)
 * and must never share or clobber each other's queued files.
 *
 * Browsers sort multi-file pickers alphabetically, not by click order, so
 * files are added one at a time into a list this script fully controls,
 * with explicit up/down/remove. Every item gets an inline preview
 * (image thumb / video / audio player).
 *
 * `FilePicker.collectFormData(formData)` appends every ACTIVE picker's files
 * (new ones by bytes, existing ones as a `${field}_manifest` JSON list) to
 * the given FormData -- pickers inside a disabled fieldset (the tools not
 * currently selected on /generate) are skipped, matching how native form
 * submission treats disabled fieldsets.
 *
 * Also handles the simpler single-slot `.preview-input` fields: just an
 * object-URL preview swap, submitted as a plain file input.
 */
(function () {
    // "uploads/assets/{sha256}__{original}.png" -> "{original}.png" -- the
    // hash prefix is a storage detail, not something to show the user.
    function displayName(path) {
        const base = path.split(/[\\/]/).pop();
        const sep = base.indexOf("__");
        return sep > 0 ? base.slice(sep + 2) : base;
    }

    function isActive(picker) {
        return !picker.closest("fieldset:disabled");
    }

    function buildPreview(kind, item) {
        const src =
            item.kind === "new"
                ? URL.createObjectURL(item.file)
                : `/refs/file?path=${encodeURIComponent(item.path)}`;
        if (kind === "image") {
            const thumb = document.createElement("img");
            thumb.className = "thumb-small";
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

    function renderList(picker) {
        const kind = picker.dataset.kind;
        const listEl = picker.querySelector(".file-order-list");
        listEl.innerHTML = "";
        picker._items.forEach((item, idx) => {
            const li = document.createElement("li");
            const name = item.kind === "new" ? item.file.name : item.name;

            const preview = buildPreview(kind, item);
            if (preview) li.appendChild(preview);

            // Name truncates with an ellipsis (long filenames must never
            // stretch the layout); the full name lives in the tooltip, and
            // "reused" is a separate badge so truncation can't swallow it.
            const span = document.createElement("span");
            span.className = "item-name";
            span.textContent = `${idx + 1}. ${name}`;
            span.title = name;
            let badge = null;
            if (item.kind === "existing") {
                badge = document.createElement("span");
                badge.className = "reused-badge";
                badge.textContent = "reused";
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
    }

    // The "reuse an uploaded file" browser: a grid of preview tiles (image
    // thumbs / video first-frames / audio name tiles) instead of a plain
    // filename dropdown. Fetched fresh on every open so files uploaded since
    // page load show up too. Clicking a tile queues it as an "existing" item.
    function loadRefBrowser(picker, browser) {
        const kind = picker.dataset.kind;
        browser.textContent = "loading…";
        fetch(`/refs?kind=${kind}`)
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
                files.forEach((f) => {
                    const tile = document.createElement("button");
                    tile.type = "button";
                    tile.className = "ref-tile";
                    tile.title = f.name;

                    const src = `/refs/file?path=${encodeURIComponent(f.path)}`;
                    if (kind === "image") {
                        const img = document.createElement("img");
                        img.src = src;
                        img.alt = f.name;
                        tile.appendChild(img);
                    } else if (kind === "video") {
                        const vid = document.createElement("video");
                        vid.src = src;
                        vid.muted = true;
                        vid.preload = "metadata";
                        tile.appendChild(vid);
                    } else {
                        const icon = document.createElement("div");
                        icon.className = "ref-tile-icon";
                        icon.textContent = "🎵";
                        tile.appendChild(icon);
                    }
                    const name = document.createElement("span");
                    name.className = "ref-tile-name";
                    name.textContent = f.name;
                    tile.appendChild(name);

                    tile.addEventListener("click", () => {
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
            })
            .catch(() => {
                browser.textContent = "Could not load uploaded files.";
            });
    }

    function initPicker(picker) {
        picker._items = [];

        const addInput = picker.querySelector(".file-add-input");
        addInput.addEventListener("change", () => {
            for (const file of addInput.files) {
                picker._items.push({ kind: "new", file });
            }
            addInput.value = "";
            renderList(picker);
        });

        const toggle = picker.querySelector(".ref-toggle");
        const browser = picker.querySelector(".ref-browser");
        toggle.addEventListener("click", () => {
            if (browser.style.display !== "none") {
                browser.style.display = "none";
                return;
            }
            browser.style.display = "";
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
                } else {
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

    // Replace a field's list with server-side paths (already in the shared
    // asset store) -- what /generate's click-to-reuse calls to restore a past
    // job's reference files, previews included. Targets the picker in the
    // currently-enabled fieldset when field names are shared across tools.
    function setExisting(field, paths) {
        const pickers = [...document.querySelectorAll(`.file-picker[data-field="${field}"]`)];
        const picker = pickers.find(isActive) || pickers[0];
        if (!picker || !picker._items) return;
        picker._items = paths.map((p) => ({
            kind: "existing",
            path: p,
            name: displayName(p),
        }));
        renderList(picker);
    }

    document.addEventListener("DOMContentLoaded", () => {
        document.querySelectorAll(".file-picker").forEach(initPicker);
        initPreviewInputs();
    });

    window.FilePicker = { collectFormData, clearPicker, setExisting };
})();
