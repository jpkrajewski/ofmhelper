// Self links page: the scope picker navigates to its chosen URL, and the
// activity line chart gets a hover tooltip (dots and line are drawn
// server-side in self_links.html).
(() => {
    const scope = document.getElementById("self-scope");
    if (scope) {
        scope.addEventListener("change", () => {
            window.location.href = scope.value;
        });
    }

    const chart = document.getElementById("self-chart");
    if (!chart) return;
    const plot = chart.querySelector(".self-chart-plot");
    const tip = document.getElementById("self-chart-tip");
    const dots = Array.from(chart.querySelectorAll(".self-chart-dot"));
    if (!plot || !tip || dots.length === 0) return;

    const xOf = (dot) => Number.parseFloat(dot.style.getPropertyValue("--x")) || 0;
    const n = (value) => Number(value).toLocaleString();

    const show = (dot) => {
        for (const d of dots) d.classList.toggle("on", d === dot);
        tip.hidden = false;
        tip.textContent = `${dot.dataset.label} · ${n(dot.dataset.views)} views · ${n(dot.dataset.clicks)} clicks`;
        tip.style.left = dot.style.getPropertyValue("--x");
        tip.style.top = dot.style.getPropertyValue("--y");
    };

    const hide = () => {
        for (const d of dots) d.classList.remove("on");
        tip.hidden = true;
    };

    plot.addEventListener("pointermove", (event) => {
        const rect = plot.getBoundingClientRect();
        const x = ((event.clientX - rect.left) / rect.width) * 100;
        let nearest = dots[0];
        let best = Number.POSITIVE_INFINITY;
        for (const dot of dots) {
            const gap = Math.abs(xOf(dot) - x);
            if (gap < best) {
                best = gap;
                nearest = dot;
            }
        }
        show(nearest);
    });
    plot.addEventListener("pointerleave", hide);
})();
