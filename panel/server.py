#!/usr/bin/env python3
import argparse
import base64
import binascii
import contextlib
import glob
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import termios
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


HERE = Path(__file__).resolve().parent
DEFAULTS = {
    "power": True,
    "linked": False, "network": True, "disk": True, "cpu": True, "offline": True,
    "speed": 3, "groups": 5, "brightness": 5,
    "speedMin": 2, "speedMax": 9, "networkMin": 0, "networkMax": 100,
    "lifeMin": 8, "lifeMax": 80, "diskMin": 0, "diskMax": 100,
    "brightMin": 2, "brightMax": 12, "cpuMin": 0, "cpuMax": 100,
    "orientation": 5, "reverse": False, "interface": "", "mount": "",
}
BOUNDS = {
    "speed": (1, 10), "speedMin": (1, 10), "speedMax": (1, 10),
    "brightness": (0, 15), "brightMin": (0, 15), "brightMax": (0, 15),
    "groups": (1, 20), "lifeMin": (1, 500), "lifeMax": (1, 500),
    "networkMin": (0, 100000), "networkMax": (0, 100000),
    "diskMin": (0, 100), "diskMax": (0, 100),
    "cpuMin": (0, 100), "cpuMax": (0, 100),
    "orientation": (0, 7),
}


def checked(settings):
    if not isinstance(settings, dict) or settings.keys() != DEFAULTS.keys():
        raise ValueError("设置字段不完整")
    for key, default in DEFAULTS.items():
        if type(settings[key]) is not type(default):
            raise ValueError(f"字段类型有误：{key}")
    for key, (low, high) in BOUNDS.items():
        if not low <= settings[key] <= high:
            raise ValueError(f"数值超出范围：{key}")
    for key in ("network", "disk", "cpu"):
        if settings[key + "Min"] >= settings[key + "Max"]:
            raise ValueError("指标上限必须大于下限")
    for key in ("speed", "life", "bright"):
        if settings[key + "Min"] > settings[key + "Max"]:
            raise ValueError("输出上限必须大于或等于下限")
    if not re.fullmatch(r"[a-zA-Z0-9_.:@-]{0,64}", settings["interface"]):
        raise ValueError("网卡名称无效")
    mount = settings["mount"]
    if mount and (len(mount) > 512 or not mount.startswith("/") or "\0" in mount):
        raise ValueError("请输入存储卷的绝对路径")
    return settings


def load(path):
    if not path.exists():
        return DEFAULTS.copy()
    values = json.loads(path.read_text())
    if not isinstance(values, dict):
        raise ValueError("设置文件格式有误")
    return checked({**DEFAULTS, **values})


def store(path, settings):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as temp:
        try:
            json.dump(settings, temp, ensure_ascii=False, indent=2)
            temp.write("\n")
            temp.flush()
            os.fsync(temp.fileno())
            os.replace(temp.name, path)
        finally:
            Path(temp.name).unlink(missing_ok=True)


def scaled(value, lower, upper, output_low, output_high):
    ratio = max(0, min(1, (value - lower) / (upper - lower)))
    return round(output_low + ratio * (output_high - output_low))


def effective(settings, metrics, dark=False):
    speed, brightness, groups = (settings[key] for key in ("speed", "brightness", "groups"))
    life = 0
    if settings["linked"]:
        if settings["network"] and metrics["network"] is not None:
            speed = scaled(metrics["network"], settings["networkMin"], settings["networkMax"],
                           settings["speedMin"], settings["speedMax"])
        if settings["cpu"] and metrics["cpu"] is not None:
            brightness = scaled(metrics["cpu"], settings["cpuMin"], settings["cpuMax"],
                                settings["brightMin"], settings["brightMax"])
        if settings["disk"] and metrics["disk"] is not None:
            life = scaled(metrics["disk"], settings["diskMin"], settings["diskMax"],
                          settings["lifeMin"], settings["lifeMax"])
    return {"speed": speed, "brightness": brightness, "groups": groups,
            "life": life,
            "dark": not settings["power"] or bool(dark and settings["linked"] and settings["offline"])}


def mounts():
    paths = set()
    with contextlib.suppress(OSError):
        for line in Path("/proc/self/mountinfo").read_text().splitlines():
            path = line.split()[4]
            path = re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), path)
            if re.fullmatch(r"/(?:vol|volume)\d+", path):
                paths.add(path)
    return sorted(paths) or ["/"]


