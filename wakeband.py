#!/usr/bin/env python3
"""
WakeBand BLE Control Library

Control a Homedics WakeBand programmatically from Linux via BLE.

IMPORTANT: Before using this library, you MUST run wakeband_discover.py
first to discover the actual service/characteristic UUIDs for your device.
Then update the UUID constants below.

Features:
    - Set alarm time, vibration pattern (1-9), and intensity (1-9)
    - Enable/disable snooze
    - Read battery level
    - Trigger manual vibration (for testing)
    - OTA firmware update (once encryption key is known)

Requirements:
    pip install bleak

Usage as CLI:
    python3 wakeband.py scan                         # Find your WakeBand
    python3 wakeband.py discover XX:XX:XX:XX:XX:XX   # Dump GATT table
    python3 wakeband.py set-alarm HH:MM --pattern 3 --intensity 5
    python3 wakeband.py vibrate --pattern 1 --intensity 9
    python3 wakeband.py battery
    python3 wakeband.py sniff                        # Log all BLE traffic

Usage as library:
    from wakeband import WakeBand

    async with WakeBand("XX:XX:XX:XX:XX:XX") as wb:
        battery = await wb.read_battery()
        await wb.set_alarm(7, 30, pattern=3, intensity=5)
        await wb.vibrate(pattern=1, intensity=9)
"""

import argparse
import asyncio
import struct
import sys
from datetime import datetime, time
from typing import Optional

from bleak import BleakClient, BleakScanner

# ============================================================================
# UUID CONFIGURATION
#
# These UUIDs need to be discovered from YOUR WakeBand device.
# Run: python3 wakeband_discover.py
# Then update the values below.
#
# Common patterns for similar BLE wristbands (Telink-based):
# ============================================================================

# Placeholder UUIDs — UPDATE THESE after running wakeband_discover.py
# The format is typically: 0000XXXX-0000-1000-8000-00805f9b34fb (standard)
# or a full 128-bit UUID for vendor-specific services

# Primary WakeBand service (likely vendor-specific 128-bit UUID)
WAKEBAND_SERVICE_UUID = "0000ff00-0000-1000-8000-00805f9b34fb"  # PLACEHOLDER

# Write characteristic (phone -> device commands)
WAKEBAND_WRITE_UUID = "0000ff01-0000-1000-8000-00805f9b34fb"  # PLACEHOLDER

# Notify characteristic (device -> phone responses/events)
WAKEBAND_NOTIFY_UUID = "0000ff02-0000-1000-8000-00805f9b34fb"  # PLACEHOLDER

# Standard BLE services (these are usually correct)
BATTERY_SERVICE_UUID = "0000180f-0000-1000-8000-00805f9b34fb"
BATTERY_LEVEL_UUID = "00002a19-0000-1000-8000-00805f9b34fb"
DEVICE_INFO_SERVICE_UUID = "0000180a-0000-1000-8000-00805f9b34fb"

# WakeBand device name patterns for scanning
DEVICE_NAME_PATTERNS = ["WakeBand", "Wakeband", "WAKEBAND", "HMD-", "Homedics"]


# ============================================================================
# COMMAND PROTOCOL
#
# This section documents the suspected command format.
# Typical BLE wristband protocols use a structure like:
#   [CMD_TYPE] [CMD_ID] [PAYLOAD...] [CHECKSUM]
#
# These need to be confirmed by BLE traffic capture (HCI snoop log).
# ============================================================================

