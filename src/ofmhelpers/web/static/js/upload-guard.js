/*
 * Leaving a page while its images/videos still go up to the server loses
 * them, and the run they are for (prod 2026-11-06: a refresh mid-upload on
 * /generate, nothing ever reached the app). Every page that uploads calls
 *
 *     const end = UploadGuard.begin();   // right after xhr.send()
 *     ...                                // load / error / abort:
 *     end();
 *
 * While any upload runs: the page says so (base.html's `.upload-wait` pop-up
 * under the header); a link on the page asks first in those words (#upload-leave,
 * Wait / Leave); a refresh or a closed tab gets the browser's own "Leave
 * site?" box -- its words are the browser's, no page can change them, so the
 * notice says it on the page behind it. `UploadGuard.sessionGone()`: the tab
 * is on its way to the login page, nothing is asked any more.
 *
 * `UploadGuard.post(url, formData, onProgress)`: the POST with upload
 * progress (fetch reports none) and the guard around it. Resolves with the
 * finished XMLHttpRequest whatever its status, rejects only when nothing
 * came back, so a redirect the answer asks for (an expired session) is not
 * held up.
 */
(() => {
    let running = 0;
    let quiet = false; // the session is gone, or the person chose Leave

    window.addEventListener("beforeunload", (e) => {
        if (running && !quiet) e.preventDefault();
    });

    function showNotice(shown) {
        const notice = document.querySelector(".upload-wait");
        if (notice) notice.hidden = !shown;
    }

    function begin() {
        running += 1;
        showNotice(true);
        let ended = false;
        return () => {
            if (ended) return;
            ended = true;
            running -= 1;
            if (running) return;
            showNotice(false);
            // file-picker.js: what went up can be reused from now on.
            document.dispatchEvent(new CustomEvent("uploads:done"));
        };
    }

    // A click that opens another page in this tab (another path or query, or
    // this one again); not a download, a new tab or a jump within the page.
    function leavesThePage(e, link) {
        if (!link || link.hasAttribute("download") || link.target) return false;
        if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return false;
        const url = new URL(link.href, window.location.href);
        if (!url.protocol.startsWith("http")) return false;
        const samePage =
            url.pathname === window.location.pathname &&
            url.search === window.location.search;
        return !(samePage && url.hash);
    }

    document.addEventListener("click", (e) => {
        if (!running || quiet || e.defaultPrevented) return;
        const link = e.target.closest("a[href]");
        const dialog = document.getElementById("upload-leave");
        if (!dialog || !leavesThePage(e, link)) return;
        e.preventDefault();
        dialog.dataset.href = link.href;
        dialog.showModal();
    });

    document.addEventListener("DOMContentLoaded", () => {
        const dialog = document.getElementById("upload-leave");
        if (!dialog) return;
        document.getElementById("upload-leave-stay").addEventListener("click", () => {
            dialog.close();
        });
        document.getElementById("upload-leave-go").addEventListener("click", () => {
            quiet = true;
            window.location.href = dialog.dataset.href;
        });
    });

    function post(url, formData, onProgress) {
        return new Promise((resolve, reject) => {
            const xhr = new XMLHttpRequest();
            let guarded = () => {};
            const ended = (settle) => () => {
                guarded();
                settle();
            };
            xhr.open("POST", url);
            xhr.upload.addEventListener("progress", (ev) => {
                if (ev.lengthComputable) {
                    onProgress(Math.floor((ev.loaded / ev.total) * 100));
                }
            });
            xhr.addEventListener("load", ended(() => resolve(xhr)));
            xhr.addEventListener(
                "error",
                ended(() => reject(new Error("Upload failed — check your connection and try again.")))
            );
            xhr.addEventListener("abort", ended(() => reject(new Error("Upload stopped."))));
            xhr.send(formData);
            // After send: one that throws is never guarded (its events
            // cannot fire before this line runs).
            guarded = begin();
        });
    }

    window.UploadGuard = {
        begin,
        post,
        sessionGone: () => {
            quiet = true;
        },
    };
})();
