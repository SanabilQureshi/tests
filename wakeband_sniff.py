#!/usr/bin/env python3
"""
WakeBand BLE Protocol Sniffer

Connects to the WakeBand and logs ALL BLE traffic to help reverse-engineer
the exact command format. Run this while using the phone app simultaneously
(if the device supports multiple connections) or use it standalone to probe
commands.

Strategy for protocol discovery:
1. Run wakeband_discover.py to get service/characteristic UUIDs
2. Run this script with --probe to try common command formats
3. Analyze the responses to determine the correct byte format
4. Update wakeband_control.py with the discovered format

Requirements: pip install bleak
"""

import asyncio
import argparse
import sys
import time
from datetime import datetime
from bleak import BleakScanner, BleakClient, BleakGATTCharacteristic

DEVICE_NAME = "WakeBand"
LOG_FILE = "wakeband_ble_capture.log"


class BLESniffer:
    def __init__(self, address: str):
        self.address = address
        self.client = None
        self.log_file = None
        self.write_chars = []
        self.notify_chars = []

    def log(self, msg: str):
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{timestamp}] {msg}"
        print(line)
        if self.log_file:
            self.log_file.write(line + "\n")
            self.log_file.flush()

    def _notification_handler(self, sender: BleakGATTCharacteristic, data: bytearray):
        hex_str = " ".join(f"{b:02X}" for b in data)
        dec_str = " ".join(f"{b:3d}" for b in data)
        self.log(f"<< NOTIFY [{sender.uuid}]")
        self.log(f"   HEX: {hex_str}")
        self.log(f"   DEC: {dec_str}")
        self.log(f"   LEN: {len(data)} bytes")

        # Decode attempts
        if len(data) >= 2:
            self.log(f"   CMD: 0x{data[0]:02X} (byte[0])")
            if len(data) >= 3:
                self.log(f"   SUB: 0x{data[1]:02X} (byte[1])")

    async def connect(self):
        self.log(f"Connecting to {self.address}...")
        self.client = BleakClient(self.address, timeout=20.0)
        await self.client.connect()
        self.log(f"Connected (MTU={self.client.mtu_size})")

        # Discover and subscribe to all notify characteristics
        for service in self.client.services:
            uuid_short = service.uuid[4:8].lower()
            if uuid_short in ("1800", "1801"):
                continue

            for char in service.characteristics:
                props = char.properties
                if "write" in props or "write-without-response" in props:
                    self.write_chars.append(char)
                    self.log(f"WRITE CHAR: {char.uuid} (service: {service.uuid})")
                if "notify" in props or "indicate" in props:
                    self.notify_chars.append(char)
                    self.log(f"NOTIFY CHAR: {char.uuid} (service: {service.uuid})")
                    await self.client.start_notify(char.uuid, self._notification_handler)
                    self.log(f"  Subscribed to notifications")

    async def write_and_log(self, char_uuid: str, data: bytes, label: str = ""):
        hex_str = " ".join(f"{b:02X}" for b in data)
        self.log(f">> WRITE [{char_uuid}] {label}")
        self.log(f"   HEX: {hex_str}")
        self.log(f"   DEC: {' '.join(f'{b:3d}' for b in data)}")
        self.log(f"   LEN: {len(data)} bytes")
        try:
            await self.client.write_gatt_char(char_uuid, data, response=True)
            self.log(f"   STATUS: OK")
        except Exception as e:
            self.log(f"   STATUS: ERROR - {e}")
            # Try write without response
            try:
                await self.client.write_gatt_char(char_uuid, data, response=False)
                self.log(f"   STATUS: OK (no-response mode)")
            except Exception as e2:
                self.log(f"   STATUS: ERROR (no-response) - {e2}")
        await asyncio.sleep(1)

    async def probe_protocol(self):
        """Systematically probe the device to discover the command format."""
        if not self.write_chars:
            self.log("ERROR: No writable characteristics found!")
            return

        write_uuid = self.write_chars[0].uuid
        self.log(f"\n{'='*60}")
        self.log(f"PROBING PROTOCOL on {write_uuid}")
        self.log(f"{'='*60}\n")

        # Phase 1: Single-byte commands (find valid command IDs)
        self.log("--- Phase 1: Single-byte commands ---")
        for cmd in [0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F]:
            await self.write_and_log(write_uuid, bytes([cmd]), f"cmd=0x{cmd:02X}")

        # Phase 2: Common wearable protocol headers
        self.log("\n--- Phase 2: With 0xAA header ---")
        for cmd in [0x01, 0x02, 0x03, 0x06, 0x09]:
            await self.write_and_log(write_uuid, bytes([0xAA, cmd]), f"AA+cmd=0x{cmd:02X}")

        # Phase 3: Time sync attempts (most wearables respond to this)
        self.log("\n--- Phase 3: Time sync command formats ---")
        now = datetime.now()
        time_bytes = bytes([
            now.year - 2000, now.month, now.day,
            now.hour, now.minute, now.second,
            now.weekday() + 1
        ])

        formats = [
            (bytes([0x01]) + time_bytes, "01 + time"),
            (bytes([0xAA, 0x01]) + time_bytes, "AA 01 + time"),
            (bytes([0xAA, len(time_bytes) + 1, 0x01]) + time_bytes, "AA len 01 + time"),
        ]
        for data, label in formats:
            await self.write_and_log(write_uuid, data, label)

        # Phase 4: Battery request attempts
        self.log("\n--- Phase 4: Battery request formats ---")
        for data, label in [
            (bytes([0x06]), "cmd 06"),
            (bytes([0x03]), "cmd 03"),
            (bytes([0xAA, 0x06]), "AA 06"),
            (bytes([0xAA, 0x01, 0x06]), "AA 01 06"),
        ]:
            await self.write_and_log(write_uuid, data, label)

        # Phase 5: Vibration test attempts
        self.log("\n--- Phase 5: Vibration test formats ---")
        for data, label in [
            (bytes([0x09, 0x01, 0x05]), "09 mode=1 intensity=5"),
            (bytes([0x09, 0x00, 0x04]), "09 mode=0 intensity=4"),
            (bytes([0xAA, 0x09, 0x01, 0x05]), "AA 09 mode=1 intensity=5"),
            (bytes([0x07, 0x01, 0x05]), "07 mode=1 intensity=5"),
            (bytes([0x0A, 0x01, 0x05]), "0A mode=1 intensity=5"),
        ]:
            await self.write_and_log(write_uuid, data, label)

        # If there are multiple write characteristics, try them all
        if len(self.write_chars) > 1:
            self.log(f"\n--- Probing additional write characteristics ---")
            for wc in self.write_chars[1:]:
                self.log(f"\nUsing char: {wc.uuid}")
                await self.write_and_log(wc.uuid, bytes([0x06]), "battery request")
                await self.write_and_log(wc.uuid, bytes([0x09, 0x01, 0x05]), "vibration test")

        self.log(f"\n{'='*60}")
        self.log(f"PROBE COMPLETE - Check responses above")
        self.log(f"Log saved to {LOG_FILE}")
        self.log(f"{'='*60}")

    async def passive_sniff(self, duration: int):
        """Passively listen for notifications."""
        self.log(f"\nPassive sniffing for {duration}s...")
        self.log("Waiting for notifications from the device...\n")
        try:
            await asyncio.sleep(duration)
        except asyncio.CancelledError:
            pass

    async def disconnect(self):
        if self.client and self.client.is_connected:
            await self.client.disconnect()
            self.log("Disconnected")


