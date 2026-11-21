(() => {
    const page = document.querySelector("[data-chat-visit]");
    if (!page) return;
    fetch(page.dataset.chatVisit, { method: "POST", keepalive: true }).catch((error) => {
        console.warn("could not remember this chat", error);
    });
})();
