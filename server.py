"""Sirve la página y mantiene conectadas las H59MAX por su dirección.

Chrome no puede reconectar solo: hay que pasar por el selector. Este proceso
sí puede, porque habla con el Bluetooth de Windows directo.
"""

import asyncio
import json
import os
import socket
import subprocess
import threading
import time
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from bleak import BleakClient, BleakScanner

ROOT = Path(__file__).resolve().parent
SIDES_PATH = ROOT / "bandas.json"
RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
PORT = 8000

START = bytes([0x72, 0x00, 0x01, 0x01])
STOP = bytes([0x72, 0x04, 0x01, 0x01])
TAP = bytes([0x72, 0x01, 0x01, 0x01])
FIND = bytes([0x50, 0x55, 0xAA])
BATTERY = bytes([0x03])
# Se mandan al conectar. El primero corta una medición en curso; el resto apaga las automáticas.
SENSOR_OFF = (
    bytes([0x1E, 0x02]),
    bytes([0x16, 0x02, 0x02, 0x00]),
    bytes([0x2C, 0x02, 0x00]),
    bytes([0x36, 0x02, 0x00]),
    bytes([0x38, 0x02, 0x00]),
)

loop = asyncio.new_event_loop()
radio = asyncio.Lock()
sides = {"left": None, "right": None}
links = {}
seen = {}
known = {}
tasks = {"left": None, "right": None}


def packet(payload: bytes) -> bytes:
    buf = bytearray(16)
    buf[: len(payload)] = payload
    buf[15] = sum(buf[:15]) & 0xFF
    return bytes(buf)


