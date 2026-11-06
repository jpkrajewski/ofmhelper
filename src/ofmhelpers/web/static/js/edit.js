/*
 * /edit page (templates/edit.html).
 *
 * No video yet: picking one in the file picker opens it at once -- a reused
 * file by its stored path, a new one uploaded first (/edit/upload).
 *
 * A video: two range inputs lie over the frame strip, Start and End, never
 * closer than data-min seconds. The arrow keys move a focused one; a press
 * anywhere on the strip takes the nearer one there and drags it (pointer
 * capture: a fast drag never loses it, which the overlaid inputs' own
 * thumbs did). Moving one shows that frame in the player;
 * the parts outside are shaded. Play plays the part from Start and stops
 * at End. The length turns warn-coloured over data-limit (what /clone
 * takes). Cut queues the part on the edit worker (/edit/cut -> a job id)
 * and polls it (/edit/cut/<id>): "In line · N ahead", "Cutting…", then
 * Clone it (/clone with it picked) and Download show next to Cut; moving a
 * handle hides them again (they would be the old part).
 *
 * Messages: a refusal's own sentence (the server words those); anything
 * else (a 5xx, a proxy page, no connection) one fixed sentence.
 */
(() => {
    function clock(seconds) {
        const m = Math.floor(seconds / 60);
        const s = (seconds - m * 60).toFixed(1).padStart(4, "0");
        return `${String(m).padStart(2, "0")}:${s}`;
    }

    function setStatus(message) {
        const status = document.getElementById("edit-status");
        status.replaceChildren();
        if (!message) return;
        const el = document.createElement("div");
        el.className = "generation-error";
        el.textContent = message;
        status.append(el);
    }

    const POLL_MS = 2000;
    const FAILED = "Something went wrong: try again";
    const OFFLINE = "No connection: try again";
    const TOO_BIG = "This video is too big to open here";

    // The answer's JSON, or an Error with a sentence for the page.
    async function answer(request) {
        let response;
        try {
            response = await request;
        } catch {
            throw new Error(OFFLINE);
        }
        const body = await response.json().catch(() => ({}));
        if (response.ok) return body;
        if (response.status === 413) throw new Error(TOO_BIG);
        const worded = response.status === 400 && typeof body.detail === "string";
        throw new Error(worded ? body.detail : FAILED);
    }

    const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

    function openPicked(form) {
        const picker = form.querySelector(".file-picker");
        picker.addEventListener("filepicker:change", async () => {
            const item = window.FilePicker.items(picker)[0];
            if (!item) return;
            const go = (path) => {
                window.location.href = `/edit?video=${encodeURIComponent(path)}`;
            };
            if (item.path) {
                go(item.path);
                return;
            }
            setStatus("");
            const data = new FormData();
            data.append("file", item.file, item.name);
            try {
                go((await answer(fetch("/edit/upload", { method: "POST", body: data }))).path);
            } catch (err) {
                setStatus(err.message);
            }
        });
    }

    function initEditor(root) {
        const seconds = Number(root.dataset.seconds);
        const limit = Number(root.dataset.limit);
        const min = Number(root.dataset.min);
        const player = document.getElementById("edit-player");
        const start = document.getElementById("edit-start");
        const end = document.getElementById("edit-end");
        const timeline = document.getElementById("edit-timeline");
        const play = root.querySelector(".edit-play");
        const pause = root.querySelector(".edit-pause");
        const cutBtn = document.getElementById("edit-cut");
        const length = document.getElementById("edit-length");

        const pct = (t) => `${(t / seconds) * 100}%`;

        function show() {
            const a = Number(start.value);
            const b = Number(end.value);
            timeline.style.setProperty("--start", pct(a));
            timeline.style.setProperty("--end", pct(b));
            document.getElementById("edit-from").textContent = clock(a);
            document.getElementById("edit-to").textContent = clock(b);
            const part = b - a;
            length.textContent = `${part.toFixed(1)} s`;
            length.classList.toggle("edit-length-over", part > limit + 0.05);
        }

        // The last cut's Clone it / Download, or none.
        function done(made) {
            const clone = document.getElementById("edit-clone");
            const download = document.getElementById("edit-download");
            clone.hidden = !made;
            download.hidden = !made;
            if (!made) return;
            // `from`: the video this cut replaces, so /clone puts back the
            // form Trim it! left (clone-return.js).
            clone.href =
                `/clone?video=${encodeURIComponent(made.path)}` +
                `&from=${encodeURIComponent(root.dataset.video)}`;
            download.href = made.url;
            download.setAttribute("download", made.name);
        }

        function playing(on) {
            play.hidden = on;
            pause.hidden = !on;
        }

        // A handle never passes the other; the frame under it shows.
        function moved(handle) {
            done(null);
            if (Number(end.value) - Number(start.value) < min) {
                if (handle === start) start.value = String(Number(end.value) - min);
                else end.value = String(Number(start.value) + min);
            }
            player.pause();
            player.currentTime = Number(handle.value);
            show();
        }

        start.addEventListener("input", () => moved(start));
        end.addEventListener("input", () => moved(end));

        function timeAt(x) {
            const box = timeline.getBoundingClientRect();
            return Math.min(Math.max(((x - box.left) / box.width) * seconds, 0), seconds);
        }

        timeline.addEventListener("pointerdown", (e) => {
            e.preventDefault();
            const t = timeAt(e.clientX);
            const handle =
                Math.abs(t - Number(start.value)) <= Math.abs(t - Number(end.value)) ? start : end;
            handle.focus();
            const drag = (ev) => {
                handle.value = String(timeAt(ev.clientX));
                moved(handle);
            };
            const stop = () => {
                timeline.removeEventListener("pointermove", drag);
                timeline.removeEventListener("pointerup", stop);
                timeline.removeEventListener("pointercancel", stop);
            };
            timeline.setPointerCapture(e.pointerId);
            timeline.addEventListener("pointermove", drag);
            timeline.addEventListener("pointerup", stop);
            timeline.addEventListener("pointercancel", stop);
            drag(e);
        });

        play.addEventListener("click", () => {
            const t = player.currentTime;
            if (t < Number(start.value) || t >= Number(end.value) - 0.05) {
                player.currentTime = Number(start.value);
            }
            player.play();
        });
        pause.addEventListener("click", () => player.pause());
        player.addEventListener("click", () => (player.paused ? play.click() : player.pause()));
        player.addEventListener("play", () => playing(true));
        player.addEventListener("pause", () => playing(false));
        player.addEventListener("timeupdate", () => {
            timeline.style.setProperty("--at", pct(player.currentTime));
            if (!player.paused && player.currentTime >= Number(end.value)) player.pause();
        });

        cutBtn.addEventListener("click", async () => {
            player.pause();
            cutBtn.disabled = true;
            const label = cutBtn.lastChild;
            const was = label.textContent;
            label.textContent = " Cutting…";
            setStatus("");
            try {
                const queued = await answer(
                    fetch("/edit/cut", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                            video: root.dataset.video,
                            start: Number(start.value),
                            end: Number(end.value),
                        }),
                    })
                );
                // Polled until it is done or failed; a lost poll is tried again.
                for (;;) {
                    await wait(POLL_MS);
                    const state = await answer(fetch(`/edit/cut/${queued.job_id}`)).catch(() => null);
                    if (!state) continue;
                    if (state.status === "done") {
                        done(state.video);
                        break;
                    }
                    if (state.status === "failed") throw new Error(state.error || FAILED);
                    label.textContent = state.ahead ? ` In line · ${state.ahead} ahead` : " Cutting…";
                }
            } catch (err) {
                setStatus(err.message);
            } finally {
                cutBtn.disabled = false;
                label.textContent = was;
            }
        });

        // The strip is made on the first open: no strip, no shading over air.
        timeline.querySelector(".edit-strip").addEventListener("error", () => {
            timeline.classList.add("edit-timeline-bare");
        });
        player.currentTime = 0;
        show();
    }

    document.addEventListener("DOMContentLoaded", () => {
        const root = document.getElementById("edit");
        if (root) initEditor(root);
        const form = document.getElementById("edit-open");
        if (form) openPicked(form);
    });
})();
