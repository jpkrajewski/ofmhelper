(() => {

    const ROOT_MARGIN = "600px";

    function initGalleryScroll(container) {
        if (!container.dataset.galleryEndpoint) return;

        let loading = false;

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

            const url = new URL(container.dataset.galleryEndpoint, window.location.origin);
            url.searchParams.set("offset", sentinel.dataset.nextOffset);
            fetch(url)
                .then((r) => {
                    if (!r.ok) throw new Error("could not load more");
                    return r.text();
                })
                .then((html) => {

                    const holder = document.createElement("div");
                    holder.innerHTML = html;
                    holder.querySelectorAll("[data-job-id]").forEach((card) => {
                        const id = CSS.escape(card.dataset.jobId);
                        if (container.querySelector(`[data-job-id="${id}"]`)) card.remove();
                    });
                    sentinel.replaceWith(...holder.childNodes);
                    loading = false;
                    if (window.Generation) window.Generation.resumePendingCards();
                    container.dispatchEvent(new CustomEvent("gallery-scroll:append"));
                    watch();
                })
                .catch(() => {

                    loading = false;
                    observer.observe(sentinel);
                });
        }

        container.addEventListener("gallery:replaced", () => {
            loading = false;
            watch();
        });
        watch();
    }

    document.addEventListener("DOMContentLoaded", () => {
        document.querySelectorAll("[data-gallery-endpoint]").forEach(initGalleryScroll);
    });
})();
