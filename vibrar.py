"""Vibración de una H59MAX por Bluetooth LE (protocolo Yawell / QWatch Pro).

La banda solo acepta una conexión a la vez. Desconectala de la app del
teléfono antes de correr este script.
"""

import argparse
import asyncio

from bleak import BleakClient, BleakScanner

RX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
TX_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
DEFAULT_ADDRESS = "32:31:42:32:BC:02"
DEFAULT_NAME = "H59MAX_BC02"


def packet(payload: bytes) -> bytes:
    """Arma un paquete de 16 bytes. El último byte es la suma de los anteriores."""
    buf = bytearray(16)
    buf[: len(payload)] = payload
    buf[15] = sum(buf[:15]) & 0xFF
    return bytes(buf)


# Find Device: el mismo comando que usa la app. Vibra y se apaga solo.
FIND = packet(bytes([0x50, 0x55, 0xAA]))
# Un zumbido corto, el de una notificación.
SHORT = packet(bytes([0x72, 0x01, 0x01, 0x01]))
# Zumbido largo, el de una llamada entrante. Se corta con STOP.
LONG = packet(bytes([0x72, 0x00, 0x01, 0x01]))
STOP = packet(bytes([0x72, 0x04, 0x01, 0x01]))
# Apaga la medición periódica de pulso, oxígeno, estrés y HRV.
SENSORS_OFF = [
    packet(bytes([0x16, 0x02, 0x02, 0x00])),
    packet(bytes([0x2C, 0x02, 0x00])),
    packet(bytes([0x36, 0x02, 0x00])),
    packet(bytes([0x38, 0x02, 0x00])),
]

COMMANDS = {
    "find": [FIND],
    "short": [SHORT],
    "long": [LONG],
    "stop": [STOP],
}


def on_notify(_char, data: bytearray) -> None:
    print("respuesta:", data.hex(" "))


async def locate(address: str, name: str):
    print(f"buscando {name} ({address})...")

    def matches(device, _adv) -> bool:
        device_name = device.name or ""
        return device.address.upper() == address.upper() or device_name.upper().startswith("H59MAX")

    device = await BleakScanner.find_device_by_filter(matches, timeout=12)
    if device is None:
        raise SystemExit(
            "No apareció la banda. Desconectala de la app del teléfono, "
            "acercala a la PC y volvé a intentar."
        )
    print(f"encontrada: {device.name} {device.address}")
    return device


async def send(address: str, name: str, commands: list[bytes]) -> None:
    device = await locate(address, name)
    async with BleakClient(device) as client:
        await client.start_notify(TX_UUID, on_notify)
        char = client.services.get_characteristic(RX_UUID)
        if char is None:
            raise SystemExit("La banda no tiene la característica de escritura esperada.")
        use_response = "write" in char.properties and "write-without-response" not in char.properties
        for command in commands:
            print("enviando:", command.hex(" "))
            await client.write_gatt_char(RX_UUID, command, response=use_response)
            await asyncio.sleep(0.3)
        await asyncio.sleep(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Vibrar una H59MAX por BLE")
    parser.add_argument("--address", default=DEFAULT_ADDRESS)
    parser.add_argument("--name", default=DEFAULT_NAME)
    parser.add_argument(
        "action",
        nargs="?",
        choices=["find", "short", "long", "stop"],
        default="find",
        help="find vibra como Buscar dispositivo; short es un zumbido; long es una llamada; stop la corta",
    )
    parser.add_argument(
        "--sensors-off",
        action="store_true",
        help="apaga la medición periódica de pulso, oxígeno, estrés y HRV",
    )
    args = parser.parse_args()
    commands = list(COMMANDS[args.action])
    if args.sensors_off:
        commands = SENSORS_OFF + commands
    asyncio.run(send(args.address, args.name, commands))


if __name__ == "__main__":
    main()
