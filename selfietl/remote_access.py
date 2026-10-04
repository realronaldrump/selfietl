"""Authenticated HTTPS gateway for the local SelfieTL server.

The gateway listens only on loopback and is published on SelfieTL's HTTPS route.
Login links contain a short-lived, single-use ticket in the URL fragment.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

COOKIE = "__Host-selfietl_session"
SESSION_SECONDS = 180 * 86400
MAX_UPLOAD = 260 * 1024 * 1024
HOP_HEADERS = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "proxy-authenticate", "proxy-authorization"}
LOGIN_HTML = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SelfieTL</title>
<style>body{font:16px system-ui;background:#f2f3f0;color:#171917;margin:0;display:grid;min-height:100vh;place-items:center}main{box-sizing:border-box;background:white;padding:28px;border:1px solid #ddd;border-radius:12px;width:min(420px,90vw)}h1{margin:0 0 20px}input,button{box-sizing:border-box;width:100%;font:inherit;padding:12px;border-radius:6px;margin:8px 0;border:1px solid #bbb}button{background:#171917;color:white;border:0}p{line-height:1.5}#error{color:#b73320}</style>
<main><h1>SelfieTL</h1><p id="message">Enter your access code.</p><form id="form"><input name="username" value="Davis" autocomplete="username" hidden><label for="code">Access code</label><input id="code" type="password" autocomplete="current-password" required><button id="submit">Sign in</button></form><p id="error" role="alert"></p></main>
<script nonce="__NONCE__">const form=document.getElementById('form'),code=document.getElementById('code'),error=document.getElementById('error');
const basePath=__BASE_PATH__;if(basePath&&location.pathname===basePath)location.replace(basePath+'/'+location.search+location.hash);
async function login(token){document.getElementById('submit').disabled=true;try{const r=await fetch(__SESSION_PATH__,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token})});if(!r.ok)throw new Error(r.status===429?'Too many attempts. Try again in a minute.':'Access code or sign-in link not recognized.');location.replace(location.pathname+location.search)}catch(e){error.textContent=e.message;document.getElementById('submit').disabled=false}}
form.addEventListener('submit',e=>{e.preventDefault();login(code.value.trim())});const ticket=new URLSearchParams(location.hash.slice(1)).get('access');if(ticket){history.replaceState(null,'',location.pathname+location.search);document.getElementById('message').textContent='Signing in…';login(ticket)}</script></html>"""


def _encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _decode(raw: str) -> bytes:
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))


class AccessKeys:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "remote-access-key"
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, "wb") as output:
                output.write(secrets.token_bytes(32))
        self.secret = path.read_bytes()
        if len(self.secret) != 32:
            raise ValueError("Remote access key is invalid")
        path.chmod(0o600)
        self.database = directory / "remote-access.db"
        with sqlite3.connect(self.database) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS used_tickets (nonce TEXT PRIMARY KEY, expires INTEGER NOT NULL)")
        self.database.chmod(0o600)
        self.code_path = directory / "remote-access-code"

    def issue(self, kind: str, lifetime: int) -> str:
        payload = _encode(json.dumps({"kind": kind, "exp": int(time.time()) + lifetime, "nonce": secrets.token_urlsafe(24)}, separators=(",", ":")).encode())
        return payload + "." + _encode(hmac.digest(self.secret, payload.encode(), "sha256"))

    def owner_code(self) -> str:
        if self.code_path.exists():
            code = self.code_path.read_text().strip()
            if not code:
                raise ValueError("Remote access code is empty")
            return code
        return _encode(hmac.digest(self.secret, b"SelfieTL owner access", "sha256"))

    def verify(self, token: str, kind: str) -> dict | None:
        try:
            if not isinstance(token, str) or len(token) > 2048:
                return None
            payload, signature = token.split(".")
            expected = hmac.digest(self.secret, payload.encode(), "sha256")
            if not hmac.compare_digest(expected, _decode(signature)):
                return None
            data = json.loads(_decode(payload))
            if data.get("kind") != kind or not isinstance(data.get("exp"), int) or data["exp"] <= time.time() or not isinstance(data.get("nonce"), str):
                return None
            return data
        except (ValueError, KeyError, TypeError, UnicodeError):
            return None

    def redeem(self, token: str) -> str | None:
        if isinstance(token, str) and hmac.compare_digest(token, self.owner_code()):
            return self.issue("session", SESSION_SECONDS)
        data = self.verify(token, "login")
        if data is None:
            return None
        try:
            with sqlite3.connect(self.database, timeout=10) as connection:
                connection.execute("DELETE FROM used_tickets WHERE expires < ?", (int(time.time()),))
                connection.execute("INSERT INTO used_tickets VALUES (?, ?)", (data["nonce"], data["exp"]))
        except sqlite3.IntegrityError:
            return None
        return self.issue("session", SESSION_SECONDS)


class GatewayServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, keys: AccessKeys, origin: str, upstream: str):
        parsed = urllib.parse.urlsplit(origin)
        if (parsed.scheme != "https" or not parsed.hostname
                or parsed.path.rstrip("/") not in ("", "/selfietl")
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("Public origin must be HTTPS with an optional /selfietl path")
        target = urllib.parse.urlsplit(upstream)
        if target.scheme != "http" or target.hostname not in ("127.0.0.1", "localhost"):
            raise ValueError("Upstream must be a local HTTP server")
        self.keys, self.upstream = keys, upstream.rstrip("/")
        self.origin = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
        self.base_path = parsed.path.rstrip("/")
        self.cookie_name = "__Secure-selfietl_session" if self.base_path else COOKIE
        self.hostname = parsed.netloc.lower()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.login_attempts: deque[float] = deque()
        self.login_lock = threading.Lock()
        super().__init__(address, GatewayHandler)

    def login_retry_after(self) -> int:
        # One owner and a short access code: cap attempts across clients too.
        with self.login_lock:
            now = time.monotonic()
            while self.login_attempts and self.login_attempts[0] <= now - 60:
                self.login_attempts.popleft()
            if len(self.login_attempts) >= 5:
                return max(1, int(self.login_attempts[0] + 60 - now) + 1)
            self.login_attempts.append(now)
            return 0

    def session_cookie(self, token: str, lifetime: int = SESSION_SECONDS) -> str:
        return f"{self.cookie_name}={token}; Path={self.base_path}/; Secure; HttpOnly; SameSite=Lax; Max-Age={lifetime}"


class GatewayHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "SelfieTL"

    def log_message(self, *_args):
        # Login credentials and signed cookies must never appear in access logs.
        pass

    def _reply(self, status: int, body: bytes, content_type="application/json", cookie: str | None = None, nonce: str | None = None, retry_after: int | None = None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self._security_headers(nonce)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        if retry_after:
            self.send_header("Retry-After", str(retry_after))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _security_headers(self, nonce=None):
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Strict-Transport-Security", "max-age=31536000")
        if nonce:
            self.send_header("Content-Security-Policy", f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'unsafe-inline'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")

    def _handle(self):
        server = self.server
        if self.headers.get("Host", "").lower() != server.hostname:
            self._reply(400, b'{"detail":"Invalid host"}')
            return
        if self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            self._reply(400, b'{"detail":"Content-Length is required"}')
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._reply(400, b'{"detail":"Invalid body length"}')
            return
        if length < 0 or length > MAX_UPLOAD:
            self.close_connection = True
            self._reply(413, b'{"detail":"Upload is too large"}')
            return
        if self.command not in ("GET", "HEAD", "OPTIONS"):
            origin = self.headers.get("Origin")
            if origin and origin != server.origin:
                self.close_connection = True
                self._reply(403, b'{"detail":"Invalid origin"}')
                return
        path = urllib.parse.urlsplit(self.path).path
        if path == "/auth/health" and self.command in ("GET", "HEAD"):
            self._reply(200, b'{"ok":true}')
            return
        if path == "/auth/session" and self.command == "POST":
            if length > 4096:
                self.close_connection = True
                self._reply(413, b'{"detail":"Invalid sign-in request"}')
                return
            retry_after = server.login_retry_after()
            if retry_after:
                self.close_connection = True
                self._reply(429, b'{"detail":"Too many sign-in attempts. Try again in a minute."}', retry_after=retry_after)
                return
            try:
                request = json.loads(self.rfile.read(length))
                session = server.keys.redeem(request.get("token", ""))
            except (ValueError, TypeError, AttributeError):
                session = None
            if session is None:
                self._reply(401, b'{"detail":"Invalid or expired sign-in link"}')
                return
            self._reply(200, b'{"ok":true}', cookie=server.session_cookie(session))
            return
        try:
            cookies = SimpleCookie(self.headers.get("Cookie", ""))
            session = cookies[server.cookie_name].value if server.cookie_name in cookies else ""
        except Exception:
            session = ""
        session_data = server.keys.verify(session, "session")
        if session_data is None:
            self.close_connection = True
            if self.command in ("GET", "HEAD") and not path.startswith(("/api/", "/assets/")) and path not in ("/sw.js", "/manifest.webmanifest"):
                nonce = secrets.token_urlsafe(18)
                html = (LOGIN_HTML.replace("__NONCE__", nonce)
                        .replace("__BASE_PATH__", json.dumps(server.base_path))
                        .replace("__SESSION_PATH__", json.dumps(server.base_path + "/auth/session")))
                self._reply(200, html.encode(), "text/html; charset=utf-8", nonce=nonce)
            else:
                self._reply(401, b'{"detail":"Sign in to SelfieTL"}')
            return
        if path == "/auth/logout" and self.command == "POST":
            self._reply(200, b'{"ok":true}', cookie=server.session_cookie("", 0))
            return
        body = self.rfile.read(length) if length else None
        headers = {name: self.headers[name] for name in ("Content-Type", "Accept", "Range", "If-None-Match", "If-Modified-Since", "Last-Event-ID") if name in self.headers}
        target = server.upstream + "/" + self.path.lstrip("/")
        request = urllib.request.Request(target, data=body, method=self.command, headers=headers)
        try:
            try:
                upstream = server.opener.open(request, timeout=90)
            except urllib.error.HTTPError as error:
                upstream = error
            with upstream:
                self.send_response(upstream.code)
                for name, value in upstream.headers.items():
                    if name.lower() not in HOP_HEADERS | {"cache-control", "set-cookie"}:
                        self.send_header(name, value)
                self._security_headers()
                if session_data["exp"] - time.time() < SESSION_SECONDS / 2:
                    renewed = server.keys.issue("session", SESSION_SECONDS)
                    self.send_header("Set-Cookie", server.session_cookie(renewed))
                chunked = upstream.headers.get("Content-Length") is None and self.command != "HEAD" and upstream.code not in (204, 304)
                if chunked:
                    self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                if self.command == "HEAD":
                    return
                while chunk := upstream.read1(65536):
                    if chunked:
                        self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                    else:
                        self.wfile.write(chunk)
                    self.wfile.flush()
                if chunked:
                    self.wfile.write(b"0\r\n\r\n")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except urllib.error.URLError:
            self._reply(502, b'{"detail":"SelfieTL is restarting. Retry shortly."}')

    do_GET = _handle
    do_HEAD = _handle
    do_POST = _handle
    do_PATCH = _handle
    do_DELETE = _handle
    do_PUT = _handle
    do_OPTIONS = _handle


def main():
    parser = argparse.ArgumentParser(description="Protected remote access to SelfieTL")
    parser.add_argument("action", choices=("serve", "issue-link", "show-code"))
    parser.add_argument("--public-origin", required=True)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--port", type=int, default=8777)
    parser.add_argument("--lifetime", type=int, default=86400)
    args = parser.parse_args()
    from selfietl.config import load_config
    keys = AccessKeys(args.data_dir or load_config().data_dir)
    if args.action == "show-code":
        print(keys.owner_code())
    elif args.action == "issue-link":
        token = keys.issue("login", min(max(args.lifetime, 60), 86400))
        print(args.public_origin.rstrip("/") + "/?action=hair#access=" + urllib.parse.quote(token))
    else:
        server = GatewayServer(("127.0.0.1", args.port), keys, args.public_origin, "http://127.0.0.1:8766")
        server.serve_forever()


if __name__ == "__main__":
    main()