def default_interface(counters, network_dir=Path("/proc/net")):
    routes = []
    with contextlib.suppress(OSError, ValueError):
        for line in (network_dir / "route").read_text().splitlines()[1:]:
            name, destination, _, flags, _, _, metric, *_ = line.split()
            if destination == "00000000" and int(flags, 16) & 1 and name in counters:
                routes.append((int(metric), name))
    return min(routes)[1] if routes else next(iter(counters), "")


class Sampler:
    def __init__(self, network_dir="/proc/net"):
        self.network_dir = Path(network_dir)
        self.previous_cpu = None
        self.previous_rx_tx = {}
        self.previous_time = None

    def read(self, settings):
        cpu = network = disk = None
        counters = {}
        selected_mounts = mounts() if sys.platform.startswith("linux") else []
        mount = settings["mount"] or next(iter(selected_mounts), "")
        if sys.platform.startswith("linux"):
            with contextlib.suppress(OSError, ValueError):
                fields = [int(f) for f in Path("/proc/stat").read_text().splitlines()[0].split()[1:9]]
                current_cpu = sum(fields), sum(fields[3:5])
                if self.previous_cpu:
                    total = current_cpu[0] - self.previous_cpu[0]
                    idle = current_cpu[1] - self.previous_cpu[1]
                    if total > 0:
                        cpu = round(max(0, min(100, 100 * (total - idle) / total)), 1)
                self.previous_cpu = current_cpu
            with contextlib.suppress(OSError, ValueError):
                for line in (self.network_dir / "dev").read_text().splitlines()[2:]:
                    name, values = line.split(":", 1)
                    parts = values.split()
                    if name.strip() != "lo":
                        counters[name.strip()] = int(parts[0]) + int(parts[8])
            with contextlib.suppress(OSError, ValueError, ZeroDivisionError):
                if mount == "/" or os.path.ismount(mount):
                    usage = shutil.disk_usage(mount)
                    disk = round(100 * usage.used / usage.total, 1)
        interface = settings["interface"] or default_interface(counters, self.network_dir)
        now = time.monotonic()
        if self.previous_time is not None and interface in counters and interface in self.previous_rx_tx:
            difference = counters[interface] - self.previous_rx_tx[interface]
            if difference >= 0:
                network = round(difference / (now - self.previous_time) / 1_000_000, 3)
        self.previous_rx_tx, self.previous_time = counters, now
        return {"cpu": cpu, "network": network, "disk": disk, "interface": interface,
                "mount": mount, "interfaces": sorted(counters), "mounts": selected_mounts}


def serial_path(explicit):
    if explicit:
        return explicit
    candidates = glob.glob("/dev/serial/by-id/*")
    if sys.platform == "darwin":
        for pattern in ("SLAB_USBtoUART*", "usbserial*", "wchusbserial*"):
            candidates.extend(glob.glob("/dev/cu." + pattern))
    matches = sorted({path for path in candidates if re.search(
        r"cp210|usb[-_.]?to[-_.]?uart|usb[-_.]?serial|ch34|ch91|silicon.labs", path, re.I)})
    return matches[0] if len(matches) == 1 else None


class Link:
    def __init__(self, path):
        self.path = path
        self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        self.buffer = bytearray()
        try:
            attrs = termios.tcgetattr(self.fd)
            attrs[0] = attrs[1] = attrs[3] = 0
            attrs[2] = termios.CS8 | termios.CLOCAL | termios.CREAD
            attrs[4] = attrs[5] = termios.B115200
            attrs[6][termios.VMIN] = attrs[6][termios.VTIME] = 0
            termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
            termios.tcflush(self.fd, termios.TCIOFLUSH)
        except (OSError, termios.error):
            self.close()
            raise

    def close(self):
        os.close(self.fd)

    def send(self, command):
        try:
            data = (command + "\n").encode("ascii")
            if os.write(self.fd, data) != len(data):
                raise OSError("串口写入不完整")
            return True
        except BlockingIOError:
            return False

    def read(self):
        with contextlib.suppress(BlockingIOError):
            self.buffer.extend(os.read(self.fd, 8192))
        lines = self.buffer.split(b"\n")
        self.buffer = bytearray(lines.pop()[-512:])
        return [line.strip().decode("ascii", errors="replace") for line in lines]


FRAME = re.compile(r"F LIFE1 (\d{1,10}) ([1-9]|10) ([0-9]|1[0-5]) ([01]) ([0-9a-f]{64})")


