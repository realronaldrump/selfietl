import http.client
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from selfietl.remote_access import AccessKeys, COOKIE, GatewayServer


def test_login_tickets_are_signed_expiring_and_single_use(tmp_path, monkeypatch):
    keys = AccessKeys(tmp_path)
    token = keys.issue("login", 300)
    assert keys.verify(token + "bad", "login") is None
    session = keys.redeem(token)
    assert session and keys.verify(session, "session")
    assert keys.redeem(token) is None
    assert keys.redeem(keys.owner_code()) is not None
    assert keys.verify([], "session") is None
    assert keys.verify(session, "login") is None
    monkeypatch.setattr("selfietl.remote_access.time.time", lambda: 10**12)
    assert keys.verify(session, "session") is None
    assert (tmp_path / "remote-access-key").stat().st_mode & 0o777 == 0o600


def test_gateway_protects_api_media_and_proxy_preserves_ranges(tmp_path):
    class Origin(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"photo-data"
            self.send_response(206 if self.headers.get("Range") else 200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Type", "image/jpeg")
            self.end_headers(); self.wfile.write(body)
        def log_message(self, *_): pass
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Origin)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    keys = AccessKeys(tmp_path)
    server = GatewayServer(("127.0.0.1", 0), keys, "https://selfietl.example.com", f"http://127.0.0.1:{upstream.server_port}")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    def request(method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        connection.request(method, path, body=body, headers={"Host": "selfietl.example.com", **(headers or {})})
        response = connection.getresponse(); content = response.read(); result=(response.status, dict(response.getheaders()), content)
        connection.close(); return result
    try:
        assert request("GET", "/api/projects")[0] == 401
        assert request("GET", "/api/photos/hash/image")[0] == 401
        assert request("GET", "/api/hair-exports/1/file")[0] == 401
        assert request("GET", "/")[0] == 200
        assert request("POST", "/api/system/reset", "{}")[0] == 401
        token = keys.issue("login", 300)
        body = json.dumps({"token": token})
        assert request("POST", "/auth/session", body, {"Origin": "https://evil.example"})[0] == 403
        status, headers, _ = request("POST", "/auth/session", body, {"Origin": "https://selfietl.example.com"})
        assert status == 200
        cookie = headers["Set-Cookie"]
        assert all(flag in cookie for flag in ("Secure", "HttpOnly", "SameSite=Lax"))
        session_cookie = cookie.split(";", 1)[0]
        status, _, content = request("GET", "/api/photos/hash/image", headers={"Cookie": session_cookie, "Range": "bytes=0-9"})
        assert status == 206 and content == b"photo-data"
        assert request("POST", "/api/capture", "{}", {"Cookie": session_cookie, "Origin": "https://evil.example"})[0] == 403
        assert request("GET", "/api/projects", headers={"Host": "evil.example.com", "Cookie": session_cookie})[0] == 400
        assert request("POST", "/auth/session", body)[0] == 401
    finally:
        server.shutdown(); server.server_close(); upstream.shutdown(); upstream.server_close()


def test_mounted_gateway_uses_its_own_login_path_cookie_and_throttle(tmp_path):
    keys = AccessKeys(tmp_path)
    keys.code_path.write_text("test-owner-code")
    server = GatewayServer(
        ("127.0.0.1", 0), keys,
        "https://selfietl.example.com:10000/selfietl/", "http://127.0.0.1:8766",
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def request(method, path, token=None, origin="https://selfietl.example.com:10000"):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        # Funnel removes the mount path before forwarding to the gateway.
        body = json.dumps({"token": token}) if token is not None else None
        connection.request(method, path, body, {
            "Host": "selfietl.example.com:10000", "Origin": origin,
        })
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    try:
        status, _, html = request("GET", "/")
        assert status == 200 and b'"/selfietl/auth/session"' in html
        assert b"/selfietl/" in html
        assert request("POST", "/auth/session", "test-owner-code", "https://other.example")[0] == 403
        status, headers, _ = request("POST", "/auth/session", "test-owner-code")
        assert status == 200
        assert headers["Set-Cookie"].startswith("__Secure-selfietl_session=")
        assert "Path=/selfietl/;" in headers["Set-Cookie"]
        assert "Domain=" not in headers["Set-Cookie"]
        for _ in range(4):
            assert request("POST", "/auth/session", "incorrect")[0] == 401
        status, headers, body = request("POST", "/auth/session", "incorrect")
        assert status == 429 and int(headers["Retry-After"]) > 0
        assert b"Too many" in body
        assert request("GET", "/api/projects")[0] == 401
    finally:
        server.shutdown()
        server.server_close()
