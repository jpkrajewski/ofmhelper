/*
 * /clone submit: one POST queues a Wan 3.0 job per video, so the response is
 * a list of job ids rather than generation.js's single one. Reloading lets
 * the server render every new job as a pending card, which generation.js
 * then polls like any other.
 */
(function () {
    document.addEventListener("DOMContentLoaded", () => {
        const form = document.getElementById("clone-form");
        const statusEl = form.querySelector(".generation-status");
        const submitBtn = form.querySelector('button[type="submit"]');

        form.addEventListener("submit", (e) => {
            e.preventDefault();
            submitBtn.disabled = true;
            statusEl.textContent = "Uploading videos…";

            fetch(form.action, { method: "POST", body: new FormData(form) })
                .then(async (r) => {
                    const data = await r.json().catch(() => ({}));
                    if (!r.ok) {
                        throw new Error(
                            typeof data.detail === "string" ? data.detail : `Request failed (${r.status}).`
                        );
                    }
                    window.location.reload();
                })
                .catch((err) => {
                    statusEl.innerHTML = '<div class="generation-error"></div>';
                    statusEl.querySelector(".generation-error").textContent = err.message;
                    submitBtn.disabled = false;
                });
        });
    });
})();