class WakeBandProtocol:
    """
    Suspected command protocol for the WakeBand.

    Most BLE wristbands use a simple command-response protocol:
    - Commands are written to the write characteristic
    - Responses come via notifications on the notify characteristic
    - Each command starts with a type byte and command byte

    Common command structure for similar devices:
        Byte 0: Command type (e.g., 0x01=time, 0x02=alarm, 0x03=vibration)
        Byte 1: Sub-command
        Byte 2+: Payload
        Last byte: Checksum (XOR of all previous bytes, or sum & 0xFF)

    IMPORTANT: These are educated guesses based on similar devices.
    You MUST verify by capturing actual BLE traffic between the official
    app and your WakeBand. See the README for instructions.
    """

    # Command type prefixes (GUESSES - verify with traffic capture)
    CMD_TIME_SYNC = 0x01      # Sync current time to device
    CMD_ALARM = 0x02          # Set/get alarm settings
    CMD_VIBRATE = 0x03        # Trigger vibration
    CMD_BATTERY = 0x04        # Request battery level
    CMD_DEVICE_INFO = 0x05    # Device info query
    CMD_SETTINGS = 0x06       # Device settings
    CMD_OTA = 0x07            # OTA firmware update

    @staticmethod
    def checksum_xor(data: bytes) -> int:
        """XOR checksum (common in cheap BLE devices)."""
        result = 0
        for b in data:
            result ^= b
        return result

    @staticmethod
    def checksum_sum(data: bytes) -> int:
        """Sum checksum mod 256."""
        return sum(data) & 0xFF

    @classmethod
    def build_command(cls, cmd_type: int, sub_cmd: int, payload: bytes = b"") -> bytes:
        """
        Build a command packet.

        NOTE: The actual format must be verified by traffic capture.
        This is a template that works for many similar devices.
        """
        data = bytes([cmd_type, sub_cmd]) + payload
        checksum = cls.checksum_xor(data)
        return data + bytes([checksum])

    @classmethod
    def time_sync_command(cls) -> bytes:
        """Build a time sync command with current time."""
        now = datetime.now()
        payload = struct.pack(
            "<HBBBBB",
            now.year,
            now.month,
            now.day,
            now.hour,
            now.minute,
            now.second,
        )
        return cls.build_command(cls.CMD_TIME_SYNC, 0x00, payload)

    @classmethod
    def set_alarm_command(cls, hour: int, minute: int, pattern: int = 1,
                          intensity: int = 5, enabled: bool = True,
                          snooze: bool = False, alarm_id: int = 0) -> bytes:
        """
        Build a set-alarm command.

        Args:
            hour: 0-23
            minute: 0-59
            pattern: 1-9 vibration pattern
            intensity: 1-9 vibration intensity
            enabled: Whether alarm is active
            snooze: Whether snooze is enabled
            alarm_id: Alarm slot (0-based, device may support multiple)
        """
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("Invalid time")
        if not (1 <= pattern <= 9 and 1 <= intensity <= 9):
            raise ValueError("Pattern and intensity must be 1-9")

        flags = (1 if enabled else 0) | (2 if snooze else 0)
        payload = bytes([alarm_id, hour, minute, pattern, intensity, flags])
        return cls.build_command(cls.CMD_ALARM, 0x01, payload)

    @classmethod
    def vibrate_command(cls, pattern: int = 1, intensity: int = 5, duration_sec: int = 3) -> bytes:
        """Build a manual vibration trigger command."""
        if not (1 <= pattern <= 9 and 1 <= intensity <= 9):
            raise ValueError("Pattern and intensity must be 1-9")
        payload = bytes([pattern, intensity, duration_sec])
        return cls.build_command(cls.CMD_VIBRATE, 0x01, payload)

    @classmethod
    def battery_request(cls) -> bytes:
        """Build a battery level request command."""
        return cls.build_command(cls.CMD_BATTERY, 0x00)


