// Weight owns the root identity, so a Home Screen app launched at / opens
// Weight; ordinary browser / remains the full app. Product choice everywhere
// else is explicit in the URL, never screen size.
if (window.matchMedia("(display-mode: standalone)").matches || navigator.standalone) {
  window.location.replace("/phone/weight/");
}
