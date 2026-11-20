(() => {
  if (document.querySelector("[data-refresh]")) {
    setTimeout(() => location.reload(), 5000);
  }
})();
