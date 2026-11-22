(() => {
    const failed = "Could not start. Try again.";
    const status = document.getElementById("st-status");
    const buttons = () => document.querySelectorAll(".st-bin");
    const say = (text) => {
        if (status) status.textContent = text;
    };
    const dialog = document.getElementById("st-confirm");
    const text = document.getElementById("st-confirm-text");
    let chosen = null;
    const reload = () => {
        if (document.hidden) {
            document.addEventListener("visibilitychange", reload, { once: true });
            return;
        }
        window.location.reload();
    };
    const refresh = () => {
        if (dialog?.open) {
            setTimeout(refresh, 5000);
            return;
        }
        reload();
    };

    if (document.querySelector("[data-refresh]")) setTimeout(refresh, 5000);

    async function start(button) {
        const body = new FormData();
        body.set("part", button.dataset.part || "");
        if (button.dataset.folder) body.set("folder_id", button.dataset.folder);
        let response;
        try {
            response = await fetch("/storage/bin-moves", {
                method: "POST",
                body,
                signal: AbortSignal.timeout(20000),
            });
        } catch {
            return failed;
        }
        const data = await response.json().catch(() => null);
        if (response.ok && data && typeof data.job_id === "string") return null;
        return typeof data?.detail === "string" ? data.detail : failed;
    }

    async function move(button) {
        buttons().forEach((b) => { b.disabled = true; });
        say("Moving to the bin…");
        const problem = await start(button);
        if (problem === null) {
            reload();
            return;
        }
        say(problem);
        buttons().forEach((b) => { b.disabled = false; });
    }

    dialog?.addEventListener("close", () => { chosen = null; });
    document.getElementById("st-confirm-back")?.addEventListener("click", () => {
        dialog?.close();
    });
    document.getElementById("st-confirm-go")?.addEventListener("click", () => {
        const button = chosen;
        chosen = null;
        dialog?.close();
        if (button) move(button);
    });

    document.addEventListener("click", (event) => {
        const button = event.target.closest(".st-bin");
        if (!button || button.disabled || !dialog) return;
        chosen = button;
        if (text) text.textContent = button.dataset.confirm || "";
        dialog.showModal();
    });
})();
