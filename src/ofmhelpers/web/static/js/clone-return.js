/*
 * /clone's form across a Trim it! (clone-cost.js) -> /edit -> Clone it.
 *
 * `save(form, source)`: before leaving for /edit, everything the form holds
 * -- the subject images and videos (a File not uploaded yet included), the
 * prompt, provider, resolution, audio, export, mirror -- and which video is
 * being trimmed (`source`, its stored path). In IndexedDB, not
 * sessionStorage: only IndexedDB keeps a File.
 *
 * Back on /clone as `?video=<cut>&from=<source>` (/edit's Clone it): the
 * saved form comes back with that video swapped for the cut, then the save
 * is dropped. Only for that very trim (`from` = the saved `source`) and
 * within MAX_AGE_MS: a /clone opened any other way, or long after, starts
 * as it always does. Nothing works without IndexedDB (a private window):
 * the page then just opens with the cut, as before.
 */
(() => {
    const DB = "ofm-clone";
    const STORE = "form";
    const KEY = "trim";
    const MAX_AGE_MS = 60 * 60 * 1000;

    // One stored file, however its path is written ("uploads/assets/x" or
    // "/app/uploads/assets/x"): its name, which starts with its content hash.
    const fileName = (path) => (path || "").split(/[\\/]/).pop();
    const same = (a, b) => Boolean(a) && fileName(a) === fileName(b);

    function open() {
        return new Promise((resolve, reject) => {
            const req = indexedDB.open(DB, 1);
            req.onupgradeneeded = () => req.result.createObjectStore(STORE);
            req.onsuccess = () => resolve(req.result);
            req.onerror = () => reject(req.error);
        });
    }

    async function run(mode, action) {
        const db = await open();
        return new Promise((resolve, reject) => {
            const tx = db.transaction(STORE, mode);
            const req = action(tx.objectStore(STORE));
            tx.oncomplete = () => resolve(req.result);
            tx.onerror = () => reject(tx.error);
        });
    }

    function picker(form, field) {
        return window.FilePicker.items(form.querySelector(`.file-picker[data-field="${field}"]`));
    }

    async function save(form, source) {
        const value = (name) => form.querySelector(`[name="${name}"]`);
        const state = {
            at: Date.now(),
            source,
            images: picker(form, "image"),
            videos: picker(form, "videos"),
            prompt: value("prompt").value,
            provider: value("provider")?.value,
            resolution: value("resolution").value,
            audio: value("audio").checked,
            export: value("export").value,
            mirror: value("mirror").checked,
        };
        try {
            await run("readwrite", (store) => store.put(state, KEY));
        } catch {
            // No IndexedDB: Clone it opens /clone with only the cut.
        }
    }

    function fill(form, state, cut) {
        const value = (name) => form.querySelector(`[name="${name}"]`);
        const provider = value("provider");
        if (provider && state.provider && provider.value !== state.provider
            && provider.querySelector(`option[value="${state.provider}"]`)) {
            provider.value = state.provider;
            provider.dispatchEvent(new Event("change", { bubbles: true }));
        }
        window.FilePicker.setItems("image", state.images);
        window.FilePicker.setItems(
            "videos",
            state.videos.map((item) => (same(item.path, state.source) ? { path: cut } : item))
        );
        const prompt = value("prompt");
        prompt.value = state.prompt;
        prompt.dispatchEvent(new Event("input", { bubbles: true }));
        value("resolution").value = state.resolution;
        value("resolution").dispatchEvent(new Event("change", { bubbles: true }));
        value("audio").checked = state.audio;
        value("audio").dispatchEvent(new Event("change", { bubbles: true }));
        value("export").value = state.export;
        value("mirror").checked = state.mirror;
    }

    async function restore(form) {
        const query = new URLSearchParams(window.location.search);
        const cut = query.get("video");
        const from = query.get("from");
        if (!cut || !from) return;
        let state;
        try {
            state = await run("readonly", (store) => store.get(KEY));
            await run("readwrite", (store) => store.delete(KEY));
        } catch {
            return;
        }
        if (!state || !same(state.source, from) || Date.now() - state.at > MAX_AGE_MS) return;
        fill(form, state, cut);
    }

    document.addEventListener("DOMContentLoaded", () => {
        const form = document.getElementById("clone-form");
        if (form) restore(form);
    });

    window.CloneReturn = { save };
})();
