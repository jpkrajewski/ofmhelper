// "Are you sure?" before a form posts: <form data-confirm="Remove x?">.
//
// The question travels as a data attribute, never inside an inline
// onsubmit: a value with a quote in it (an email, a file name) would end
// the JS string there and run the rest as script.
(() => {
    document.addEventListener("submit", (e) => {
        const form = e.target;
        if (!(form instanceof HTMLFormElement)) return;
        const question = form.dataset.confirm;
        if (question && !window.confirm(question)) e.preventDefault();
    });
})();
