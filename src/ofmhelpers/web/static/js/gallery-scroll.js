(() => {

    const ROOT_MARGIN = "600px";
    const RETRY_FIRST_MS = 1000;
    const RETRY_MAX_MS = 30000;

    function initGalleryScroll(container) {
        if (!container.dataset.galleryEndpoint) return;

        let loading = false;
        let retryIn = RETRY_FIRST_MS;
        let retryTimer = 0;
        let round = 0;

        const observer = new IntersectionObserver(
            (entries) => {
                for (const entry of entries) {
                    if (entry.isIntersecting) load(entry.target);
                }
            },
            { rootMargin: ROOT_MARGIN }
        );

        function watch() {
            const sentinel = container.querySelector(".gallery-sentinel");
            if (sentinel) observer.observe(sentinel);
        }

        function load(sentinel) {

            if (loading) return;
            loading = true;
            observer.unobserve(sentinel);
            const mine = round;

            const url = new URL(container.dataset.galleryEndpoint, window.location.origin);
            url.searchParams.set("offset", sentinel.dataset.nextOffset);
            fetch(url)
                .then((r) => {
                    if (!r.ok) throw new Error("could not load more");
                    return r.text();
                })
                .then((html) => {
                    if (mine !== round) return;
                    const holder = document.createElement("div");
                    holder.innerHTML = html;
                    holder.querySelectorAll("[data-job-id]").forEach((card) => {
                        const id = CSS.escape(card.dataset.jobId);
                        if (container.querySelector(`[data-job-id="${id}"]`)) card.remove();
                    });
                    sentinel.replaceWith(...holder.childNodes);
                    loading = false;
                    retryIn = RETRY_FIRST_MS;
                    if (window.Generation) window.Generation.resumePendingCards();
                    container.dispatchEvent(new CustomEvent("gallery-scroll:append"));
                    watch();
                })
                .catch(() => {
                    if (mine !== round) return;
                    loading = false;
                    const resume = () => {
                        if (mine === round) observer.observe(sentinel);
                    };
                    if (!navigator.onLine) {
                        window.addEventListener("online", resume, { once: true });
                        return;
                    }
                    retryTimer = setTimeout(resume, retryIn);
                    retryIn = Math.min(retryIn * 2, RETRY_MAX_MS);
                });
        }

        container.addEventListener("gallery:replaced", () => {
            round += 1;
            clearTimeout(retryTimer);
            retryIn = RETRY_FIRST_MS;
            loading = false;
            observer.disconnect();
            watch();
        });
        watch();
    }

    document.addEventListener("DOMContentLoaded", () => {
        document.querySelectorAll("[data-gallery-endpoint]").forEach(initGalleryScroll);
    });
})();