def load_sides() -> None:
    if not SIDES_PATH.exists():
        return
    try:
        saved = json.loads(SIDES_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    for side in ("left", "right"):
        value = saved.get(side)
        if isinstance(value, dict) and value.get("address"):
            sides[side] = {"address": value["address"].upper(), "name": value.get("name") or "H59MAX"}


def save_sides() -> None:
    SIDES_PATH.write_text(json.dumps(sides, indent=2), encoding="utf-8")


def side_of(address: str):
    address = address.upper()
    for side, slot in sides.items():
        if slot and slot["address"] == address:
            return side
    return None


def on_notify(address: str, data: bytearray) -> None:
    if len(data) != 16 or (sum(data[:15]) & 0xFF) != data[15]:
        return
    link = links.get(address)
    if link is None:
        return
    if data[0] == 0x03 and data[1] <= 100:
        link["battery"] = data[1]
        link["charging"] = data[2] == 1
    elif data[0] == 0x73 and data[1] == 0x0C and data[2] <= 100:
        link["battery"] = data[2]
        link["charging"] = data[3] == 1


async def write(address: str, payload: bytes) -> None:
    link = links.get(address)
    if not link or not link["client"] or not link["client"].is_connected:
        raise RuntimeError("la banda no está conectada")
    char = link["rx"]
    response = "write" in char.properties and "write-without-response" not in char.properties
    await link["client"].write_gatt_char(char, packet(payload), response=response)


def mark_dropped(address: str, client: BleakClient) -> None:
    link = links.get(address)
    if link and link["client"] is client:
        link["connected"] = False


async def connect_device(device, name: str) -> None:
    address = device.address.upper()
    current = links.get(address)
    if current and current["client"] and current["client"].is_connected:
        return
    client = BleakClient(device, disconnected_callback=lambda ble: mark_dropped(address, ble))
    await client.connect()
    rx = client.services.get_characteristic(RX)
    if rx is None:
        await client.disconnect()
        raise RuntimeError("la banda no tiene el canal de escritura")
    await client.start_notify(TX, lambda _char, data: on_notify(address, data))
    links[address] = {
        "client": client,
        "rx": rx,
        "name": name,
        "battery": None,
        "charging": False,
        "connected": True,
        "error": "",
    }
    slot = sides.get(side_of(address) or "")
    if slot:
        slot["name"] = name
        save_sides()
    await write(address, BATTERY)
    for payload in SENSOR_OFF:
        await write(address, payload)
        await asyncio.sleep(0.15)


async def maintain() -> None:
    while True:
        try:
            async with radio:
                found = await BleakScanner.discover(timeout=4, return_adv=True)
            fresh = {}
            for device, adv in found.values():
                name = device.name or adv.local_name or ""
                if "H59" not in name.upper():
                    continue
                fresh[device.address.upper()] = {"device": device, "name": name}
            seen.clear()
            seen.update(fresh)
            now = time.monotonic()
            for address, item in fresh.items():
                known[address] = {"name": item["name"], "device": item["device"], "last": now}
            if fresh:
                print("vistas:", ", ".join(item["name"] for item in fresh.values()))
            for address, item in fresh.items():
                if side_of(address) is None:
                    continue
                link = links.get(address)
                if link and link["connected"] and link["client"].is_connected:
                    continue
                try:
                    async with radio:
                        await connect_device(item["device"], item["name"])
                except Exception as error:
                    link = links.setdefault(address, {
                        "client": None,
                        "rx": None,
                        "name": item["name"],
                        "battery": None,
                        "charging": False,
                        "connected": False,
                        "error": "",
                    })
                    link["connected"] = False
                    link["error"] = str(error)
        except Exception as error:
            print("busqueda:", error)
        await asyncio.sleep(2)


def slot_view(side: str):
    slot = sides[side]
    if not slot:
        return None
    link = links.get(slot["address"])
    connected = bool(link and link["connected"] and link["client"] and link["client"].is_connected)
    return {
        "name": (link or {}).get("name") or slot["name"],
        "address": slot["address"],
        "connected": connected,
        "battery": None if not link else link["battery"],
        "charging": False if not link else link["charging"],
        "error": "" if connected or not link else link.get("error") or "",
    }


def snapshot() -> dict:
    assigned = {slot["address"] for slot in sides.values() if slot}
    nearby = [
        {"name": item["name"], "address": address}
        for address, item in known.items()
        if address not in assigned
    ]
    return {"left": slot_view("left"), "right": slot_view("right"), "nearby": nearby}


async def scan_now() -> dict:
    async with radio:
        found = await BleakScanner.discover(timeout=8, return_adv=True)
    now = time.monotonic()
    fresh = {}
    for device, adv in found.values():
        name = device.name or adv.local_name or ""
        if "H59" not in name.upper():
            continue
        address = device.address.upper()
        fresh[address] = {"device": device, "name": name}
        known[address] = {"name": name, "device": device, "last": now}
    seen.clear()
    seen.update(fresh)
    print("vistas:", ", ".join(item["name"] for item in fresh.values()) or "ninguna")
    return snapshot()


async def run_pulses(side: str, mode: str, pulse: int, gap: int, count: int) -> None:
    slot = sides[side]
    if not slot:
        return
    address = slot["address"]
    try:
        for index in range(count):
            if mode == "cut":
                await write(address, START)
                await asyncio.sleep(pulse / 1000)
                await write(address, STOP)
            else:
                await write(address, TAP)
            if index < count - 1:
                await asyncio.sleep(gap / 1000)
    except asyncio.CancelledError:
        try:
            await write(address, STOP)
        except Exception:
            pass
        raise
    except Exception as error:
        print(side, error)


def start_pulses(side: str, mode: str, pulse: int, gap: int, count: int) -> None:
    current = tasks[side]
    if current and not current.done():
        current.cancel()
    tasks[side] = asyncio.create_task(run_pulses(side, mode, pulse, gap, count))


def stop_side(side: str) -> None:
    current = tasks[side]
    if current and not current.done():
        current.cancel()


async def identify(side: str) -> None:
    slot = sides[side]
    if slot:
        await write(slot["address"], FIND)


async def connect_if_seen(address: str) -> None:
    item = seen.get(address) or known.get(address)
    if not item:
        return
    link = links.get(address)
    if link and link["connected"] and link["client"] and link["client"].is_connected:
        return
    async with radio:
        await connect_device(item["device"], item["name"])


async def do_assign(side: str, address: str, name: str) -> dict:
    assign(side, address, name)
    try:
        await connect_if_seen(address.upper())
    except Exception as error:
        link = links.get(address.upper())
        if link:
            link["error"] = str(error)
    return snapshot()


async def do_forget(side: str) -> dict:
    sides[side] = None
    save_sides()
    return snapshot()


async def do_swap() -> dict:
    sides["left"], sides["right"] = sides["right"], sides["left"]
    save_sides()
    return snapshot()


async def take_snapshot() -> dict:
    return snapshot()


def assign(side: str, address: str, name: str) -> None:
    address = address.upper()
    other = "right" if side == "left" else "left"
    if sides[other] and sides[other]["address"] == address:
        sides[other] = None
    sides[side] = {"address": address, "name": name or "H59MAX"}
    save_sides()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, fmt, *args):
        if args and str(args[0]).startswith("GET /api"):
            return
        super().log_message(fmt, *args)

    def send_json(self, payload, status=200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def call(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=30)

    def do_GET(self):
        if self.path.split("?", 1)[0] == "/api/state":
            self.send_json(self.call(take_snapshot()))
            return
        super().do_GET()

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        try:
            body = self.read_json()
            if path == "/api/scan":
                self.send_json(self.call(scan_now()))
                return
            if path == "/api/assign":
                self.send_json(self.call(do_assign(body["side"], body["address"], body.get("name") or "")))
                return
            if path == "/api/forget":
                self.send_json(self.call(do_forget(body["side"])))
                return
            if path == "/api/swap":
                self.send_json(self.call(do_swap()))
                return
            if path == "/api/vibrate":
                pulse = max(40, min(1000, int(body.get("pulse", 200))))
                gap = max(40, min(1000, int(body.get("gap", 200))))
                count = max(1, min(20, int(body.get("count", 1))))
                mode = "tap" if body.get("mode") == "tap" else "cut"
                targets = ["left", "right"] if body.get("side") == "both" else [body["side"]]
                for side in targets:
                    if side in sides and sides[side]:
                        loop.call_soon_threadsafe(start_pulses, side, mode, pulse, gap, count)
                self.send_json(self.call(take_snapshot()))
                return
            if path == "/api/stop":
                targets = ["left", "right"] if body.get("side") == "both" else [body["side"]]
                for side in targets:
                    if side in tasks:
                        loop.call_soon_threadsafe(stop_side, side)
                self.send_json(self.call(take_snapshot()))
                return
            if path == "/api/identify":
                self.call(identify(body["side"]))
                self.send_json(self.call(take_snapshot()))
                return
            self.send_json({"error": "ruta desconocida"}, 404)
        except Exception as error:
            self.send_json({"error": str(error)}, 400)


class Server(ThreadingHTTPServer):
    address_family = socket.AF_INET6
    allow_reuse_address = False

    def server_bind(self):
        # Sin esto, localhost entra por IPv6 y el navegador no ve nada.
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def listening_pids(port: int) -> list[int]:
    output = subprocess.check_output(["netstat", "-ano", "-p", "tcp"], text=True, errors="replace")
    found = []
    for line in output.splitlines():
        if "LISTENING" not in line.upper():
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        port_text = parts[1].rpartition(":")[2]
        if port_text != str(port):
            continue
        try:
            found.append(int(parts[-1]))
        except ValueError:
            continue
    return found


def process_command(pid: int) -> str:
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\").CommandLine",
        ],
        capture_output=True,
        text=True,
        errors="replace",
    )
    return result.stdout or ""


def stop_previous_server() -> None:
    """El servidor anterior, si quedó suelto, no tiene ventana. Esta pasa a ser la dueña."""
    me = os.getpid()
    for pid in listening_pids(PORT):
        if pid in (0, me):
            continue
        if "server.py" not in process_command(pid):
            continue
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], check=False, capture_output=True)
    for _ in range(20):
        if not any(pid not in (0, me) for pid in listening_pids(PORT)):
            return
        time.sleep(0.25)


def main() -> None:
    stop_previous_server()
    load_sides()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    asyncio.run_coroutine_threadsafe(maintain(), loop)
    try:
        server = Server(("::", PORT), Handler)
    except OSError as error:
        in_use = getattr(error, "winerror", None) == 10048 or error.errno in (48, 98, 10048)
        if not in_use:
            raise
        print(f"El puerto {PORT} está ocupado por otro programa.")
        raise SystemExit(1)
    url = f"http://127.0.0.1:{PORT}"
    print(f"Servidor en {url}")
    print("Cerra esta ventana para detenerlo.")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
