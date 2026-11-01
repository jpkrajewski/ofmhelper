/*
 * /viral: the profile form posts to /viral/run and opens that search;
 * a search still running is polled until its job ends, then the page
 * reloads with its reels. A failed poll is tried again, a hidden tab
 * polls once it is shown (as generation.js does).
 */
(() => {
    const POLL_MS = 3000;

    function initForm(form) {
        const error = document.getElementById("viral-error");
        const submit = form.querySelector('button[type="submit"]');
        form.addEventListener("submit", (e) => {
            e.preventDefault();
            error.hidden = true;
            submit.disabled = true;
            fetch(form.action, { method: "POST", body: new FormData(form) })
                .then(async (r) => {
                    const body = await r.json().catch(() => ({}));
                    if (!r.ok) throw new Error(body.detail || "Could not start the search, try again.");
                    window.location.href = `/viral?job=${body.job_id}`;
                })
                .catch((err) => {
                    error.textContent = err.message;
                    error.hidden = false;
                    submit.disabled = false;
                });
        });
    }

    function poll(url) {
        if (document.hidden) {
            document.addEventListener("visibilitychange", function shown() {
                if (document.hidden) return;
                document.removeEventListener("visibilitychange", shown);
                poll(url);
            });
            return;
        }
        fetch(url)
            .then((r) => (r.ok || r.status === 404 ? r.json() : Promise.reject()))
            .then((job) => {
                if (job.status === "running" || job.status === "queued") {
                    setTimeout(() => poll(url), POLL_MS);
                } else {
                    window.location.reload();
                }
            }, () => setTimeout(() => poll(url), POLL_MS));
    }

    document.addEventListener("DOMContentLoaded", () => {
        const form = document.getElementById("viral-form");
        if (form) initForm(form);
        const pending = document.getElementById("viral-pending");
        if (pending) setTimeout(() => poll(pending.dataset.poll), POLL_MS);
    });
})();
