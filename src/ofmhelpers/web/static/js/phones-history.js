/*
 * /phones "History": the person's phones after each change (the database
 * records every one: features/phones/revisions.py). The toolbar's History
 * button opens the list from GET /phones/revisions; Restore on a line asks
 * first, then POSTs /phones/revisions/{id}/restoration and reloads the
 * page. A restore is itself a line, so it is undone the same way.
 */
(() => {
    const ENDPOINT = "/phones/revisions";

    // The reader's zone (the header's globe, cookie ofm_timezone), else the
    // browser's.
    function zone() {
        const found = document.cookie.match(/(?:^|;\s*)ofm_timezone=([^;]+)/);
        return found ? decodeURIComponent(found[1]) : undefined;
    }

    function localTimes(list) {
        list.querySelectorAll("time[datetime]").forEach((el) => {
            const when = new Date(el.dateTime);
            if (Number.isNaN(when.getTime())) return;
            const options = { dateStyle: "medium", timeStyle: "short" };
            try {
                el.textContent = when.toLocaleString(undefined, { ...options, timeZone: zone() });
            } catch {
                el.textContent = when.toLocaleString(undefined, options);
            }
        });
    }

    function fail(box, text) {
        box.textContent = text;
        box.hidden = !text;
    }

    function load(list, errorBox) {
        fail(errorBox, "");
        return fetch(ENDPOINT)
            .then((response) => {
                if (!response.ok) throw new Error(`history ${response.status}`);
                return response.text();
            })
            .then((html) => {
                list.innerHTML = html;
                localTimes(list);
            })
            .catch(() => fail(errorBox, "Could not load the history. Try again."));
    }

    function ask(confirmBox, question, when) {
        question.textContent = `Go back to the phones as they were on ${when}? Accounts and passwords added since will be removed.`;
        confirmBox.returnValue = "";
        confirmBox.showModal();
        return new Promise((resolve) => {
            confirmBox.addEventListener(
                "close",
                () => resolve(confirmBox.returnValue === "confirm"),
                { once: true },
            );
        });
    }

    function restore(line, errorBox) {
        return fetch(`${ENDPOINT}/${line.dataset.revisionId}/restoration`, { method: "POST" })
            .then((response) => {
                if (response.ok) {
                    window.location.reload();
                    return null;
                }
                return response
                    .json()
                    .catch(() => ({}))
                    .then((body) => {
                        const said = typeof body.detail === "string" ? body.detail : "";
                        fail(errorBox, said || "Could not restore. Nothing was changed.");
                    });
            })
            .catch(() => fail(errorBox, "Could not restore. Nothing was changed."));
    }

    document.addEventListener("DOMContentLoaded", () => {
        const dialog = document.getElementById("ph-history");
        const list = document.getElementById("ph-revisions");
        const errorBox = document.getElementById("ph-history-error");
        const confirmBox = document.getElementById("ph-restore-confirm");
        const question = document.getElementById("ph-restore-question");
        const opener = document.getElementById("ph-history-open");
        if (!dialog || !list || !opener) return;

        opener.addEventListener("click", () => {
            dialog.showModal();
            load(list, errorBox);
        });
        document.getElementById("ph-history-close").addEventListener("click", () => dialog.close());
        list.addEventListener("click", (e) => {
            const button = e.target.closest(".ph-revision-restore");
            const line = button?.closest(".ph-revision");
            if (!line) return;
            const when = line.querySelector("time").textContent;
            ask(confirmBox, question, when).then((yes) => {
                if (!yes) return;
                button.disabled = true;
                restore(line, errorBox).finally(() => {
                    button.disabled = false;
                });
            });
        });
    });
})();
