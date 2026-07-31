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
        // Existing images preview through /refs/thumb (small, cached) --
        // .thumb-small is a 100px box, a full-res original would be pure
        // waste. Freshly-picked files have no server path yet, so those
        // still preview via a local object URL.
        const src =
            item.kind === "new"
                ? URL.createObjectURL(item.file)
                : kind === "image"
                  ? `/refs/thumb?path=${encodeURIComponent(item.path)}&size=150`
                  : `/refs/file?path=${encodeURIComponent(item.path)}`;
        if (kind === "image") {
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

    function renderList(picker) {
        const kind = picker.dataset.kind;
        const listEl = picker.querySelector(".file-order-list");
        listEl.innerHTML = "";
        picker._items.forEach((item, idx) => {
            const li = document.createElement("li");
            // Drives the per-kind row layout in app.css: an <audio> player has
            // a wide intrinsic width, so on an audio row the filename gets its
            // own line instead of being ellipsised down to nothing.
            li.dataset.kind = kind;
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

    // How many tiles "show older" asks for -- must stay <= the server's own
    // MAX_REF_LIMIT (routers/refs.py), which is what actually enforces it.
    // The default open takes no limit at all: the server decides that split
    // (5 last used + 5 last uploaded).
    const REF_MAX = 60;

    // The "reuse an uploaded file" browser: a grid of preview tiles (image
    // thumbs / video first-frames / audio name tiles) instead of a plain
    // filename dropdown. Fetched fresh on every open so files uploaded since
    // page load show up too. Clicking a tile queues it as an "existing" item.
    //
    // Opens on the files you last picked, then the ones you last uploaded
    // (`used_at` marks which is which) -- picking the file you just used is
    // the overwhelmingly common case, and a wall of tiles buried it.
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
                    // One label per group, emitted when the group changes --
                    // the server already returns used-first, so this needs no
                    // sorting of its own.
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

                    // An audio tile carries a real <audio controls>, and that
                    // cannot live inside a <button>: nesting interactive
                    // content in a button is invalid HTML, and the browser
                    // hands the button every click -- press play and you'd
                    // queue the file instead of hearing it. So an audio tile
                    // is a plain div holding the player plus its own add
                    // button; an image/video tile stays one button.
                    const isAudio = kind === "audio";
                    const tile = document.createElement(isAudio ? "div" : "button");
                    if (!isAudio) tile.type = "button";
                    tile.className = isAudio ? "ref-tile ref-tile--audio" : "ref-tile";
                    tile.title = f.name;

                    // Both images and videos poster through /refs/thumb: once
                    // "show older" is used this grid paints up to REF_MAX
                    // tiles at once, and the old <video preload="metadata">
                    // tile made that one range request per clip against
                    // multi-MB originals.
                    if (kind === "image" || kind === "video") {
                        const img = document.createElement("img");
                        img.src = `/refs/thumb?path=${encodeURIComponent(f.path)}&size=200`;
                        img.loading = "lazy";
                        img.alt = f.name;
                        tile.appendChild(img);
                    } else if (isAudio) {
                        // preload="none" for the same reason videos poster
                        // through a thumb: this grid can paint REF_MAX tiles,
                        // and nothing should be fetched until you press play.
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
                    // On an audio tile the name IS the add button (the tile
                    // itself can't be one); everywhere else the whole tile
                    // adds, as before.
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

                // The default view is deliberately short, so there is always
                // "older" to reach for. Reuses .ref-toggle rather than
                // inventing a component -- same affordance, one row down.
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

    // Clipboard files (screenshots, copied images) rarely carry a real
    // filename -- give them one so the server's extension-based classify_kind
    // / save_asset still work like they do for a normal file-input upload.
    function ensureNamedFile(file, kind) {
        if (file.name) return file;
        const ext = (file.type.split("/")[1] || kind || "png").split("+")[0];
        return new File([file], `pasted-${Date.now()}.${ext}`, { type: file.type });
    }

    // Ctrl+V/Cmd+V (same browser "paste" event on both platforms) support for
    // adding a copied/screenshotted image straight into a picker -- same
    // {kind: "new", file} shape and upload path as the file-input, so it's
    // thumbnailed/stored/attached identically. Listens on `document`, no
    // click-to-focus anything first -- Ctrl+V anywhere on the page just
    // works. Routes purely by clipboard CONTENT, not by what happens to be
    // focused: if the clipboard holds no file matching an active picker's
    // kind (e.g. it's plain text), `picker` below stays undefined and the
    // handler returns before preventDefault(), so normal text paste into
    // the prompt textarea/any other field is completely unaffected. This is
    // also why checking the focused element would be wrong -- the prompt
    // textarea is very often focused right when you go to paste a
    // screenshot, and an editable-target check would swallow it there.
    document.addEventListener("paste", (e) => {
        const items = e.clipboardData && e.clipboardData.items;
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

        // Only now that we know this paste is actually image/video/audio
        // data meant for a picker do we take over the event -- anything else
        // (plain text, a paste with no matching-kind files) is left alone.
        e.preventDefault();
        matched.forEach((file) => picker._items.push({ kind: "new", file }));
        renderList(picker);
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
