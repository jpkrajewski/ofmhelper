// /handmade (templates/handmade.html): Upload to Drive on a card opens /drive
// with that reel to pick its account folder (drive.js comes back here); x on
// "In Drive" takes it back out (the same route /clone's x uses).
document.addEventListener("click", async (e) => {
    const upload = e.target.closest(".drive-upload-btn");
    if (upload) {
        const card = upload.closest(".result-item[data-job-id]");
        if (card) window.location.href = `/drive?upload=${encodeURIComponent(card.dataset.jobId)}`;
        return;
    }
    const remove = e.target.closest(".drive-remove");
    if (!remove) return;
    remove.disabled = true;
    const response = await fetch(`/clone/drive-uploads/${remove.dataset.upload}/remove`, {
        method: "POST",
    }).catch(() => null);
    if (response?.ok) remove.closest("li")?.remove();
    else remove.disabled = false;
});