class WakeBand:
    """
    High-level WakeBand BLE controller.

    Usage:
        async with WakeBand("XX:XX:XX:XX:XX:XX") as wb:
            level = await wb.read_battery()
            print(f"Battery: {level}%")
    """

    def __init__(self, address: str, timeout: float = 20.0):
        self.address = address
        self.timeout = timeout
        self._client: Optional[BleakClient] = None
        self._notify_data: list[bytes] = []
        self._notify_event = asyncio.Event()

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        await self.disconnect()

    async def connect(self):
        """Connect to the WakeBand device."""
        self._client = BleakClient(self.address, timeout=self.timeout)
        await self._client.connect()
        print(f"[+] Connected to {self.address}")

        # Subscribe to notifications
        try:
            await self._client.start_notify(WAKEBAND_NOTIFY_UUID, self._on_notify)
            print(f"[+] Subscribed to notifications")
        except Exception as e:
            print(f"[-] Could not subscribe to {WAKEBAND_NOTIFY_UUID}: {e}")
            print(f"    Run wakeband_discover.py to find correct UUIDs")

    async def disconnect(self):
        """Disconnect from the device."""
        if self._client and self._client.is_connected:
            await self._client.disconnect()
            print(f"[+] Disconnected")

    def _on_notify(self, sender, data: bytes):
        """Handle incoming BLE notifications."""
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(f"  [{timestamp}] NOTIFY: {data.hex()}")
        self._notify_data.append(data)
        self._notify_event.set()

    async def _send_command(self, data: bytes, wait_response: bool = True,
                            response_timeout: float = 3.0) -> Optional[bytes]:
        """Send a command and optionally wait for response notification."""
        self._notify_data.clear()
        self._notify_event.clear()

        try:
            await self._client.write_gatt_char(WAKEBAND_WRITE_UUID, data, response=True)
        except Exception:
            await self._client.write_gatt_char(WAKEBAND_WRITE_UUID, data, response=False)

        if wait_response:
            try:
                await asyncio.wait_for(self._notify_event.wait(), timeout=response_timeout)
                return self._notify_data[-1] if self._notify_data else None
            except asyncio.TimeoutError:
                return None

        return None

    async def read_battery(self) -> Optional[int]:
        """Read battery level (0-100%)."""
        try:
            # Try standard BLE battery service first
            value = await self._client.read_gatt_char(BATTERY_LEVEL_UUID)
            return value[0]
        except Exception:
            # Fall back to vendor-specific command
            response = await self._send_command(WakeBandProtocol.battery_request())
            if response and len(response) >= 3:
                return response[2]  # Guess: 3rd byte is battery %
            return None

    async def sync_time(self):
        """Sync current time to the device."""
        cmd = WakeBandProtocol.time_sync_command()
        print(f"[*] Syncing time: {cmd.hex()}")
        await self._send_command(cmd, wait_response=False)

    async def set_alarm(self, hour: int, minute: int, pattern: int = 1,
                        intensity: int = 5, enabled: bool = True,
                        snooze: bool = False, alarm_id: int = 0):
        """Set an alarm on the WakeBand."""
        cmd = WakeBandProtocol.set_alarm_command(
            hour, minute, pattern, intensity, enabled, snooze, alarm_id
        )
        print(f"[*] Setting alarm {alarm_id}: {hour:02d}:{minute:02d} "
              f"pattern={pattern} intensity={intensity}: {cmd.hex()}")
        response = await self._send_command(cmd)
        if response:
            print(f"[+] Device responded: {response.hex()}")

    async def vibrate(self, pattern: int = 1, intensity: int = 5, duration: int = 3):
        """Trigger manual vibration (for testing)."""
        cmd = WakeBandProtocol.vibrate_command(pattern, intensity, duration)
        print(f"[*] Vibrating: pattern={pattern} intensity={intensity} duration={duration}s")
        await self._send_command(cmd, wait_response=False)

    async def discover_services(self):
        """Print all GATT services and characteristics."""
        for service in self._client.services:
            print(f"\n[Service] {service.uuid} - {service.description}")
            for char in service.characteristics:
                props = ", ".join(char.properties)
                print(f"  [Char] {char.uuid} [{props}] - {char.description}")
                if "read" in char.properties:
                    try:
                        val = await self._client.read_gatt_char(char)
                        print(f"         Value: {val.hex()}")
                    except Exception:
                        pass

    async def sniff(self, duration: float = 60.0):
        """
        Subscribe to ALL notification characteristics and log traffic.
        Useful for reverse engineering: run this while using the official app
        on another phone to capture the protocol.
        """
        print(f"[*] Sniffing all notifications for {duration}s...")
        handlers = []

        for service in self._client.services:
            for char in service.characteristics:
                if "notify" in char.properties or "indicate" in char.properties:
                    uuid = char.uuid

                    def make_handler(u):
                        def handler(sender, data):
                            t = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                            print(f"  [{t}] {u}: {data.hex()}")
                        return handler

                    try:
                        await self._client.start_notify(char, make_handler(uuid))
                        handlers.append(char)
                        print(f"[+] Monitoring {uuid}")
                    except Exception as e:
                        print(f"[-] Cannot monitor {uuid}: {e}")

        try:
            await asyncio.sleep(duration)
        except KeyboardInterrupt:
            pass

        for char in handlers:
            try:
                await self._client.stop_notify(char)
            except Exception:
                pass

    async def raw_write(self, char_uuid: str, data: bytes):
        """Write raw bytes to any characteristic (for experimentation)."""
        print(f"[*] Writing {data.hex()} to {char_uuid}")
        try:
            await self._client.write_gatt_char(char_uuid, data, response=True)
            print(f"[+] Written (with response)")
        except Exception:
            await self._client.write_gatt_char(char_uuid, data, response=False)
            print(f"[+] Written (without response)")

    async def raw_read(self, char_uuid: str) -> bytes:
        """Read raw bytes from any characteristic."""
        data = await self._client.read_gatt_char(char_uuid)
        print(f"[*] Read from {char_uuid}: {data.hex()}")
        return data


