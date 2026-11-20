(() => {
    let running = 0;
    let quiet = false;

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

            document.dispatchEvent(new CustomEvent("uploads:done"));
        };
    }

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

    function post(url, formData, onProgress, headers = {}) {
        return new Promise((resolve, reject) => {
            const xhr = new XMLHttpRequest();
            let guarded = () => {};
            const ended = (settle) => () => {
                guarded();
                settle();
            };
            xhr.open("POST", url);
            for (const [name, value] of Object.entries(headers)) {
                xhr.setRequestHeader(name, value);
            }
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
