/*
 * /clone live cost estimate.
 *
 * Every picked video is one Wan 3.0 run, and a run costs about the same
 * number of credits per second of its source video (the server hands the
 * per-resolution rates, measured from this account's history, in
 * data-credits-per-video-s). Lengths are read in the browser from each
 * video's metadata -- a local File or a reused server file alike -- and
 * remembered, so reordering or changing resolution recalculates instantly.
 * A video over kie.ai's reference limit is flagged and left out of the total.
 */
(function () {
    const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
    // A per-second rate is fractions of a cent apart; cents would hide that.
    const rateUsd = new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
        maximumFractionDigits: 3,
    });
    const whole = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

    const fileLengths = new WeakMap();
    const pathLengths = new Map();

    function cached(item) {
        return item.file ? fileLengths.get(item.file) : pathLengths.get(item.path);
    }

    // Resolves to the length in seconds, or NaN when the browser can't read it.
    function readLength(item) {
        const known = cached(item);
        if (known !== undefined) return Promise.resolve(known);
        return new Promise((resolve) => {
            const video = document.createElement("video");
            video.preload = "metadata";
            const src = item.file
                ? URL.createObjectURL(item.file)
                : `/refs/file?path=${encodeURIComponent(item.path)}`;
            const finish = (seconds) => {
                if (item.file) {
                    URL.revokeObjectURL(src);
                    fileLengths.set(item.file, seconds);
                } else {
                    pathLengths.set(item.path, seconds);
                }
                video.removeAttribute("src");
                resolve(seconds);
            };
            video.addEventListener("loadedmetadata", () => finish(video.duration));
            video.addEventListener("error", () => finish(NaN));
            video.src = src;
        });
    }

    function line(index, name, detail, price, flagged) {
        const li = document.createElement("li");
        li.className = flagged ? "cost-line cost-line-flagged" : "cost-line";
        const label = document.createElement("span");
        label.className = "cost-line-name";
        label.textContent = `${index}. ${name}`;
        label.title = name;
        const meta = document.createElement("span");
        meta.className = "cost-line-detail";
        meta.textContent = detail;
        const value = document.createElement("span");
        value.className = "cost-line-price";
        value.textContent = price;
        li.append(label, meta, value);
        return li;
    }

    function init(form) {
        const picker = form.querySelector('.file-picker[data-field="videos"]');
        const resolution = form.querySelector('[name="resolution"]');
        const panel = document.getElementById("clone-cost");
        const lines = document.getElementById("clone-cost-lines");
        const rates = JSON.parse(form.dataset.creditsPerVideoS || "{}");
        const usdPerCredit = Number(form.dataset.usdPerCredit);
        const maxSeconds = Number(form.dataset.maxReferenceS);

        function render() {
            const items = window.FilePicker.items(picker);
            panel.hidden = items.length === 0;
            if (!items.length) return;
            const rate = rates[resolution.value] || 0;
            document.getElementById("clone-cost-rate").textContent =
                `${resolution.value} · ${rateUsd.format(rate * usdPerCredit)} per video second`;

            lines.innerHTML = "";
            let credits = 0;
            let seconds = 0;
            let runs = 0;
            let pending = 0;
            items.forEach((item, i) => {
                const length = cached(item);
                if (length === undefined) {
                    pending += 1;
                    lines.append(line(i + 1, item.name, "reading length…", "—", false));
                    return;
                }
                if (!Number.isFinite(length)) {
                    lines.append(line(i + 1, item.name, "length unreadable", "—", true));
                    return;
                }
                if (length > maxSeconds) {
                    const detail = `${length.toFixed(1)} s · over ${maxSeconds} s, kie.ai rejects it`;
                    lines.append(line(i + 1, item.name, detail, "—", true));
                    return;
                }
                const lineCredits = length * rate;
                credits += lineCredits;
                seconds += length;
                runs += 1;
                const detail = `${length.toFixed(1)} s · ${whole.format(lineCredits)} credits`;
                lines.append(line(i + 1, item.name, detail, usd.format(lineCredits * usdPerCredit), false));
            });

            const runNoun = runs === 1 ? "run" : "runs";
            document.getElementById("clone-cost-summary").textContent = pending
                ? `${runs} ${runNoun} · reading ${pending} more…`
                : `${runs} ${runNoun} · ${seconds.toFixed(1)} s of video`;
            document.getElementById("clone-cost-usd").textContent = usd.format(credits * usdPerCredit);
            document.getElementById("clone-cost-credits").textContent = `${whole.format(credits)} credits`;
        }

        function update() {
            render();
            const unknown = window.FilePicker.items(picker).filter((item) => cached(item) === undefined);
            unknown.forEach((item) => readLength(item).then(render));
        }

        picker.addEventListener("filepicker:change", update);
        resolution.addEventListener("change", render);
        update();
    }

    document.addEventListener("DOMContentLoaded", () => {
        const form = document.getElementById("clone-form");
        if (form) init(form);
    });
})();
