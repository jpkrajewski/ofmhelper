(() => {
    const template = document.getElementById("card-failed-more");
    if (!template) return;
    const minRun = 3;
    const opened = new Set();
    const watch = { childList: true, subtree: true };

    function failure(card) {
        const box = card.querySelector(":scope > .result-failed");
        if (!box || box.classList.contains("fail-note")) return null;
        const meta = card.querySelector(":scope > .source")?.textContent || "";
        return `${box.textContent} | ${meta}`.replace(/\s+/g, " ").trim();
    }

    function runs(gallery) {
        const found = [];
        let current = null;
        for (const card of gallery.querySelectorAll(":scope > .result-item")) {
            const key = failure(card);
            if (key && current?.key === key) {
                current.cards.push(card);
                continue;
            }
            current = key ? { key, cards: [card] } : null;
            if (current) found.push(current);
        }
        return found;
    }

    function fold(gallery) {
        gallery.querySelectorAll(".failed-more").forEach((button) => button.remove());
        gallery.querySelectorAll(":scope > [data-folded]").forEach((card) => card.removeAttribute("data-folded"));
        for (const run of runs(gallery)) {
            if (run.cards.length < minRun || opened.has(run.key)) continue;
            const [first, ...rest] = run.cards;
            rest.forEach((card) => card.setAttribute("data-folded", ""));
            const button = template.content.firstElementChild.cloneNode(true);
            button.textContent = `${rest.length} more like this`;
            button.addEventListener("click", () => {
                opened.add(run.key);
                fold(gallery);
            });
            first.append(button);
        }
    }

    for (const gallery of document.querySelectorAll(".results[data-gallery-endpoint]")) {
        let queued = false;
        const observer = new MutationObserver(() => {
            if (queued) return;
            queued = true;
            requestAnimationFrame(() => {
                queued = false;
                observer.disconnect();
                fold(gallery);
                observer.observe(gallery, watch);
            });
        });
        fold(gallery);
        observer.observe(gallery, watch);
    }
})();
