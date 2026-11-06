/*
 * /clone's one-click picks and the Start dialog.
 *
 * Tiles (.subject-tile): the person's last used subjects, then their most
 * used; a click makes that picture the subject (the Subject list becomes
 * just it). A tile is pressed while its picture (same content, any stored
 * name) is in the list, however it got there.
 *
 * Add-on chips (.addon-chip, data-text + data-pattern from
 * features/clone/addons.json): a click adds ", <text>" to the end of the
 * prompt, a click on a pressed one takes out every part of the prompt (the
 * pieces between commas / line breaks, never the first) asking for it. A
 * chip is pressed while a piece after the first asks for it in any wording
 * (the pattern: "NO TATTOS" counts): the first is the replace sentence,
 * which a click never takes out, so a chip it matched could not be turned
 * off. The dialog holds the same chips, kept in step.
 *
 * Start dialog (#clone-confirm): on the left the subject images, the prompt
 * as it will be sent, the add-ons re-runs most often had to add and audio;
 * on the right the videos, playable; Start or Cancel.
 * `window.ClonePicks.confirm(form)` resolves true on Start.
 */
(() => {
    // A separator kept by split(): the pieces are at even indexes.
    const PIECES = /(\s*,\s*|\s*\r?\n\s*)/;
    // Presses every chip as the prompt and the audio switch stand (Recreate
    // sets the switch without a change event).
    let syncChips = () => {};

    function asks(prompt, chip) {
        const pattern = new RegExp(chip.dataset.pattern, "i");
        return prompt.split(PIECES).some((piece, i) => i > 1 && i % 2 === 0 && pattern.test(piece));
    }

    function withAddon(prompt, chip) {
        const base = prompt.replace(/[\s,]+$/, "");
        return base ? `${base}, ${chip.dataset.text}` : chip.dataset.text;
    }

    function withoutAddon(prompt, chip) {
        const pattern = new RegExp(chip.dataset.pattern, "i");
        const parts = prompt.split(PIECES);
        const kept = [parts[0]];
        for (let i = 1; i < parts.length; i += 2) {
            if (!pattern.test(parts[i + 1])) kept.push(parts[i], parts[i + 1]);
        }
        return kept.join("");
    }

    function initChips(form) {
        const prompt = form.querySelector('[name="prompt"]');
        const audio = form.querySelector('[name="audio"]');
        const chips = () => document.querySelectorAll(".addon-chip");
        const sync = () => {
            chips().forEach((chip) => {
                chip.setAttribute("aria-pressed", String(asks(prompt.value, chip)));
            });
            document.querySelectorAll(".audio-chip").forEach((chip) => {
                chip.setAttribute("aria-pressed", String(audio.checked));
            });
        };
        document.addEventListener("click", (e) => {
            const chip = e.target.closest(".addon-chip");
            if (chip) {
                prompt.value = asks(prompt.value, chip)
                    ? withoutAddon(prompt.value, chip)
                    : withAddon(prompt.value, chip);
                prompt.dispatchEvent(new Event("input", { bubbles: true }));
                return;
            }
            if (e.target.closest(".audio-chip")) {
                audio.checked = !audio.checked;
                audio.dispatchEvent(new Event("change", { bubbles: true }));
            }
        });
        prompt.addEventListener("input", sync);
        audio.addEventListener("change", sync);
        sync();
        syncChips = sync;
    }

    // "uploads/assets/<sha256>__<name>" -> the sha256: one picture, whatever
    // name or path it was stored under.
    function digest(path) {
        return (path || "").split(/[\\/]/).pop().split("__")[0];
    }

    function initTiles(form) {
        const picker = form.querySelector('.file-picker[data-field="image"]');
        const tiles = () => picker.querySelectorAll(".subject-tile");
        picker.addEventListener("click", (e) => {
            const tile = e.target.closest(".subject-tile");
            if (tile) window.FilePicker.setExisting("image", [tile.dataset.path]);
        });
        picker.addEventListener("filepicker:change", () => {
            const picked = new Set(
                window.FilePicker.items(picker).filter((item) => item.path).map((item) => digest(item.path))
            );
            tiles().forEach((tile) => {
                tile.setAttribute("aria-pressed", String(picked.has(digest(tile.dataset.path))));
            });
        });
    }

    function subjectImage(item) {
        const img = document.createElement("img");
        img.alt = "";
        img.src = item.file
            ? URL.createObjectURL(item.file)
            : `/refs/thumb?path=${encodeURIComponent(item.path)}&size=400`;
        return img;
    }

    // A picked video to play: a stored one from the server (its first frame
    // as the poster), a new one from the computer.
    function videoPlayer(item) {
        const video = document.createElement("video");
        video.controls = true;
        video.playsInline = true;
        video.preload = "metadata";
        if (item.path) {
            video.poster = `/refs/thumb?path=${encodeURIComponent(item.path)}&size=400`;
            video.src = `/refs/file?path=${encodeURIComponent(item.path)}`;
        } else {
            video.src = `${URL.createObjectURL(item.file)}#t=0.1`;
        }
        return video;
    }

    function confirm(form) {
        const dialog = document.getElementById("clone-confirm");
        const prompt = form.querySelector('[name="prompt"]');
        const shown = document.getElementById("clone-confirm-prompt");
        const images = window.FilePicker.items(form.querySelector('.file-picker[data-field="image"]'));
        const videoItems = window.FilePicker.items(form.querySelector('.file-picker[data-field="videos"]'));
        const videos = videoItems.length;
        const thumbs = [...images.map(subjectImage), ...videoItems.map(videoPlayer)];
        document.getElementById("clone-confirm-images").replaceChildren(...thumbs.slice(0, images.length));
        document.getElementById("clone-confirm-videos").replaceChildren(...thumbs.slice(images.length));
        document.getElementById("clone-confirm-title").textContent =
            `Start ${videos} clone${videos === 1 ? "" : "s"}?`;
        // The cost panel's total, when it has one.
        const cost = document.getElementById("clone-cost");
        const usd = document.getElementById("clone-cost-usd").textContent.trim();
        document.getElementById("clone-confirm-go").textContent =
            !cost.hidden && usd ? `Start · ${usd}` : "Start";
        // References marked as in the prompt box (prompt-highlight.js).
        const showPrompt = () => {
            shown.innerHTML = window.PromptHighlight.html(prompt.value);
        };
        showPrompt();
        syncChips();
        prompt.addEventListener("input", showPrompt);
        dialog.returnValue = "";
        dialog.showModal();
        return new Promise((resolve) => {
            dialog.addEventListener(
                "close",
                () => {
                    prompt.removeEventListener("input", showPrompt);
                    // A video still playing stops; local files' URLs freed.
                    thumbs.forEach((el) => {
                        el.pause?.();
                        if (el.src.startsWith("blob:")) URL.revokeObjectURL(el.src.split("#")[0]);
                        el.removeAttribute("src");
                    });
                    resolve(dialog.returnValue === "confirm");
                },
                { once: true }
            );
        });
    }

    document.addEventListener("DOMContentLoaded", () => {
        const form = document.getElementById("clone-form");
        initChips(form);
        initTiles(form);
    });

    window.ClonePicks = { confirm };
})();
