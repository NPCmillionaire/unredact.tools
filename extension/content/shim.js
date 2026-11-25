// Runs in the page's own world at document_start. Many apps open the file picker on an <input>
// that was never added to the page, so its events never reach a listener. Park such inputs in a
// hidden holder first so the upload check can see them. Nothing else about the input changes.
(() => {
  if (window.__unredactShim) return;
  Object.defineProperty(window, "__unredactShim", { value: true });
  const holder = () => {
    let h = document.getElementById("__unredact_holder");
    if (!h && document.documentElement) {
      h = document.createElement("div");
      h.id = "__unredact_holder";
      h.hidden = true;
      h.style.display = "none";
      document.documentElement.appendChild(h);
    }
    return h;
  };
  const adopt = (el) => {
    if (el instanceof HTMLInputElement && el.type === "file" && !el.isConnected) holder()?.appendChild(el);
  };
  const proto = HTMLInputElement.prototype;
  const click = proto.click;
  proto.click = function () { adopt(this); return click.apply(this, arguments); };
  if (proto.showPicker) {
    const show = proto.showPicker;
    proto.showPicker = function () { adopt(this); return show.apply(this, arguments); };
  }
})();
