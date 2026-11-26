(() => {
    const POLL_MS = 2000;

    const COPY_POLL_MS = 1000;
    const GRIP_PX = 12;
    const DRAG_PX = 6;

    const MIN_SCALE = 0.1;

    const INTO_FRAME_S = 0.0005;
    const BEFORE_FRAME_S = 0.001;
    const FAILED = "Something went wrong: try again";
    const OFFLINE = "No connection: try again";
    const TOO_BIG = "This video is too big to open here";

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
        const detail = body?.detail;
        if (Array.isArray(detail) && detail.length) {
            return new Error(
                detail.map((d) => d.msg.replace(/^Value error, /, "")).join("; ")
            );
        }
        const worded = status === 400 && typeof detail === "string";
        return new Error(worded ? detail : FAILED);
    }

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
        if (xhr.status >= 200 && xhr.status < 300 && body?.path) return body;
        throw failure(xhr.status, body);
    }

    function pickedVideo(item) {
        if (item.path) return answer(fetch(`/edit/video?video=${encodeURIComponent(item.path)}`));
        return uploadVideo(item);
    }

    function onPick(form, use) {
        const picker = form.querySelector(".file-picker");
        picker.addEventListener("filepicker:change", async () => {
            const item = window.FilePicker.items(picker)[0];
            if (!item) return;
            try {
                if (item.path) {
                    await use({ path: item.path });
                } else {
                    const video = await uploadVideo(item);
                    if (video) await use(video);
                }
            } catch (err) {
                setStatus(err.message);
            } finally {
                window.FilePicker.clearPicker(picker);
            }
        });
    }

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

    function frameIndex(frames, t) {
        let lo = 0;
        let hi = frames.length - 1;
        while (lo < hi) {
            const mid = (lo + hi + 1) >> 1;
            if (frames[mid] <= t + 1e-4) lo = mid;
            else hi = mid - 1;
        }
        return lo;
    }

    function initEditor(root) {
        const limit = Number(root.dataset.limit);
        const min = Number(root.dataset.min);
        const maxClips = Number(root.dataset.maxClips) || 10;
        const first = root.dataset.video;
        const player = document.getElementById("edit-player");
        const stage = document.getElementById("edit-stage");
        const crop = document.getElementById("edit-crop");
        const start = document.getElementById("edit-start");
        const end = document.getElementById("edit-end");
        const timeline = document.getElementById("edit-timeline");
        const segs = document.getElementById("edit-segs");
        const track = document.getElementById("edit-track");
        const waitLine = document.getElementById("edit-wait");
        const play = root.querySelector(".edit-play");
        const pause = root.querySelector(".edit-pause");
        const undoBtn = root.querySelector(".edit-undo");
        const cutBtn = document.getElementById("edit-cut");
        const frameBtn = document.getElementById("edit-frame");
        const length = document.getElementById("edit-length");
        const shape = { turn: 0, mirror: false, aspect: "original", scale: 1, x: 0.5, y: 0.5 };
        let speed = 1;
        let size = root.querySelector('.edit-size-pick[aria-pressed="true"]').dataset.size;

        const videos = new Map();
        function know(v) {
            if (videos.has(v.path)) return videos.get(v.path);
            const known = {
                path: v.path,
                name: v.name,
                seconds: v.seconds,
                fps: v.fps || null,
                frameS: 1 / (v.fps || 30),
                original: v.url,
                src: v.url,
                strip: "",
                frames: null,
            };
            videos.set(v.path, known);
            return known;
        }
        know({
            path: first,
            name: root.dataset.name,
            seconds: Number(root.dataset.seconds),
            fps: Number(root.dataset.fps) || null,
            url: root.dataset.original,
        });
        let clips = [{ video: first, start: 0, end: videos.get(first).seconds }];

        const history = [];
        const lengthOf = (c) => c.end - c.start;
        const total = () => clips.reduce((sum, c) => sum + lengthOf(c), 0);
        const startOf = (i) => clips.slice(0, i).reduce((sum, c) => sum + lengthOf(c), 0);
        const a = () => Number(start.value);
        const b = () => Number(end.value);
        const pct = (t) => `${(t / total()) * 100}%`;

        function locate(t) {
            let at = 0;
            for (let i = 0; i < clips.length; i++) {
                const len = lengthOf(clips[i]);
                if (t < at + len - 1e-6 || i === clips.length - 1) return { i, at };
                at += len;
            }
            return { i: 0, at: 0 };
        }

        function source(t) {
            const { i, at } = locate(t);
            const clip = clips[i];
            return {
                i,
                clip,
                video: videos.get(clip.video),
                time: clamp(clip.start + t - at, clip.start, clip.end),
            };
        }

        function snapSource(v, s) {
            return v.frames
                ? v.frames[frameIndex(v.frames, s)]
                : Math.floor(s / v.frameS + 1e-6) * v.frameS;
        }
        function snap(t) {
            const all = total();
            if (t >= all - 1e-6) return all;
            const at = clamp(t, 0, all);
            const { i, at: from } = locate(at);
            const c = clips[i];
            const s = clamp(snapSource(videos.get(c.video), c.start + at - from), c.start, c.end);
            return from + s - c.start;
        }

        function stepFrom(t, n) {
            const { i, at } = locate(t);
            const c = clips[i];
            const v = videos.get(c.video);
            const s = c.start + t - at;
            const next = v.frames
                ? v.frames[clamp(frameIndex(v.frames, s) + n, 0, v.frames.length - 1)]
                : snapSource(v, s) + n * v.frameS;
            if (n < 0 && next < c.start - 1e-6) return i > 0 ? snap(at - 1e-4) : 0;
            if (n > 0 && (next >= c.end - 1e-6 || next <= s + 1e-6)) {
                return i < clips.length - 1 ? at + lengthOf(c) : total();
            }
            return at + next - c.start;
        }

        function atLeast(t) {
            const s = snap(t);
            return s < t - 1e-6 ? stepFrom(s, 1) : s;
        }

        function timecode(t) {
            const at = Math.max(t, 0);
            const sec = Math.floor(at + 1e-6);
            const ff = Math.floor((at - sec) / source(at).video.frameS + 1e-6);
            const two = (n) => String(n).padStart(2, "0");
            return `${two(Math.floor(sec / 60))}:${two(sec % 60)}:${two(ff)}`;
        }

        let loaded = first;
        let shown = 0;

        function load(path) {
            if (loaded === path && player.readyState >= HTMLMediaElement.HAVE_METADATA) {
                return Promise.resolve();
            }
            loaded = path;
            return new Promise((resolve) => {
                player.addEventListener("loadedmetadata", () => resolve(), { once: true });
                player.src = videos.get(path).src;
                player.defaultPlaybackRate = speed;
                watchFrames();
            });
        }

        let wanted = null;
        let markAt = 0;
        let shownAt = 0;
        function apply() {
            if (wanted === null || player.seeking) return;
            player.currentTime = wanted;
            wanted = null;
        }
        player.addEventListener("seeked", apply);
        async function seek(t) {
            const at = snap(t);
            markAt = at;
            timeline.style.setProperty("--at", pct(at));
            const src = source(at);
            shown = src.i;
            if (loaded !== src.clip.video) await load(src.clip.video);
            wanted = Math.min(src.time + INTO_FRAME_S, src.video.seconds);
            apply();
        }

        function timelineTime(media) {
            const c = clips[shown];
            if (!c) return 0;
            return startOf(shown) + clamp(media, c.start, c.end) - c.start;
        }
        function showAt(t) {
            shownAt = t;
            document.getElementById("edit-at").textContent = timecode(t);
            if (wanted === null && !player.seeking) {
                markAt = t;
                timeline.style.setProperty("--at", pct(t));
            }
        }

        let playTo = null;
        let advancing = false;

        async function advance() {
            if (advancing) return;
            const next = shown + 1;
            if (next >= clips.length) {
                player.pause();
                return;
            }
            advancing = true;
            try {
                shown = next;
                const c = clips[next];
                await load(c.video);
                player.currentTime = c.start + INTO_FRAME_S;
                player.playbackRate = speed;
                await player.play().catch(() => {});
            } finally {
                advancing = false;
            }
        }
        function onMedia(media) {
            const c = clips[shown];
            if (!player.paused && !player.seeking && !advancing && c && playTo !== null) {
                const past = timelineTime(media) - playTo;
                if (past >= -1e-3) {
                    player.pause();
                    if (past > 0) player.currentTime = media - past;
                } else if (media >= c.end - 1e-3) {
                    advance();
                    return;
                }
            }
            showAt(timelineTime(media));
        }
        const exactFrames = "requestVideoFrameCallback" in player;
        let frameCallback = 0;
        const onFrame = (_now, meta) => {

            onMedia(meta.mediaTime);
            frameCallback = player.requestVideoFrameCallback(onFrame);
        };

        function watchFrames() {
            if (!exactFrames) return;
            player.cancelVideoFrameCallback(frameCallback);
            frameCallback = player.requestVideoFrameCallback(onFrame);
        }
        if (exactFrames) watchFrames();
        else {
            for (const ev of ["seeked", "timeupdate"]) {
                player.addEventListener(ev, () => onMedia(player.currentTime));
            }
        }
        function playing(on) {
            play.hidden = on;
            pause.hidden = !on;
        }
        play.addEventListener("click", async () => {
            wanted = null;
            const from = markAt >= a() && markAt < b() - 0.05 ? markAt : a();
            playTo = b();
            await seek(from);
            player.playbackRate = speed;
            player.play().catch(() => {});
        });
        pause.addEventListener("click", () => player.pause());
        player.addEventListener("click", () => (player.paused ? play.click() : player.pause()));
        player.addEventListener("play", () => playing(true));
        player.addEventListener("pause", () => {
            if (!advancing) playTo = null;
            playing(false);
        });

        player.addEventListener("emptied", () => {
            if (!advancing) playing(false);
        });

        player.addEventListener("error", () => {
            const v = videos.get(loaded);
            if (!v || !player.currentSrc.includes("/edit/proxy")) return;
            v.src = v.original;
            player.src = v.original;
            watchFrames();
        });

        function shownSize() {
            const sideways = shape.turn % 180 !== 0;
            const w = player.videoWidth;
            const h = player.videoHeight;
            return sideways ? [h, w] : [w, h];
        }
        function outSize() {
            if (!player.videoWidth) return "";
            const box = cropBox();
            const [sw, sh] = shownSize();
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

        function done(made) {
            const clone = document.getElementById("edit-clone");
            const cloneLong = document.getElementById("edit-clone-long");
            const download = document.getElementById("edit-download");
            clone.hidden = !made;
            cloneLong.hidden = !made;
            download.hidden = !made;
            if (!made) return;

            clone.href =
                `/clone?video=${encodeURIComponent(made.path)}` +
                `&from=${encodeURIComponent(first)}`;
            cloneLong.href = `/clone-long?video=${encodeURIComponent(made.path)}`;
            download.href = made.url;
            download.setAttribute("download", made.name);
        }

        const STORE = `edit:timeline:${first}`;
        function save() {
            try {
                const used = [...new Set(clips.map((c) => c.video))].map((p) => {
                    const v = videos.get(p);
                    return { path: v.path, name: v.name, seconds: v.seconds, fps: v.fps, url: v.original };
                });
                sessionStorage.setItem(STORE, JSON.stringify({ clips, videos: used }));
            } catch {

            }
        }
        function stripOf(seg, v, c) {
            if (!v.strip) return;
            const len = lengthOf(c);
            const room = v.seconds - len;
            seg.style.setProperty("--strip", `url("${v.strip.replaceAll('"', "%22")}")`);
            seg.style.setProperty("--strip-w", `${(v.seconds / len) * 100}%`);
            seg.style.setProperty("--strip-x", room > 1e-6 ? `${(c.start / room) * 100}%` : "0%");
        }

        function groups() {
            const found = [];
            clips.forEach((c, i) => {
                const last = found.at(-1);
                if (last && clips[last.to].video === c.video) last.to = i;
                else found.push({ from: i, to: i });
            });
            return found;
        }
        const spanOf = (g) => [startOf(g.from), startOf(g.to + 1)];
        function drawClips() {
            const all = total();
            const share = (t) => `${(t / all) * 100}%`;
            const blocks = clips.map((c, i) => {
                const seg = document.createElement("div");
                seg.className = "edit-seg";
                seg.style.setProperty("--seg-from", share(startOf(i)));
                seg.style.setProperty("--seg-width", share(lengthOf(c)));
                stripOf(seg, videos.get(c.video), c);
                return seg;
            });
            const labelled = groups();
            const labels = labelled.map((g) => {
                const [from, to] = spanOf(g);
                return label(g, share(from), share(to - from), labelled.length > 1);
            });
            segs.replaceChildren(...blocks);
            track.replaceChildren(...labels);
            timeline.classList.toggle("edit-timeline-bare", !videos.get(first).strip);
            start.max = String(all);
            end.max = String(all);
            undoBtn.disabled = !history.length;
        }
        function label(g, from, width, droppable) {
            const el = document.createElement("div");
            el.className = "edit-label";
            el.style.setProperty("--seg-from", from);
            el.style.setProperty("--seg-width", width);
            const [s0, s1] = spanOf(g);
            const text = document.createElement("span");
            text.textContent = `${videos.get(clips[g.from].video).name} · ${(s1 - s0).toFixed(1)} s`;
            text.title = text.textContent;
            el.append(text);
            if (droppable) {
                const drop = document
                    .getElementById("edit-label-x")
                    .content.firstElementChild.cloneNode(true);
                drop.addEventListener("click", (e) => {
                    e.stopPropagation();
                    change(() => clips.splice(g.from, g.to - g.from + 1));
                });
                el.append(drop);
            }
            el.addEventListener("pointerdown", (e) => grab(e, el, g));
            return el;
        }

        function grab(e, el, g) {
            if (e.target.closest(".edit-label-drop")) return;
            e.preventDefault();
            el.setPointerCapture(e.pointerId);
            let dx = 0;
            let dragged = false;
            const move = (ev) => {
                dx = ev.clientX - e.clientX;
                if (!dragged && Math.abs(dx) < DRAG_PX) return;
                dragged = true;
                el.dataset.dragging = "";
                el.style.translate = `${dx}px 0`;
            };
            const stop = () => {
                el.removeEventListener("pointermove", move);
                el.removeEventListener("pointerup", stop);
                el.removeEventListener("pointercancel", stop);
                const [s0, s1] = spanOf(g);
                if (!dragged) {
                    mark(s0, s1);
                    return;
                }
                const centre = (s0 + s1) / 2 + (dx / track.clientWidth) * total();

                const others = groups().filter((o) => o.from !== g.from);
                const before = others.filter((o) => {
                    const [o0, o1] = spanOf(o);
                    return (o0 + o1) / 2 < centre;
                });
                const at = before.length ? before.at(-1).to + 1 : 0;
                if (at === g.from || at === g.to + 1) {
                    drawClips();
                    return;
                }
                change(() => {
                    const moved = clips.splice(g.from, g.to - g.from + 1);
                    clips.splice(at > g.from ? at - moved.length : at, 0, ...moved);
                });
            };
            el.addEventListener("pointermove", move);
            el.addEventListener("pointerup", stop);
            el.addEventListener("pointercancel", stop);
        }

        function mark(from, to) {
            start.value = String(from);
            end.value = String(to);
            done(null);
            player.pause();
            seek(from);
            show();
        }

        function change(fn) {
            history.push({ clips: clips.map((c) => ({ ...c })), a: a(), b: b() });
            fn();
            player.pause();
            drawClips();
            save();
            mark(0, total());
            seek(clamp(markAt, 0, total()));
        }
        undoBtn.addEventListener("click", () => {
            const last = history.pop();
            if (!last) return;
            clips = last.clips;
            player.pause();
            drawClips();
            save();
            mark(last.a, last.b);
        });

        function without(from, to) {
            const kept = [];
            let at = 0;
            for (const c of clips) {
                const s0 = at;
                const s1 = at + lengthOf(c);
                at = s1;
                if (to <= s0 || from >= s1) {
                    kept.push(c);
                    continue;
                }
                if (from - s0 >= min) kept.push({ ...c, end: c.start + (from - s0) });
                if (s1 - to >= min) kept.push({ ...c, start: c.start + (to - s0) });
            }
            return kept;
        }
        root.querySelector(".edit-delete").addEventListener("click", () => {
            setStatus("");
            const joint = a();
            const kept = without(a(), b());
            if (kept.reduce((sum, c) => sum + lengthOf(c), 0) < min) {
                setStatus("Nothing would be left: mark less");
                return;
            }
            if (kept.length > maxClips) {
                setStatus(`At most ${maxClips} clips`);
                return;
            }
            change(() => {
                clips = kept;
            });
            seek(Math.min(joint, total()));
        });

        async function loadFrames(v, url) {
            const body = url ? await answer(fetch(url)).catch(() => null) : null;
            if (!body?.times?.length) return;
            v.frames = body.times;
            show();
        }
        function ready(v, state) {
            v.src = state.proxy_url;
            v.strip = state.strip_url;
            loadFrames(v, state.frames_url);
            drawClips();

            if (loaded !== v.path || player.getAttribute("src") === v.src) return;

            const at = player.currentTime;
            const wasPlaying = !player.paused;
            player.preload = "auto";
            player.src = v.src;
            watchFrames();
            player.addEventListener(
                "loadedmetadata",
                () => {
                    player.currentTime = at;
                    if (wasPlaying) player.play().catch(() => {});
                },
                { once: true }
            );
        }

        async function prepare(v) {
            const url = `/edit/prepared?video=${encodeURIComponent(v.path)}`;
            for (;;) {
                await wait(COPY_POLL_MS);
                if (document.hidden) continue;
                const response = await fetch(url).catch(() => null);
                if (!response || response.status >= 500) continue;
                if (!response.ok) break;
                const state = await response.json().catch(() => null);
                if (!state) continue;
                if (state.status === "done") {
                    ready(v, state);
                    return;
                }
                if (state.status === "failed") break;
                if (v.path === first) {
                    waitLine.textContent = state.ahead
                        ? `Loading frames… · ${state.ahead} ahead`
                        : "Loading frames…";
                }
            }
            if (v.path === first) waitLine.hidden = true;
        }

        const addForm = document.getElementById("edit-add");
        root.querySelector(".edit-add-video").addEventListener("click", () => {
            addForm.hidden = !addForm.hidden;
        });
        onPick(addForm, async (picked) => {
            if (clips.length >= maxClips) {
                setStatus(`At most ${maxClips} clips`);
                return;
            }
            const opened = picked.seconds ? picked : await pickedVideo(picked);
            const v = know(opened);
            setStatus("");
            addForm.hidden = true;
            change(() => clips.push({ video: v.path, start: 0, end: v.seconds }));
            if (!v.strip) prepare(v);
        });

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
            return clamp(((x - box.left) / box.width) * total(), 0, total());
        }

        function gripAt(x) {
            const box = timeline.getBoundingClientRect();
            const off = (t) => Math.abs(x - (box.left + (t / total()) * box.width));
            const toStart = off(a());
            const toEnd = off(b());
            if (Math.min(toStart, toEnd) > GRIP_PX) return null;
            return toStart <= toEnd ? start : end;
        }

        function dragOn(el, e, onMove) {
            el.setPointerCapture(e.pointerId);
            const stop = () => {
                el.removeEventListener("pointermove", onMove);
                el.removeEventListener("pointerup", stop);
                el.removeEventListener("pointercancel", stop);
            };
            el.addEventListener("pointermove", onMove);
            el.addEventListener("pointerup", stop);
            el.addEventListener("pointercancel", stop);
            onMove(e);
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

        function step(n, seconds = false) {
            player.pause();
            seek(seconds ? snap(markAt + n) : stepFrom(markAt, n));
        }
        root.querySelector(".edit-back").addEventListener("click", () => step(-1));
        root.querySelector(".edit-next").addEventListener("click", () => step(1));

        document.addEventListener("keydown", (e) => {
            if (e.target.closest?.("input, textarea, select, dialog[open]")) return;
            if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
            e.preventDefault();
            step(e.key === "ArrowLeft" ? -1 : 1, e.shiftKey);
        });

        function maxBox() {
            if (shape.aspect === "original" || !player.videoWidth) return [1, 1];
            const [w, h] = shape.aspect.split(":").map(Number);
            const [sw, sh] = shownSize();
            const r = w / h / (sw / sh);
            return [Math.min(1, r), Math.min(1, 1 / r)];
        }

        function cropBox() {
            const [mw, mh] = maxBox();
            const w = mw * shape.scale;
            const h = mh * shape.scale;
            return { w, h, left: (1 - w) * shape.x, top: (1 - h) * shape.y };
        }

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

        function cropMoved(before) {
            if (before.x === shape.x && before.y === shape.y && before.scale === shape.scale) return;
            cropChanged();
        }

        function layout() {
            if (!player.videoWidth) return;
            const [sw, sh] = shownSize();
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

        if (player.readyState >= HTMLMediaElement.HAVE_METADATA) metadataIn();

        crop.addEventListener("pointerdown", (e) => {
            e.preventDefault();
            const area = stage.getBoundingClientRect();
            const from = cropBox();
            const corner = e.target.closest(".edit-crop-grip")?.dataset.corner;
            if (!corner) {

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
                const src = source(shownAt);
                const query = new URLSearchParams({
                    video: src.clip.video,
                    t: String(Math.max(src.time - BEFORE_FRAME_S, 0)),
                    ...shapeParams(),
                });
                await saveFile(`/edit/frame?${query}`).catch((err) => setStatus(err.message));
            })
        );

        function cutWhat() {
            const parts = [];
            let at = 0;
            for (const c of clips) {
                const s0 = at;
                const s1 = at + lengthOf(c);
                at = s1;
                const lo = Math.max(a(), s0);
                const hi = Math.min(b(), s1);
                if (hi - lo > 1e-6) {
                    parts.push({ video: c.video, start: c.start + lo - s0, end: c.start + hi - s0 });
                }
            }
            parts[0].start = Math.max(parts[0].start - BEFORE_FRAME_S, 0);
            return parts.length === 1 ? parts[0] : { clips: parts };
        }

        cutBtn.addEventListener("click", () =>
            busy(cutBtn, " Cutting…", async (label) => {
                player.pause();
                setStatus("");
                try {
                    const queued = await answer(
                        fetch("/edit/cut", {
                            method: "POST",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ ...cutWhat(), speed, ...shapeParams() }),
                        })
                    );

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

        try {
            const kept = JSON.parse(sessionStorage.getItem(STORE) || "null");
            if (kept?.clips?.length) {
                for (const v of kept.videos) know(v);
                clips = kept.clips.filter((c) => videos.has(c.video));
            }
        } catch {

        }
        const firstVideo = videos.get(first);
        if (root.dataset.ready === "true") {
            ready(firstVideo, {
                proxy_url: root.dataset.proxy,
                strip_url: root.dataset.strip,
                frames_url: root.dataset.frames,
            });
        } else prepare(firstVideo);
        for (const v of videos.values()) if (v !== firstVideo) prepare(v);
        drawClips();
        if (clips.length > 1 || total() < firstVideo.seconds - 1e-6) mark(0, total());
        else end.value = String(snap(b()));
        player.currentTime = 0;
        show();
        showAt(0);
    }

    document.addEventListener("DOMContentLoaded", () => {
        const root = document.getElementById("edit");
        if (root) initEditor(root);

        const open = document.getElementById("edit-open");
        if (open) {
            onPick(open, (video) => {
                window.location.href = `/edit?video=${encodeURIComponent(video.path)}`;
            });
        }
    });
})();
