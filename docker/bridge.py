import contextlib
import ipaddress
import json
import os
import re
import select
import shutil
import termios
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from sampler import Link, Sampler

READ_ROUTES = {
    "/": ("index", "text/html"),
    "/panel.js": ("js", "text/javascript"),
    "/panel.css": ("css", "text/css"),
    "/api/state": ("state", "application/json"),
}
WRITE_ROUTES = {f"/api/{action}": action for action in (
    "reseed", "preview", "save", "discard", "calibration",
)}
MAX_BODY = 3500
REPLY_HEADER = re.compile(rb"A (\d+) (\d{3}) (\d+)\n")


class SerialPanel:
    def __init__(self, device):
        self.device = device
        self.lock = threading.Lock()
        self.link = None
        self.buffer = bytearray()
        self.sequence = 0

    def read_more(self, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([self.link.fd], [], [], remaining)[0]:
            raise TimeoutError("ESP32 串口响应超时")
        data = os.read(self.link.fd, 8192)
        if not data:
            raise OSError("ESP32 串口已断开")
        self.buffer.extend(data)

    def read_reply(self, deadline):
        while True:
            separator = self.buffer.find(b"\n")
            if separator == -1:
                if len(self.buffer) > 8192:
                    raise OSError("ESP32 串口响应头过长")
                self.read_more(deadline)
                continue
            match = REPLY_HEADER.fullmatch(self.buffer[:separator + 1])
            del self.buffer[:separator + 1]
            if match is None:
                continue
            sequence, code, length = map(int, match.groups())
            if length > 128 * 1024:
                raise OSError("ESP32 串口响应过长")
            while len(self.buffer) < length:
                self.read_more(deadline)
            body = bytes(self.buffer[:length])
            del self.buffer[:length]
            if sequence == self.sequence:
                return code, body

    def request(self, action, body=b""):
        with self.lock:
            try:
                if self.link is None:
                    self.link = Link(self.device)
                self.sequence = (self.sequence + 1) % 1_000_000_000
                data = memoryview(f"\nQ {self.sequence} {action} ".encode() + body + b"\n")
                deadline = time.monotonic() + 5
                while data:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not select.select([], [self.link.fd], [], remaining)[1]:
                        raise TimeoutError("ESP32 串口写入超时")
                    try:
                        data = data[os.write(self.link.fd, data):]
                    except BlockingIOError:
                        continue
                return self.read_reply(deadline)
            except (OSError, termios.error):
                if self.link:
                    self.link.close()
                    self.link = None
                self.buffer.clear()
                raise


def compact_json(value):
    return json.dumps(value, separators=(",", ":"), allow_nan=False).encode("ascii")


def collect(panel, volume):
    sampler = Sampler(os.environ.get("NAS_NET_DIR", "/proc/net"))
    previous_error = None
    while True:
        try:
            code, data = panel.request("state")
            if code != 200:
                raise ValueError(f"ESP32 state returned {code}")
            settings = json.loads(data)["settings"]
            metrics = sampler.read({"interface": settings["interface"], "mount": "/nas-volume"})
            metrics.update(mount=volume, mounts=[volume], disk=None)
            if settings["mount"] in ("", volume):
                with contextlib.suppress(OSError, ZeroDivisionError):
                    usage = shutil.disk_usage("/nas-volume")
                    metrics["disk"] = round(100 * usage.used / usage.total, 1)
            code, _ = panel.request("telemetry", compact_json(metrics))
            if code != 200:
                raise ValueError(f"ESP32 telemetry returned {code}")
            previous_error = None
        except (OSError, termios.error, ValueError, KeyError) as exc:
            if str(exc) != previous_error:
                print(f"Telemetry: {exc}", flush=True)
                previous_error = str(exc)
        time.sleep(2)


def handler(panel, host):
    class Proxy(BaseHTTPRequestHandler):
        timeout = 6

        def log_message(self, *_):
            pass

        def authorized(self):
            if self.headers.get("Host") != host:
                self.send_error(403)
                return False
            return True

        def forward(self, action, mime, payload=b""):
            try:
                code, body = panel.request(action, payload)
            except (OSError, termios.error):
                code, mime = 503, "application/json"
                body = compact_json({"error": "飞牛尚未收到 ESP32 串口响应，请检查 USB 连接"})
            self.send_response(code)
            self.send_header("Content-Type", f"{mime}; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store" if mime == "application/json" else "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(body)

        def do_GET(self):
            if not self.authorized():
                return
            route = READ_ROUTES.get(self.path)
            if route is None:
                self.send_error(404)
                return
            self.forward(*route)

        def do_POST(self):
            if not self.authorized():
                return
            action = WRITE_ROUTES.get(self.path)
            if action is None:
                self.send_error(404)
                return
            if (self.headers.get("Origin") not in (None, f"http://{host}") or
                    self.headers.get_content_type() != "application/json"):
                self.send_error(403)
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= MAX_BODY:
                    raise ValueError("invalid request length")
                payload = compact_json(json.loads(self.rfile.read(size)))
                if len(payload) > MAX_BODY:
                    raise ValueError("encoded request too large")
            except (ValueError, OSError):
                self.send_error(400)
                return
            self.forward(action, "application/json", payload)

    return Proxy


if __name__ == "__main__":
    address = str(ipaddress.IPv4Address(os.environ["NAS_IP"]))
    port = int(os.environ.get("NAS_PORT", "8765"))
    host = address if port == 80 else f"{address}:{port}"
    panel = SerialPanel(os.environ.get("ESP_DEVICE", "/dev/matrix"))
    bind_address = os.environ.get("LISTEN_IP", address)
    bind_port = int(os.environ.get("LISTEN_PORT", str(port)))
    with ThreadingHTTPServer((bind_address, bind_port), handler(panel, host)) as server:
        threading.Thread(target=collect, args=(panel, os.environ["NAS_VOLUME"]), daemon=True).start()
        print(f"http://{host}", flush=True)
        server.serve_forever()
