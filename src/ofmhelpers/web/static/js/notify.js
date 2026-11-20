(() => {
    let audio = null;
    const unlocked = [];

    function unlockAudio() {
        if (audio) return audio;
        const Context = window.AudioContext || window.webkitAudioContext;
        if (!Context) return null;
        audio = new Context();
        return audio;
    }

    function chime() {
        if (!audio || audio.state !== "running") return false;
        const now = audio.currentTime;
        [[880, 0], [1318.5, 0.18]].forEach(([freq, at]) => {
            const osc = audio.createOscillator();
            const gain = audio.createGain();
            osc.type = "sine";
            osc.frequency.value = freq;
            gain.gain.setValueAtTime(0.0001, now + at);
            gain.gain.exponentialRampToValueAtTime(0.25, now + at + 0.02);
            gain.gain.exponentialRampToValueAtTime(0.0001, now + at + 0.6);
            osc.connect(gain).connect(audio.destination);
            osc.start(now + at);
            osc.stop(now + at + 0.65);
        });
        return true;
    }

    function canNotify() {
        return "Notification" in window && Notification.permission === "granted";
    }

    function desktop(message, tag) {
        if (!canNotify()) return false;
        try {
            const note = new Notification(message, { tag });
            note.onclick = () => {
                window.focus();
                note.close();
            };
            return true;
        } catch {

            return false;
        }
    }

    function onFirstGesture() {
        window.removeEventListener("pointerdown", onFirstGesture, true);
        window.removeEventListener("keydown", onFirstGesture, true);
        const ctx = unlockAudio();
        const resumed = ctx && ctx.state !== "running" ? ctx.resume() : Promise.resolve();
        if ("Notification" in window && Notification.permission === "default") {
            Notification.requestPermission().catch(() => {});
        }
        resumed.then(() => unlocked.forEach((fn) => fn())).catch(() => {});
    }
    window.addEventListener("pointerdown", onFirstGesture, true);
    window.addEventListener("keydown", onFirstGesture, true);

    window.Notifier = {
        chime,
        desktop,
        canNotify,
        onUnlock(fn) {
            unlocked.push(fn);
        },
    };
})();
