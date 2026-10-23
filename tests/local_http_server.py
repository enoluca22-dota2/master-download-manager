"""A small, real HTTP server for engine tests (no internet needed).

Routes (all GET/HEAD):
    /files/<name>           the registered file; honours Range + If-Range
    /cd/<name>              same file but with a Content-Disposition name
    /redirect/<name>        302 -> /files/<name>
    /norange/<name>         ignores Range, no Accept-Ranges
    /slow/<name>            Range-capable, but trickles data (for pause/cancel tests)
    /page                   an HTML page
    /torrentfile            served with Content-Type application/x-bittorrent
    /missing                404
Every request's Range header is recorded in server.range_log.
"""

import hashlib
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # keep test output quiet
        pass

    def _file(self, name):
        return self.server.files.get(name)

    def do_HEAD(self):
        self._serve(head=True)

    def do_GET(self):
        self._serve(head=False)

    def _serve(self, head):
        path = self.path.split("?", 1)[0]
        self.server.range_log.append((path, self.headers.get("Range"), self.headers.get("If-Range")))
        if path == "/page":
            body = b"<html><head><title>Hello</title></head><body>No media here</body></html>"
            return self._send(200, body, {"Content-Type": "text/html; charset=utf-8"}, head)
        if path == "/torrentfile":
            body = self.server.files.get("torrent", b"d4:infod4:name1:aee")
            return self._send(200, body, {"Content-Type": "application/x-bittorrent"}, head)
        if path == "/missing":
            return self._send(404, b"not found", {"Content-Type": "text/plain"}, head)
        parts = path.strip("/").split("/", 1)
        if len(parts) != 2:
            return self._send(404, b"", {}, head)
        route, name = parts
        data = self._file(name)
        if data is None:
            return self._send(404, b"", {}, head)
        if route == "redirect":
            self.send_response(302)
            self.send_header("Location", f"/files/{name}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        etag = '"' + hashlib.md5(data).hexdigest() + '"'
        headers = {"Content-Type": "application/octet-stream", "ETag": etag}
        if route == "cd":
            headers["Content-Disposition"] = "attachment; filename=\"fallback.bin\"; filename*=UTF-8''r%C3%A9sum%C3%A9%20file.zip"
        if route == "norange":
            return self._send(200, data, headers, head)
        headers["Accept-Ranges"] = "bytes"
        rng = self.headers.get("Range")
        if_range = self.headers.get("If-Range")
        if rng and rng.startswith("bytes=") and (not if_range or if_range == etag):
            start_s, end_s = rng[6:].split("-", 1)
            start = int(start_s)
            end = int(end_s) if end_s else len(data) - 1
            end = min(end, len(data) - 1)
            if start >= len(data):
                return self._send(416, b"", {"Content-Range": f"bytes */{len(data)}"}, head)
            headers["Content-Range"] = f"bytes {start}-{end}/{len(data)}"
            return self._send(206, data[start:end + 1], headers, head, slow=(route == "slow"))
        return self._send(200, data, headers, head, slow=(route == "slow"))

    def _send(self, code, body, headers, head, slow=False):
        self.send_response(code)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if head:
            return
        try:
            if slow:
                step = 16 * 1024
                for i in range(0, len(body), step):
                    self.wfile.write(body[i:i + step])
                    self.wfile.flush()
                    time.sleep(self.server.slow_delay)
            else:
                self.wfile.write(body)
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            pass


class LocalHTTPServer:
    def __init__(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.daemon_threads = True
        self.httpd.files = {}
        self.httpd.range_log = []
        self.httpd.slow_delay = 0.02
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()

    @property
    def base(self):
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    @property
    def files(self):
        return self.httpd.files

    @property
    def range_log(self):
        return self.httpd.range_log

    def set_slow_delay(self, seconds):
        self.httpd.slow_delay = seconds