# ============================================================================
# CLI
# ============================================================================

async def cmd_scan():
    """Scan for WakeBand devices."""
    print("[*] Scanning for BLE devices (10s)...")
    devices = await BleakScanner.discover(timeout=10.0, return_adv=True)

    found = []
    for device, adv in devices.values():
        name = device.name or adv.local_name or ""
        for pattern in DEVICE_NAME_PATTERNS:
            if pattern.lower() in name.lower():
                found.append((device, adv))
                break

    if found:
        print(f"\n[+] Found {len(found)} WakeBand device(s):")
        for device, adv in found:
            print(f"  Name: {device.name or adv.local_name}")
            print(f"  Address: {device.address}")
            print(f"  RSSI: {adv.rssi} dBm")
            if adv.service_uuids:
                print(f"  Services: {adv.service_uuids}")
            if adv.manufacturer_data:
                for cid, mdata in adv.manufacturer_data.items():
                    print(f"  Manufacturer (0x{cid:04x}): {mdata.hex()}")
            print()
    else:
        print("\n[-] No WakeBand devices found.")
        print("    Make sure your WakeBand is nearby and not connected to another device.")
        print("    Also try: python3 wakeband_discover.py --all")


async def cmd_discover(address: str):
    """Discover GATT services on a device."""
    async with WakeBand(address) as wb:
        await wb.discover_services()


async def cmd_battery(address: str):
    """Read battery level."""
    async with WakeBand(address) as wb:
        level = await wb.read_battery()
        if level is not None:
            print(f"[+] Battery: {level}%")
        else:
            print("[-] Could not read battery level")


async def cmd_set_alarm(address: str, time_str: str, pattern: int,
                        intensity: int, snooze: bool):
    """Set an alarm."""
    parts = time_str.split(":")
    hour, minute = int(parts[0]), int(parts[1])
    async with WakeBand(address) as wb:
        await wb.set_alarm(hour, minute, pattern, intensity, snooze=snooze)


