/*
 * Infinite scroll for a server-rendered card gallery.
 *
 * The gallery used to be a hard 20-item slice with no way to reach anything
 * older. Rather than paginating the whole page, an IntersectionObserver watches
 * a `.gallery-sentinel[data-next-offset]` at the end of the list and fetches the
 * next page as a ready-made HTML fragment, appending it in place.
 *
 * The server renders each page with the same partial the first page uses, and
 * only emits a new sentinel while there is another page -- so a response
 * without one is what ends the scroll. No item count is tracked client-side.
 *
 * Appended cards inherit the delegated Recreate and Download handlers, but a
 * still-running card needs its poller armed, hence Generation.resumePendingCards
 * (idempotent by design -- it skips cards already being polled).
 */
(function () {
    // Fetch a page before the sentinel is actually on screen: at a normal
    // scroll speed the cards are already there by the time you reach them.
    const ROOT_MARGIN = "600px";

    function initGalleryScroll(container) {
        const endpoint = container.dataset.galleryEndpoint;
        if (!endpoint) return;

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
            // One request at a time: the observer fires again on every scroll
            // tick while the sentinel is in view.
            if (loading) return;
            loading = true;
            observer.unobserve(sentinel);

            fetch(`${endpoint}?offset=${encodeURIComponent(sentinel.dataset.nextOffset)}`)
                .then((r) => {
                    if (!r.ok) throw new Error("could not load more");
                    return r.text();
                })
                .then((html) => {
                    // Replace the sentinel with the fragment: the fragment
                    // carries the next sentinel itself, or none when the
                    // gallery is exhausted.
                    const holder = document.createElement("div");
                    holder.innerHTML = html;
                    sentinel.replaceWith(...holder.childNodes);
                    loading = false;
                    if (window.Generation) window.Generation.resumePendingCards();
                    watch();
                })
                .catch(() => {
                    // Leave the sentinel in place and re-observe: scrolling
                    // away and back retries, which is the whole recovery story
                    // a gallery needs. session.js already handles a 401.
                    loading = false;
                    observer.observe(sentinel);
                });
        }

        watch();
    }

    document.addEventListener("DOMContentLoaded", () => {
        document.querySelectorAll("[data-gallery-endpoint]").forEach(initGalleryScroll);
    });
})();
