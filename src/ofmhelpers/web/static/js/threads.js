// /threads/{login}: while a reel's pictures are being made (`data-refresh`
// on the grid) the page reloads itself every few seconds, so a finished
// one shows its pictures without a click.
(() => {
  if (document.querySelector("[data-refresh]")) {
    setTimeout(() => location.reload(), 5000);
  }
})();
