// Copy-to-clipboard for the wallet addresses on /donate.
document.querySelectorAll(".wallet .copy").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const addr = btn.parentElement.querySelector(".addr").textContent.trim();
    try {
      await navigator.clipboard.writeText(addr);
      btn.textContent = "copied";
    } catch {
      btn.textContent = "select and copy";
    }
    setTimeout(() => { btn.textContent = "copy"; }, 1600);
  });
});
