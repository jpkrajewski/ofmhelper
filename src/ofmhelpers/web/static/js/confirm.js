(() => {
    document.addEventListener("submit", (e) => {
        const form = e.target;
        if (!(form instanceof HTMLFormElement)) return;
        const question = form.dataset.confirm;
        if (question && !window.confirm(question)) e.preventDefault();
    });
})();
