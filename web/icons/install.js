// Older iOS web clips may reopen this install URL instead of manifest.start_url.
// Navigate into the real app only when launched in standalone mode. This page
// has no credentials, storage, API calls or changes to existing app data.
if (window.matchMedia("(display-mode: standalone)").matches || navigator.standalone) {
  window.location.replace("/");
}
