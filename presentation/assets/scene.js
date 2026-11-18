// The session scene: scaled to fit the page, or opened on its own across the whole screen.
(() => {
  const wrap = document.querySelector(".scene-wrap");
  const frame = wrap.querySelector("iframe.scene");
  const W = 1440;
  const fit = () => { if (!wrap.classList.contains("full")) frame.style.transform = `scale(${wrap.clientWidth / W})`; };
  new ResizeObserver(fit).observe(wrap);
  fit();

  const open = () => {
    wrap.classList.add("full");
    document.body.classList.add("scene-open");
    frame.style.transform = "none";
    if (wrap.requestFullscreen) wrap.requestFullscreen().catch(() => {});
  };
  const close = () => {
    wrap.classList.remove("full");
    document.body.classList.remove("scene-open");
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    fit();
  };
  document.querySelector(".scene-full").addEventListener("click", open);
  wrap.querySelector(".scene-close").addEventListener("click", close);
  document.addEventListener("keydown", e => { if (e.key === "Escape" && wrap.classList.contains("full")) close(); });
  document.addEventListener("fullscreenchange", () => { if (!document.fullscreenElement && wrap.classList.contains("full")) close(); });
})();
