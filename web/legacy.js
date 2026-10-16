// Existing iOS web clips may keep launching / or an old icon install page.
// Preserve their identity and storage; ordinary browser / remains the full app.
// Product choice everywhere else is explicit in the URL, never screen size.
if (window.matchMedia("(display-mode: standalone)").matches || navigator.standalone) {
  window.location.replace("/phone/weight/");
}
