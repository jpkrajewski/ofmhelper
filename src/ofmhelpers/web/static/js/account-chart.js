/*
 * /accounts/{id} follower chart: crosshair + tooltip over the server-drawn
 * SVG (templates/account_analytics.html, figure[data-fchart]).
 *
 * The points are the data table's rows: data-x/data-y = position in percent
 * of the plot, cells = sync time, followers, change. The pointer snaps to
 * the nearest point by x (aim at a date, not at a 2px line); arrow keys,
 * Home and End step through them while the plot has focus. Without this
 * script the table still holds every value.
 */
(() => {

    function setup(figure) {
        const plot = figure.querySelector(".fchart-plot");
        const cross = figure.querySelector(".fchart-cross");
        const focus = figure.querySelector(".fchart-focus");
        const tip = figure.querySelector(".fchart-tip");
        const [tipValue, tipWhen, tipChange] = tip.children;
        const step = figure.dataset.step;
        const points = [...figure.querySelectorAll("tbody tr[data-x]")].map((row) => ({
            x: Number(row.dataset.x),
            y: Number(row.dataset.y),
            cells: [...row.cells].map((cell) => cell.textContent.trim()),
        }));
        if (points.length < 2) return;
        let current = -1;

        function show(index) {
            current = index;
            const p = points[index];
            for (const el of [cross, focus]) el.style.setProperty("--x", `${p.x}%`);
            focus.style.setProperty("--y", `${p.y}%`);
            tipValue.textContent = p.cells[1];
            tipWhen.textContent = `${p.cells[0]} UTC`;
            tipChange.textContent = p.cells[2] ? `${p.cells[2]} since previous ${step}` : "Start of the period";
            cross.hidden = focus.hidden = tip.hidden = false;
            // Keep the tooltip inside the plot: centred on the point, pinned
            // to an edge near the ends.
            const width = plot.clientWidth;
            const half = tip.offsetWidth / 2;
            const left = Math.min(Math.max(p.x / 100 * width, half), width - half);
            tip.style.left = `${left}px`;
            tip.dataset.below = p.y < 35 ? "1" : "";
        }

        function hide() {
            current = -1;
            cross.hidden = focus.hidden = tip.hidden = true;
        }

        function nearest(clientX) {
            const box = plot.getBoundingClientRect();
            const x = (clientX - box.left) / box.width * 100;
            let best = 0;
            points.forEach((p, i) => {
                if (Math.abs(p.x - x) < Math.abs(points[best].x - x)) best = i;
            });
            return best;
        }

        plot.addEventListener("pointermove", (event) => show(nearest(event.clientX)));
        plot.addEventListener("pointerdown", (event) => show(nearest(event.clientX)));
        plot.addEventListener("pointerleave", (event) => {
            if (event.pointerType === "mouse") hide();
        });
        document.addEventListener("pointerdown", (event) => {
            if (!plot.contains(event.target) && document.activeElement !== plot) hide();
        });
        plot.addEventListener("focus", () => show(points.length - 1));
        plot.addEventListener("blur", hide);
        plot.addEventListener("keydown", (event) => {
            const moves = {
                ArrowLeft: current - 1,
                ArrowRight: current + 1,
                Home: 0,
                End: points.length - 1,
            };
            if (!(event.key in moves)) return;
            event.preventDefault();
            show(Math.min(Math.max(moves[event.key], 0), points.length - 1));
        });
    }

    document.querySelectorAll("figure[data-fchart]").forEach(setup);
})();
