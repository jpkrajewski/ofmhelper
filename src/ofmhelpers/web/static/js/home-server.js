/*
 * /home's Server section (admins): its body comes from /home/server after
 * the page is open (reading the CPU takes a moment, the uploads folders a
 * walk), and again on Refresh.
 */
(() => {
    async function load(section, body, button) {
        button.disabled = true;
        try {
            const r = await fetch(section.dataset.src);
            body.innerHTML = r.ok ? await r.text() : "";
            if (!r.ok) body.textContent = "Could not read the server, try Refresh";
        } catch {
            body.textContent = "Could not read the server, try Refresh";
        } finally {
            button.disabled = false;
        }
    }

    document.addEventListener("DOMContentLoaded", () => {
        const section = document.getElementById("server");
        if (!section) return;
        const body = document.getElementById("server-body");
        const button = section.querySelector(".server-refresh");
        button.addEventListener("click", () => load(section, body, button));
        load(section, body, button);
    });
})();
