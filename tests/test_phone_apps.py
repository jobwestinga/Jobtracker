"""Installation contracts served by the actual static mount, without user data."""
from html.parser import HTMLParser

from fastapi.testclient import TestClient

from server.app import app


class Shell(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.tags = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def attrs(self, tag, **match):
        return next(attrs for name, attrs in self.tags if name == tag and
                    all(attrs.get(key) == value for key, value in match.items()))


def test_two_phone_identities_metadata_and_shared_browser():
    with TestClient(app, raise_server_exceptions=True) as client:
        identities = []
        for name, product, tabs, icon in (
            ("Weight", "weight", ["weight"], "weight"),
            ("JobTracker", "jobtracker", ["goals", "today", "sessions", "graphs"], "cutout"),
        ):
            route = f"/phone/{product}/"
            response = client.get(route)
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-cache"
            shell = Shell(response.text)
            assert shell.attrs("html")["data-profile"] == product
            assert shell.attrs("meta", name="apple-mobile-web-app-title")["content"] == name
            assert shell.attrs("link", rel="apple-touch-icon")["href"] == f"/icons/{icon}-touch.png"
            assert [attrs["data-view"] for tag, attrs in shell.tags if "data-view" in attrs] == tabs
            manifest = client.get(shell.attrs("link", rel="manifest")["href"]).json()
            identities.append(manifest["id"])
            assert manifest["name"] == manifest["short_name"] == name
            assert manifest["start_url"] == route
            assert manifest["scope"] == ("/" if product == "weight" else route)
            assert manifest["icons"][0]["src"] == f"/icons/{icon}.png"
            for asset in (f"/icons/{icon}.png", f"/icons/{icon}-touch.png"):
                assert client.get(asset).content.startswith(b"\x89PNG")
            assert client.get(route + "index.html").text == response.text
            assert client.get(route.rstrip("/"), follow_redirects=False).status_code == 307
        assert identities == ["/", "/phone/jobtracker/"]
        full = Shell(client.get("/").text)
        assert [attrs["data-view"] for tag, attrs in full.tags if "data-view" in attrs] == ["goals", "today", "sessions", "graphs", "weight"]
        legacy = client.get("/manifest.webmanifest").json()
        assert legacy["id"] == "/"
        assert legacy["start_url"] == "/phone/weight/"
        assert client.get("/icons/cutout.webmanifest").json()["id"] == "/"
        # This deployment supports the origin root, not a proxy subpath. Do not
        # silently serve a wrong product under an invented prefix.
        assert client.get("/unsupported-prefix/phone/weight/").status_code == 404


def test_static_shell_does_not_weaken_authenticated_data():
    # No lifespan: static routes work without constructing a service/database.
    client = TestClient(app)
    try:
        assert client.get("/phone/").status_code == 200
        for route in ("/api/context", "/api/weight", "/api/snapshot"):
            response = client.get(route)
            assert response.status_code == 401
            assert response.headers["cache-control"] == "no-store"
    finally:
        client.close()
