// Show the result of a /contact form submission (the server redirects back with ?sent or ?error=).
(() => {
  // same split as app.js: messages go straight to the server, never through the CDN
  if (location.hostname === "unredact.tools") {
    document.getElementById("form").action = "https://api.unredact.tools/api/contact";
  }
  const q = new URLSearchParams(location.search);
  const msgs = {
    off: "The contact form isn't set up on this server. Please use email instead.",
    invalid: "Please write a message, and check the email address if you gave one.",
    busy: "Too many messages from you in the last hour. Please try again later.",
    send: "Your message couldn't be sent. Please try again or use email instead.",
  };
  const box = document.getElementById("form-status");
  if (q.has("sent")) {
    box.textContent = "Thanks, your message was sent.";
    box.classList.add("ok");
  } else if (q.has("error")) {
    box.textContent = msgs[q.get("error")] || msgs.send;
    box.classList.add("bad");
  } else return;
  box.hidden = false;
  history.replaceState(null, "", location.pathname + "#form");
})();