async def main():
    parser = argparse.ArgumentParser(description="WakeBand BLE Protocol Sniffer")
    parser.add_argument("--address", "-a", help="Device BLE address")
    parser.add_argument("--probe", action="store_true",
                        help="Actively probe the device with common command formats")
    parser.add_argument("--duration", "-d", type=int, default=120,
                        help="Sniff duration in seconds (default: 120)")
    args = parser.parse_args()

    address = args.address
    if not address:
        print(f"Scanning for '{DEVICE_NAME}'...")
        devices = await BleakScanner.discover(timeout=10)
        for d in devices:
            if d.name and DEVICE_NAME.lower() in d.name.lower():
                address = d.address
                print(f"Found: {d.name} [{d.address}]")
                break
        if not address:
            print("No WakeBand found. Specify --address manually.")
            sys.exit(1)

    sniffer = BLESniffer(address)
    sniffer.log_file = open(LOG_FILE, "a")
    sniffer.log(f"\n{'='*60}")
    sniffer.log(f"SESSION START: {datetime.now()}")
    sniffer.log(f"{'='*60}")

    try:
        await sniffer.connect()
        if args.probe:
            await sniffer.probe_protocol()
        else:
            await sniffer.passive_sniff(args.duration)
    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        await sniffer.disconnect()
        if sniffer.log_file:
            sniffer.log_file.close()


if __name__ == "__main__":
    asyncio.run(main())
