/*
 * /edit page (templates/edit.html).
 *
 * No video yet: picking one in the file picker opens it at once -- a reused
 * file by its stored path, a new one uploaded first (/edit/upload, its
 * progress on the status line; leaving mid-upload asks first:
 * upload-guard.js).
 *
 * A video: the player plays the original until the edit worker made the
 * light copy and the strip (/edit/prepared, polled while data-ready is
 * false), then the copy -- same frames at the same times, a keyframe every
 * few frames, fetched whole once: a seek is a short local decode, not a
 * round trip to the server (dev, 250 ms / 8 Mbps: one frame shown in a 5 s
 * drag of the original). Seeks never pile up: while one runs only the last
 * position asked waits.
 *
 * The strip: a press moves the marker there and shows that frame (a drag
 * scrubs); a press on a handle's grip drags that handle instead. Start and
 * End are never closer than data-min seconds; a focused one moves a frame
 * with the arrow keys, ‹ › and the arrow keys elsewhere step the marker a
 * frame (Shift + arrow: a second). Times show as editors' timecode,
 * MM:SS:FF (FF = the frame within its second), so a step always moves it by
 * one. Every time lands on a frame's start: the copy's own frame times
 * (/edit/frames, with the copy) -- a phone video's frames are not evenly
 * spaced, and a step of 1/fps stood still or skipped one; until they are in,
 * a 1/fps grid (data-fps). A copy that will not load (swept) gives way to
 * the original again. The time shown is the frame shown
 * (requestVideoFrameCallback's mediaTime).
 *
 * Turn: Rotate turns the picture 90° clockwise a click, Mirror flips it
 * left to right; the player shows it so (the stage takes the turned shape)
 * and the crop box is drawn on the turned picture, as the cut and the frame
 * make it (the server turns first, then crops). A turn starts the crop box
 * over.
 * Crop: the box over the player (Original: the whole picture) in the
 * picked shape; drag it to move it, drag a corner to make it smaller or
 * bigger (shape kept, never past the picture, at least 10%). Speed: Play plays the part at that speed; the length shown
 * is the cut's (part / speed), warn-coloured over data-limit (what /clone
 * takes). Size: the cut's and the frame's short side (up or down); the
 * size it comes out at shows next to the length. Frame downloads the frame
 * shown, cropped, at that size (/edit/frame). Cut queues the part, cropped and at that speed, on the edit
 * worker (/edit/cut -> a job id) and polls it (/edit/cut/<id>): "In line ·
 * N ahead", "Cutting…", then Clone it (/clone with it picked) and Download
 * show next to Cut; changing the part, the crop, the speed or the size
 * hides them again (they would be the old cut).
 *
 * Messages: a refusal's own sentence (the server words those); anything
 * else (a 5xx, a proxy page, no connection) one fixed sentence.
 */