async def cmd_vibrate(address: str, pattern: int, intensity: int, duration: int):
    """Trigger vibration."""
    async with WakeBand(address) as wb:
        await wb.vibrate(pattern, intensity, duration)


async def cmd_sniff(address: str, duration: float):
    """Sniff BLE notifications."""
    async with WakeBand(address) as wb:
        await wb.sniff(duration)


async def cmd_raw(address: str, char_uuid: str, data_hex: str):
    """Write raw data to a characteristic."""
    async with WakeBand(address) as wb:
        await wb.raw_write(char_uuid, bytes.fromhex(data_hex))
        await asyncio.sleep(2)  # Wait for any response


def main():
    parser = argparse.ArgumentParser(
        description="WakeBand BLE Control Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s scan                                    # Find WakeBand
  %(prog)s discover XX:XX:XX:XX:XX:XX              # Dump GATT table
  %(prog)s battery --addr XX:XX:XX:XX:XX:XX        # Read battery
  %(prog)s set-alarm 07:30 --pattern 3 --intensity 5 --addr XX:XX
  %(prog)s vibrate --pattern 1 --intensity 9 --addr XX:XX
  %(prog)s sniff --addr XX:XX --duration 120       # Capture traffic
  %(prog)s raw --addr XX:XX --char UUID --data ff0102
        """,
    )
    parser.add_argument("--addr", help="WakeBand BLE address")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("scan", help="Scan for WakeBand devices")

    p = sub.add_parser("discover", help="Discover GATT services")
    p.add_argument("address", nargs="?", help="BLE address")

    p = sub.add_parser("battery", help="Read battery level")

    p = sub.add_parser("set-alarm", help="Set an alarm")
    p.add_argument("time", help="Alarm time (HH:MM)")
    p.add_argument("--pattern", type=int, default=1, help="Vibration pattern (1-9)")
    p.add_argument("--intensity", type=int, default=5, help="Vibration intensity (1-9)")
    p.add_argument("--snooze", action="store_true", help="Enable snooze")

    p = sub.add_parser("vibrate", help="Trigger manual vibration")
    p.add_argument("--pattern", type=int, default=1, help="Vibration pattern (1-9)")
    p.add_argument("--intensity", type=int, default=5, help="Vibration intensity (1-9)")
    p.add_argument("--duration", type=int, default=3, help="Duration in seconds")

    p = sub.add_parser("sniff", help="Sniff BLE notifications")
    p.add_argument("--duration", type=float, default=60.0, help="Duration in seconds")

    p = sub.add_parser("raw", help="Write raw hex data")
    p.add_argument("--char", required=True, help="Characteristic UUID")
    p.add_argument("--data", required=True, help="Hex data to write")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    if args.command == "scan":
        asyncio.run(cmd_scan())
    elif args.command == "discover":
        addr = args.address or args.addr
        if not addr:
            print("Error: provide address via positional arg or --addr")
            sys.exit(1)
        asyncio.run(cmd_discover(addr))
    elif args.command == "battery":
        if not args.addr:
            print("Error: --addr required")
            sys.exit(1)
        asyncio.run(cmd_battery(args.addr))
    elif args.command == "set-alarm":
        if not args.addr:
            print("Error: --addr required")
            sys.exit(1)
        asyncio.run(cmd_set_alarm(args.addr, args.time, args.pattern, args.intensity, args.snooze))
    elif args.command == "vibrate":
        if not args.addr:
            print("Error: --addr required")
            sys.exit(1)
        asyncio.run(cmd_vibrate(args.addr, args.pattern, args.intensity, args.duration))
    elif args.command == "sniff":
        if not args.addr:
            print("Error: --addr required")
            sys.exit(1)
        asyncio.run(cmd_sniff(args.addr, args.duration))
    elif args.command == "raw":
        if not args.addr:
            print("Error: --addr required")
            sys.exit(1)
        asyncio.run(cmd_raw(args.addr, args.char, args.data))


if __name__ == "__main__":
    main()