class Controller:
    def __init__(self, path, port, demo):
        self.path, self.port, self.demo = path, port, demo
        self.saved = load(path)
        self.preview = self.saved
        self.has_saved = path.exists()
        self.demo_values = {"cpu": 35, "disk": 55, "network": 12, "offline": False}
        self.lock = threading.Lock()
        self.reseed = False
        self.status = {}
        self.calibrating = False

    def work(self):
        sampler, connection = Sampler(), None
        next_open = next_sample = next_command = 0
        last_frame = last_sync = 0
        connection_time = 0
        frame = ack = None
        metrics = {"cpu": None, "disk": None, "network": None, "interface": "", "mount": "",
                   "interfaces": [], "mounts": []}
        error = ""
        while True:
            now = time.monotonic()
            with self.lock:
                settings = self.preview
                saved = self.saved
                has_saved = self.has_saved
                demo = self.demo_values.copy()
                reseed = self.reseed
                calibrating = self.calibrating
            if now >= next_sample:
                metrics = sampler.read(settings)
                next_sample = now + 1
            active_metrics = {**metrics, **demo} if self.demo else metrics
            output = effective(settings, active_metrics, self.demo and demo["offline"])
            p = "P {} {} {} {} {} {}".format(
                saved["speed"], saved["brightness"], saved["groups"],
                int(not saved["power"] or (saved["linked"] and saved["offline"])),
                saved["orientation"], int(saved["reverse"]))
            if now >= next_open and connection is None and not self.demo:
                next_open = now + 2
                device = serial_path(self.port)
                error = ""
                if device:
                    try:
                        connection = Link(device)
                        connection_time = now
                        last_frame = 0
                        ack = None
                    except (OSError, termios.error) as exc:
                        error = str(exc)
            try:
                if connection:
                    for line in connection.read():
                        match = FRAME.fullmatch(line)
                        if match:
                            generation, speed, bright, dark, rows = match.groups()
                            frame = {"generation": int(generation), "speed": int(speed),
                                     "brightness": int(bright), "dark": bool(int(dark)), "rows": rows}
                            last_frame = now
                        elif line.startswith("ACK LIFE1 "):
                            ack = line[10:]
                    connected = now - last_frame < 3 and frame is not None
                    if not connected and now - max(connection_time, last_frame) > 6:
                        raise OSError("串口没有收到 LIFE1 固件的回应")
                    if connected and now >= next_command:
                        next_command = now + 0.25
                        c = "C {} {} {} {} {} {} {}".format(
                            output["speed"], output["brightness"], output["groups"], output["life"],
                            int(output["dark"]), settings["orientation"], int(settings["reverse"]))
                        connection.send(c)
                        connection.send(f"T {int(calibrating)}")
                        if has_saved and p != ack and now - last_sync >= 1:
                            if connection.send(p):
                                last_sync = now
                        if reseed and connection.send("R"):
                            with self.lock:
                                self.reseed = False
            except OSError as exc:
                error = str(exc)
                connection.close()
                connection, frame = None, None
                next_open = now + 2
            connected = bool(connection and frame and now - last_frame < 3)
            with self.lock:
                self.status = {"connected": connected, "device": connection.path if connection else "",
                               "error": error, "metrics": active_metrics, "effective": output,
                               "frame": frame if connected else None,
                               "boardSaved": connected and has_saved and p == ack,
                               "calibrating": calibrating}
            time.sleep(0.05)