(() => {
    const POLL_MS = 2000;
    // The light copy is ready seconds after the page opens: asked sooner.
    const COPY_POLL_MS = 1000;
    const GRIP_PX = 12;
    // The smallest crop box, of the biggest one (options.py `Framing`).
    const MIN_SCALE = 0.1;
    // Seek a hair past a frame's start (its timestamp may round up) and ask
    // the server a hair before it, so both land on the frame shown.
    const INTO_FRAME_S = 0.0005;
    const BEFORE_FRAME_S = 0.001;
    const FAILED = "Something went wrong: try again";
    const OFFLINE = "No connection: try again";
    const TOO_BIG = "This video is too big to open here";

    // A refusal / failure (red), or a plain line ("Uploading… 42%").
    function setStatus(message, plain = false) {
        const status = document.getElementById("edit-status");
        status.replaceChildren();
        if (!message) return;
        const el = document.createElement("div");
        if (!plain) el.className = "generation-error";
        el.textContent = message;
        status.append(el);
    }

    function failure(status, body) {
        if (status === 413) return new Error(TOO_BIG);
        const worded = status === 400 && typeof body?.detail === "string";
        return new Error(worded ? body.detail : FAILED);
    }

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
        throw failure(response.status, body);
    }

    function parseJson(text) {
        try {
            return JSON.parse(text);
        } catch {
            return null;
        }
    }

    const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);

    async function uploadVideo(item) {
        const data = new FormData();
        data.append("file", item.file, item.name);
        setStatus("Uploading… 0%", true);
        const xhr = await window.UploadGuard.post("/edit/upload", data, (pct) =>
            setStatus(pct < 100 ? `Uploading… ${pct}%` : "Opening…", true)
        );
        const body = parseJson(xhr.responseText);
        if (xhr.status === 401 && body?.login_url) {
            window.UploadGuard.sessionGone();
            window.SessionExpiry.expire(body.login_url);
            return null;
        }
        if (xhr.status >= 200 && xhr.status < 300 && body?.path) return body.path;
        throw failure(xhr.status, body);
    }

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
            try {
                const path = await uploadVideo(item);
                if (path) go(path);
            } catch (err) {
                setStatus(err.message);
            }
        });
    }

    // A download of a fetched file under the name the server gave it.
    async function saveFile(url) {
        let response;
        try {
            response = await fetch(url);
        } catch {
            throw new Error(OFFLINE);
        }
        if (!response.ok) {
            throw failure(response.status, await response.json().catch(() => ({})));
        }
        const named = /filename\*=UTF-8''([^;]+)/.exec(
            response.headers.get("content-disposition") || ""
        );
        const link = document.createElement("a");
        link.href = URL.createObjectURL(await response.blob());
        link.download = named ? decodeURIComponent(named[1]) : "frame.jpg";
        document.body.append(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(link.href), 10000);
    }

    // A button's label while it works ("Saving…"), back when done.
    async function busy(button, label, work) {
        const text = button.lastChild;
        const was = text.textContent;
        button.disabled = true;
        text.textContent = label;
        try {
            await work((next) => {
                text.textContent = next;
            });
        } finally {
            button.disabled = false;
            text.textContent = was;
        }
    }

    function initEditor(root) {
        const seconds = Number(root.dataset.seconds);
        const limit = Number(root.dataset.limit);
        const min = Number(root.dataset.min);
        const frameS = 1 / (Number(root.dataset.fps) || 30);
        const video = root.dataset.video;
        const player = document.getElementById("edit-player");
        const stage = document.getElementById("edit-stage");
        const crop = document.getElementById("edit-crop");
        const start = document.getElementById("edit-start");
        const end = document.getElementById("edit-end");
        const timeline = document.getElementById("edit-timeline");
        const strip = document.getElementById("edit-strip");
        const play = root.querySelector(".edit-play");
        const pause = root.querySelector(".edit-pause");
        const cutBtn = document.getElementById("edit-cut");
        const frameBtn = document.getElementById("edit-frame");
        const length = document.getElementById("edit-length");
        const shape = { turn: 0, mirror: false, aspect: "original", scale: 1, x: 0.5, y: 0.5 };
        let speed = 1;
        let size = root.querySelector('.edit-size-pick[aria-pressed="true"]').dataset.size;
        // The frame on screen, and where the marker was last sent.
        let shownAt = 0;
        let markAt = 0;
        // Every frame's start time, once the light copy is in.
        let frames = null;

        const a = () => Number(start.value);
        const b = () => Number(end.value);
        const pct = (t) => `${(t / seconds) * 100}%`;

        // The last frame starting at or before t.
        function frameIndex(t) {
            let lo = 0;
            let hi = frames.length - 1;
            while (lo < hi) {
                const mid = (lo + hi + 1) >> 1;
                if (frames[mid] <= t + 1e-4) lo = mid;
                else hi = mid - 1;
            }
            return lo;
        }
        // The start of the frame at t.
        function snap(t) {
            const at = clamp(t, 0, seconds);
            return frames ? frames[frameIndex(at)] : Math.floor(at / frameS + 1e-6) * frameS;
        }
        // n frames on from the frame at t.
        function stepFrom(t, n) {
            if (!frames) return snap(snap(t) + n * frameS + 1e-6);
            return frames[clamp(frameIndex(t) + n, 0, frames.length - 1)];
        }
        // The first frame start at or after t.
        function atLeast(t) {
            const s = snap(t);
            return s < t - 1e-6 ? stepFrom(s, 1) : s;
        }
        // MM:SS:FF, FF = the frame within its second (0 = the first frame
        // starting in that second): one frame step moves it by exactly one.
        function timecode(t) {
            const at = snap(t);
            const sec = Math.floor(at + 1e-6);
            let ff = Math.round((at - sec) / frameS);
            if (frames) {
                const j = frameIndex(sec - 2e-4);
                const first = frames[j] < sec - 1e-4 ? j + 1 : j;
                ff = frameIndex(at) - first;
            }
            const two = (n) => String(n).padStart(2, "0");
            return `${two(Math.floor(sec / 60))}:${two(sec % 60)}:${two(ff)}`;
        }

        // --- seeking: one at a time, only the last one asked waits ---
        let wanted = null;
        function apply() {
            if (wanted === null || player.seeking) return;
            player.currentTime = wanted;
            wanted = null;
        }
        function seek(t) {
            const at = snap(t);
            markAt = at;
            timeline.style.setProperty("--at", pct(at));
            wanted = Math.min(at + INTO_FRAME_S, seconds);
            apply();
        }
        player.addEventListener("seeked", apply);

        // The frame on screen and its time (rVFC: exact; else currentTime).
        function showAt(t) {
            shownAt = t;
            const at = frames ? snap(t) : t;
            document.getElementById("edit-at").textContent = timecode(at);
            if (wanted === null && !player.seeking) {
                markAt = at;
                timeline.style.setProperty("--at", pct(at));
            }
        }
        const exactFrames = "requestVideoFrameCallback" in player;
        let frameCallback = 0;
        const onFrame = (_now, meta) => {
            showAt(meta.mediaTime);
            frameCallback = player.requestVideoFrameCallback(onFrame);
        };
        // Armed again after a new src: one loop, never two.
        function watchFrames() {
            if (!exactFrames) return;
            player.cancelVideoFrameCallback(frameCallback);
            frameCallback = player.requestVideoFrameCallback(onFrame);
        }
        if (exactFrames) watchFrames();
        else {
            for (const ev of ["seeked", "timeupdate"]) {
                player.addEventListener(ev, () => showAt(player.currentTime));
            }
        }

        // The cut's / frame's width x height: the crop of the picture, its
        // short side `size` (ffmpeg: media/trim.py scaled_to).
        // The picture as shown: turned 90 or 270, its sides swap.
        function shown() {
            const sideways = shape.turn % 180 !== 0;
            const w = player.videoWidth;
            const h = player.videoHeight;
            return sideways ? [h, w] : [w, h];
        }

        function outSize() {
            if (!player.videoWidth) return "";
            const box = cropBox();
            const [sw, sh] = shown();
            const w = box.w * sw;
            const h = box.h * sh;
            const side = Number(size);
            const even = (v) => Math.round(v / 2) * 2;
            return w > h ? `${even((side * w) / h)}×${side}` : `${side}×${even((side * h) / w)}`;
        }

        function show() {
            timeline.style.setProperty("--start", pct(a()));
            timeline.style.setProperty("--end", pct(b()));
            document.getElementById("edit-from").textContent = timecode(a());
            document.getElementById("edit-to").textContent = timecode(b());
            const made = (b() - a()) / speed;
            length.textContent = `${made.toFixed(1)} s`;
            length.classList.toggle("edit-length-over", made > limit + 0.05);
            document.getElementById("edit-size").textContent = outSize();
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
                `&from=${encodeURIComponent(video)}`;
            download.href = made.url;
            download.setAttribute("download", made.name);
        }

        // A handle never passes the other; the frame under it shows.
        function moved(handle) {
            done(null);
            if (b() - a() < min) {
                if (handle === start) start.value = String(snap(b() - min));
                else end.value = String(atLeast(a() + min));
            }
            player.pause();
            seek(Number(handle.value));
            show();
        }
        for (const handle of [start, end]) {
            handle.addEventListener("input", () => {
                handle.value = String(snap(Number(handle.value)));
                moved(handle);
            });
            // A frame a press, through the frame times (the range input's own
            // step is an even grid).
            handle.addEventListener("keydown", (e) => {
                if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
                e.preventDefault();
                const back = e.key === "ArrowLeft" ? -1 : 1;
                const t = Number(handle.value);
                handle.value = String(e.shiftKey ? snap(t + back) : stepFrom(t, back));
                moved(handle);
            });
        }

        function timeAt(x) {
            const box = timeline.getBoundingClientRect();
            return clamp(((x - box.left) / box.width) * seconds, 0, seconds);
        }

        // The handle whose grip is under x, else null (the marker's press).
        function gripAt(x) {
            const box = timeline.getBoundingClientRect();
            const off = (t) => Math.abs(x - (box.left + (t / seconds) * box.width));
            const toStart = off(a());
            const toEnd = off(b());
            if (Math.min(toStart, toEnd) > GRIP_PX) return null;
            return toStart <= toEnd ? start : end;
        }

        // Pointer capture: a fast drag never loses it.
        function dragOn(el, e, move) {
            el.setPointerCapture(e.pointerId);
            const stop = () => {
                el.removeEventListener("pointermove", move);
                el.removeEventListener("pointerup", stop);
                el.removeEventListener("pointercancel", stop);
            };
            el.addEventListener("pointermove", move);
            el.addEventListener("pointerup", stop);
            el.addEventListener("pointercancel", stop);
            move(e);
        }

        timeline.addEventListener("pointerdown", (e) => {
            e.preventDefault();
            const handle = gripAt(e.clientX);
            player.pause();
            if (handle) {
                handle.focus();
                dragOn(timeline, e, (ev) => {
                    handle.value = String(snap(timeAt(ev.clientX)));
                    moved(handle);
                });
                return;
            }
            dragOn(timeline, e, (ev) => seek(timeAt(ev.clientX)));
        });

        // n frames, or n seconds.
        function step(n, seconds = false) {
            player.pause();
            seek(seconds ? snap(markAt + n) : stepFrom(markAt, n));
        }
        root.querySelector(".edit-back").addEventListener("click", () => step(-1));
        root.querySelector(".edit-next").addEventListener("click", () => step(1));
        // A focused handle steps itself (its range input).
        document.addEventListener("keydown", (e) => {
            if (e.target.closest?.("input, textarea, select, dialog[open]")) return;
            if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
            e.preventDefault();
            step(e.key === "ArrowLeft" ? -1 : 1, e.shiftKey);
        });

        // --- crop: the box over the player, in fractions of the picture ---
        // The biggest box of the shape in the picture (Original: all of it).
        function maxBox() {
            if (shape.aspect === "original" || !player.videoWidth) return [1, 1];
            const [w, h] = shape.aspect.split(":").map(Number);
            const [sw, sh] = shown();
            const r = w / h / (sw / sh);
            return [Math.min(1, r), Math.min(1, 1 / r)];
        }
        // The box: `scale` of the biggest, at x / y in the room it has.
        function cropBox() {
            const [mw, mh] = maxBox();
            const w = mw * shape.scale;
            const h = mh * shape.scale;
            return { w, h, left: (1 - w) * shape.x, top: (1 - h) * shape.y };
        }
        // Where a box of w x h at left / top sits in its room (0..1).
        function placeAt(box, left, top) {
            shape.x = box.w < 1 - 1e-9 ? clamp(left / (1 - box.w), 0, 1) : 0.5;
            shape.y = box.h < 1 - 1e-9 ? clamp(top / (1 - box.h), 0, 1) : 0.5;
        }
        function placeCrop() {
            crop.hidden = !player.videoWidth;
            if (crop.hidden) return;
            const box = cropBox();
            crop.style.setProperty("--crop-w", `${box.w * 100}%`);
            crop.style.setProperty("--crop-h", `${box.h * 100}%`);
            crop.style.setProperty("--crop-x", `${box.left * 100}%`);
            crop.style.setProperty("--crop-y", `${box.top * 100}%`);
        }
        function cropChanged() {
            done(null);
            placeCrop();
            show();
        }
        // A drag's step: only a box that really moved or grew is a change
        // (a click to play must not hide the last cut's Clone it / Download).
        function cropMoved(before) {
            if (before.x === shape.x && before.y === shape.y && before.scale === shape.scale) return;
            cropChanged();
        }
        // The stage: the turned picture as big as fits (the card's width,
        // 60% of the window's height); the player's own box, unturned, in
        // its middle (pages.css turns and mirrors it).
        function layout() {
            if (!player.videoWidth) return;
            const [sw, sh] = shown();
            const fit = Math.min(root.clientWidth / sw, (window.innerHeight * 0.6) / sh);
            const w = sw * fit;
            const h = sh * fit;
            const sideways = shape.turn % 180 !== 0;
            stage.style.setProperty("--stage-w", `${w}px`);
            stage.style.setProperty("--stage-h", `${h}px`);
            player.style.setProperty("--video-w", `${sideways ? h : w}px`);
            player.style.setProperty("--video-h", `${sideways ? w : h}px`);
            player.style.setProperty("--turn", `${shape.turn}deg`);
            player.style.setProperty("--flip", shape.mirror ? "-1" : "1");
        }
        new ResizeObserver(layout).observe(root);
        function metadataIn() {
            layout();
            placeCrop();
            show();
        }
        player.addEventListener("loadedmetadata", metadataIn);
        // A copy already made can be in before this script runs (no event
        // left to wait for: the stage stayed unsized, the crop box hidden).
        if (player.readyState >= HTMLMediaElement.HAVE_METADATA) metadataIn();

        // Inside: move. A corner: that corner follows the pointer, the
        // opposite one stays, the shape is kept, never past the picture.
        crop.addEventListener("pointerdown", (e) => {
            e.preventDefault();
            const area = stage.getBoundingClientRect();
            const from = cropBox();
            const corner = e.target.closest(".edit-crop-grip")?.dataset.corner;
            if (!corner) {
                // A click without a drag plays / pauses (the box covers the
                // whole player).
                crop.addEventListener(
                    "pointerup",
                    (up) => {
                        if (Math.hypot(up.clientX - e.clientX, up.clientY - e.clientY) < 4) {
                            if (player.paused) play.click();
                            else player.pause();
                        }
                    },
                    { once: true }
                );
                dragOn(crop, e, (ev) => {
                    const before = { ...shape };
                    const left = from.left + (ev.clientX - e.clientX) / area.width;
                    const top = from.top + (ev.clientY - e.clientY) / area.height;
                    placeAt(from, clamp(left, 0, 1 - from.w), clamp(top, 0, 1 - from.h));
                    cropMoved(before);
                });
                return;
            }
            const [mw, mh] = maxBox();
            const west = corner.includes("w");
            const north = corner.includes("n");
            const ax = west ? from.left + from.w : from.left;
            const ay = north ? from.top + from.h : from.top;
            const room = Math.min(1, (west ? ax : 1 - ax) / mw, (north ? ay : 1 - ay) / mh);
            dragOn(crop, e, (ev) => {
                const before = { ...shape };
                const px = (ev.clientX - area.left) / area.width;
                const py = (ev.clientY - area.top) / area.height;
                const want = Math.max(Math.abs(px - ax) / mw, Math.abs(py - ay) / mh);
                // `room` can come out a hair under MIN_SCALE (1 - 0.9).
                shape.scale = Math.max(MIN_SCALE, Math.min(want, room));
                const box = cropBox();
                placeAt(box, west ? ax - box.w : ax, north ? ay - box.h : ay);
                cropMoved(before);
            });
        });

        function pick(buttons, picked) {
            for (const button of buttons) {
                button.setAttribute("aria-pressed", String(button === picked));
            }
        }
        const aspects = root.querySelectorAll(".edit-aspect");
        for (const button of aspects) {
            button.addEventListener("click", () => {
                pick(aspects, button);
                shape.aspect = button.dataset.aspect;
                shape.scale = 1;
                shape.x = 0.5;
                shape.y = 0.5;
                cropChanged();
            });
        }
        root.querySelector(".edit-rotate").addEventListener("click", () => {
            shape.turn = (shape.turn + 90) % 360;
            shape.scale = 1;
            shape.x = 0.5;
            shape.y = 0.5;
            layout();
            cropChanged();
        });
        const mirror = root.querySelector(".edit-mirror");
        mirror.addEventListener("click", () => {
            shape.mirror = !shape.mirror;
            mirror.setAttribute("aria-pressed", String(shape.mirror));
            layout();
            cropChanged();
        });
        const sizes = root.querySelectorAll(".edit-size-pick");
        for (const button of sizes) {
            button.addEventListener("click", () => {
                pick(sizes, button);
                size = button.dataset.size;
                done(null);
                show();
            });
        }
        const speeds = root.querySelectorAll(".edit-speed");
        for (const button of speeds) {
            button.addEventListener("click", () => {
                pick(speeds, button);
                speed = Number(button.dataset.speed);
                player.defaultPlaybackRate = speed;
                player.playbackRate = speed;
                done(null);
                show();
            });
        }

        // --- play the part ---
        function playing(on) {
            play.hidden = on;
            pause.hidden = !on;
        }
        play.addEventListener("click", () => {
            wanted = null;
            const t = player.currentTime;
            if (t < a() || t >= b() - 0.05) player.currentTime = a() + INTO_FRAME_S;
            player.playbackRate = speed;
            player.play();
        });
        pause.addEventListener("click", () => player.pause());
        player.addEventListener("click", () => (player.paused ? play.click() : player.pause()));
        player.addEventListener("play", () => playing(true));
        player.addEventListener("pause", () => playing(false));
        player.addEventListener("timeupdate", () => {
            if (!player.paused && player.currentTime >= b()) player.pause();
        });

        // --- the light copy: swapped in once the edit worker made it ---
        async function loadFrames(url) {
            const body = url ? await answer(fetch(url)).catch(() => null) : null;
            if (!body?.times?.length) return;
            frames = body.times;
            start.value = String(snap(a()));
            end.value = String(snap(b()));
            show();
        }
        // The copy gone (swept a day after its last open): the original.
        player.addEventListener("error", () => {
            if (!player.currentSrc.includes("/edit/proxy")) return;
            player.src = root.dataset.original;
            watchFrames();
        });
        function swapIn(prepared) {
            const at = player.currentTime;
            player.preload = "auto";
            player.src = prepared.proxy_url;
            player.defaultPlaybackRate = speed;
            watchFrames();
            player.addEventListener(
                "loadedmetadata",
                () => {
                    player.currentTime = at;
                },
                { once: true }
            );
            strip.src = prepared.strip_url;
            loadFrames(prepared.frames_url);
        }
        const waitLine = document.getElementById("edit-wait");
        strip.addEventListener("load", () => timeline.classList.remove("edit-timeline-bare"));
        strip.addEventListener("error", () => {
            waitLine.hidden = true;
        });
        // Asked while the tab is shown; no connection or a 5xx asks again,
        // any other refusal (the video gone) ends it.
        async function waitForCopy() {
            const url = `/edit/prepared?video=${encodeURIComponent(video)}`;
            for (;;) {
                await wait(COPY_POLL_MS);
                if (document.hidden) continue;
                const response = await fetch(url).catch(() => null);
                if (!response || response.status >= 500) continue;
                if (!response.ok) {
                    waitLine.hidden = true;
                    return;
                }
                const state = await response.json().catch(() => null);
                if (!state) continue;
                if (state.status === "done") {
                    swapIn(state);
                    return;
                }
                if (state.status === "failed") {
                    waitLine.hidden = true;
                    return;
                }
                waitLine.textContent = state.ahead
                    ? `Loading frames… · ${state.ahead} ahead`
                    : "Loading frames…";
            }
        }
        if (root.dataset.ready === "true") loadFrames(root.dataset.frames);
        else waitForCopy();

        // --- Frame and Cut ---
        // Rounded: the server takes 0..1 and scale >= MIN_SCALE exactly.
        const round4 = (v) => Math.round(v * 10000) / 10000;
        const shapeParams = () => ({
            ...shape,
            scale: Math.max(MIN_SCALE, round4(shape.scale)),
            x: round4(shape.x),
            y: round4(shape.y),
            size,
        });

        frameBtn.addEventListener("click", () =>
            busy(frameBtn, " Saving…", async () => {
                player.pause();
                setStatus("");
                const query = new URLSearchParams({
                    video,
                    t: String(Math.max(shownAt - BEFORE_FRAME_S, 0)),
                    ...shapeParams(),
                });
                await saveFile(`/edit/frame?${query}`).catch((err) => setStatus(err.message));
            })
        );

        cutBtn.addEventListener("click", () =>
            busy(cutBtn, " Cutting…", async (label) => {
                player.pause();
                setStatus("");
                try {
                    const queued = await answer(
                        fetch("/edit/cut", {
                            method: "POST",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({
                                video,
                                start: Math.max(a() - BEFORE_FRAME_S, 0),
                                end: b(),
                                speed,
                                ...shapeParams(),
                            }),
                        })
                    );
                    // Polled until it is done or failed; a lost poll is tried again.
                    for (;;) {
                        await wait(POLL_MS);
                        const state = await answer(fetch(`/edit/cut/${queued.job_id}`)).catch(
                            () => null
                        );
                        if (!state) continue;
                        if (state.status === "done") {
                            done(state.video);
                            break;
                        }
                        if (state.status === "failed") throw new Error(state.error || FAILED);
                        label(state.ahead ? ` In line · ${state.ahead} ahead` : " Cutting…");
                    }
                } catch (err) {
                    setStatus(err.message);
                }
            })
        );

        end.value = String(snap(b()));
        player.currentTime = 0;
        show();
        showAt(0);
    }

    document.addEventListener("DOMContentLoaded", () => {
        const root = document.getElementById("edit");
        if (root) initEditor(root);
        const form = document.getElementById("edit-open");
        if (form) openPicked(form);
    });
})();