def handler_for(controller):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, value, mime="application/json; charset=utf-8"):
            body = json.dumps(value, ensure_ascii=False).encode() if isinstance(value, dict) else value
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(body)

        def authorize(self):
            if self.headers.get("Host") not in self.server.allowed_hosts:
                self.reply(403, {"error": "请用配置的面板地址访问"})
                return False
            if self.server.credentials is not None:
                scheme, _, encoded = self.headers.get("Authorization", "").partition(" ")
                if scheme.lower() == "basic":
                    with contextlib.suppress(binascii.Error, ValueError):
                        if hmac.compare_digest(base64.b64decode(encoded, validate=True), self.server.credentials):
                            return True
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="Life Matrix"')
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return False
            return True

        def do_GET(self):
            if not self.authorize():
                return
            path = urlsplit(self.path).path
            if path == "/api/state":
                with controller.lock:
                    value = {**controller.status, "settings": controller.preview, "saved": controller.saved,
                             "demo": controller.demo, "demoValues": controller.demo_values}
                self.reply(200, value)
                return
            files = {"/": ("index.html", "text/html"), "/panel.js": ("panel.js", "text/javascript"),
                     "/panel.css": ("panel.css", "text/css")}
            if path in files:
                name, mime = files[path]
                self.reply(200, (HERE / name).read_bytes(), mime + "; charset=utf-8")
            else:
                self.reply(404, {"error": "页面不存在"})

        def do_POST(self):
            if not self.authorize():
                return
            if self.headers.get("Origin") not in (None, "http://" + self.headers["Host"]) or \
                    self.headers.get("Content-Type") != "application/json":
                self.reply(403, {"error": "无法验证请求来源"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 8192:
                    raise ValueError("请求大小无效")
                data = json.loads(self.rfile.read(size))
                path = urlsplit(self.path).path
                with controller.lock:
                    if path == "/api/preview":
                        controller.preview = checked(data)
                    elif path == "/api/save":
                        settings = checked(data)
                        store(controller.path, settings)
                        controller.preview = controller.saved = settings
                        controller.has_saved = True
                    elif path == "/api/discard":
                        controller.preview = controller.saved
                    elif path == "/api/reseed":
                        if not controller.status.get("connected") and not controller.demo:
                            raise ValueError("请先连接 ESP32")
                        controller.reseed = True
                    elif path == "/api/calibration":
                        if type(data) is not bool or not controller.status.get("connected"):
                            raise ValueError("请先连接 ESP32")
                        controller.calibrating = data
                    elif path == "/api/demo" and controller.demo:
                        if not isinstance(data, dict) or data.keys() != controller.demo_values.keys():
                            raise ValueError("模拟数据有误")
                        if type(data["offline"]) is not bool or any(
                            type(data[key]) is not int or not 0 <= data[key] <= limit
                            for key, limit in (("cpu", 100), ("disk", 100), ("network", 1000))
                        ):
                            raise ValueError("模拟数据超出范围")
                        controller.demo_values = data
                    else:
                        self.reply(404, {"error": "操作不存在"})
                        return
                self.reply(200, {"ok": True})
            except (ValueError, UnicodeError) as exc:
                self.reply(400, {"error": str(exc)})
            except OSError as exc:
                self.reply(500, {"error": str(exc)})

    return Handler


def main():
    parser = argparse.ArgumentParser(description="飞牛生命游戏灯阵控制面板")
    parser.add_argument("--device", help="ESP32 USB 串口路径")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--forward-port", type=int, help="浏览器使用的本地 SSH 转发端口")
    parser.add_argument("--lan-address", type=ipaddress.IPv4Address, help="NAS 的私有局域网 IPv4 地址")
    parser.add_argument("--lan-password-file", type=Path, help="内含面板访问密码的文件")
    parser.add_argument("--settings", type=Path, default=HERE.parent / "settings.json")
    parser.add_argument("--demo", action="store_true", help="模拟飞牛指标，使用独立设置")
    args = parser.parse_args()
    if bool(args.lan_address) != bool(args.lan_password_file):
        parser.error("--lan-address 和 --lan-password-file 必须同时提供")
    address = "127.0.0.1"
    credentials = None
    if args.lan_address:
        if not any(args.lan_address in ipaddress.IPv4Network(network) for network in
                   ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")):
            parser.error("--lan-address 必须是私有局域网 IPv4 地址")
        if args.forward_port:
            parser.error("局域网模式请直接访问配置的局域网地址")
        address = str(args.lan_address)
        try:
            password = args.lan_password_file.read_bytes().rstrip(b"\r\n")
        except OSError as exc:
            parser.error(str(exc))
        if len(password) < 16 or not all(33 <= byte <= 126 for byte in password):
            parser.error("面板密码至少 16 位，请使用可打印的 ASCII 字符（不含空格）")
        credentials = b"matrix:" + password
    path = args.settings
    if args.demo:
        path = path.with_name(path.stem + ".demo" + path.suffix)
    controller = Controller(path, args.device, args.demo)
    with ThreadingHTTPServer((address, args.port), handler_for(controller)) as server:
        ports = {server.server_port, args.forward_port or server.server_port}
        hosts = (address, "localhost") if credentials is None else (address,)
        server.allowed_hosts = {f"{host}:{port}" if port != 80 else host for host in hosts for port in ports}
        server.credentials = credentials
        threading.Thread(target=controller.work, daemon=True).start()
        print(f"http://{address}:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
