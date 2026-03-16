#!/usr/bin/env python3
"""
WakeBand BLE Control Tool

Control your HoMedics WakeBand from Linux via BLE.

Usage:
    # First, discover your device's UUIDs:
    python3 wakeband_discover.py

    # Then update the UUIDs below and run:
    python3 wakeband_control.py vibrate               # Test vibration (mode=0, intensity=4)
    python3 wakeband_control.py vibrate --mode 3 --intensity 7
    python3 wakeband_control.py scan                   # Scan for devices
    python3 wakeband_control.py discover               # Full service discovery
    python3 wakeband_control.py battery                # Read battery level
    python3 wakeband_control.py sniff                  # Sniff all notifications
    python3 wakeband_control.py write AA BB CC DD      # Send raw hex bytes
    python3 wakeband_control.py set-alarm 07:30 --vibration 2 --intensity 5 --days mon,tue,wed,thu,fri

Requirements: pip install bleak
"""

import asyncio
import argparse
import struct
import sys
import time
from datetime import datetime
from bleak import BleakScanner, BleakClient, BleakGATTCharacteristic

# ============================================================================
# CONFIGURATION - Update these after running wakeband_discover.py
# ============================================================================
DEVICE_ADDRESS = ""  # e.g., "AA:BB:CC:DD:EE:FF"
SERVICE_UUID = ""    # e.g., "0000fff0-0000-1000-8000-00805f9b34fb"
WRITE_CHAR_UUID = "" # The characteristic with write property
NOTIFY_CHAR_UUID = "" # The characteristic with notify property
# ============================================================================

DEVICE_NAME = "WakeBand"
SCAN_TIMEOUT = 10
CONNECT_TIMEOUT = 20
RESPONSE_TIMEOUT = 5

# Vibration modes (index 0-8)
VIBRATION_MODES = {
    0: "Steady Vibe",
    1: "Ramp Climb",
    2: "Rumble",
    3: "Wink",
    4: "Jolt",
    5: "Pulse Beat",
    6: "Rapid Pulse",
    7: "Ascension Vibe",
    8: "Random",
}

# Day encoding for repeat alarms
DAYS = {
    "mon": 0x01, "tue": 0x02, "wed": 0x04, "thu": 0x08,
    "fri": 0x10, "sat": 0x20, "sun": 0x40,
}


class WakeBandController:
    def __init__(self, address: str = None):
        self.address = address or DEVICE_ADDRESS
        self.client = None
        self.write_char = None
        self.notify_char = None
        self.response_event = asyncio.Event()
        self.last_response = None

    def _notification_handler(self, sender: BleakGATTCharacteristic, data: bytearray):
        """Handle incoming BLE notifications from the device."""
        hex_str = data.hex()
        print(f"  << NOTIFY [{sender.uuid}]: {hex_str} ({len(data)} bytes)")
        self.last_response = data
        self.response_event.set()

        # Try to decode known response types
        self._decode_response(data)

    def _decode_response(self, data: bytearray):
        """Attempt to decode known response formats."""
        if len(data) < 2:
            return
        # Common response patterns will be logged here
        # The exact format depends on the device firmware
        print(f"       Raw bytes: {list(data)}")

    async def find_device(self) -> str:
        """Scan for WakeBand and return its address."""
        if self.address:
            return self.address

        print(f"Scanning for '{DEVICE_NAME}'...")
        devices = await BleakScanner.discover(timeout=SCAN_TIMEOUT)
        for d in devices:
            if d.name and DEVICE_NAME.lower() in d.name.lower():
                print(f"  Found: {d.name} [{d.address}] RSSI={d.rssi}")
                self.address = d.address
                return d.address

        raise RuntimeError(f"No '{DEVICE_NAME}' device found. Is it powered on?")

    async def connect(self):
        """Connect to the WakeBand and set up characteristics."""
        address = await self.find_device()
        print(f"Connecting to {address}...")

        self.client = BleakClient(address, timeout=CONNECT_TIMEOUT)
        await self.client.connect()
        print(f"Connected (MTU={self.client.mtu_size})")

        # Auto-discover characteristics if not configured
        if not WRITE_CHAR_UUID or not NOTIFY_CHAR_UUID:
            await self._auto_discover()
        else:
            self.write_char = WRITE_CHAR_UUID
            self.notify_char = NOTIFY_CHAR_UUID

        # Enable notifications
        if self.notify_char:
            await self.client.start_notify(self.notify_char, self._notification_handler)
            print(f"Notifications enabled on {self.notify_char}")

    async def _auto_discover(self):
        """Auto-discover write and notify characteristics."""
        print("Auto-discovering characteristics...")
        for service in self.client.services:
            # Skip standard BLE services (Generic Access, Generic Attribute, Device Info)
            uuid_short = service.uuid[4:8].lower()
            if uuid_short in ("1800", "1801", "180a"):
                continue

            for char in service.characteristics:
                props = char.properties
                if ("write" in props or "write-without-response" in props) and not self.write_char:
                    self.write_char = char.uuid
                    print(f"  Write char: {char.uuid} (service: {service.uuid})")
                if ("notify" in props or "indicate" in props) and not self.notify_char:
                    self.notify_char = char.uuid
                    print(f"  Notify char: {char.uuid} (service: {service.uuid})")

        if not self.write_char:
            raise RuntimeError("No writable characteristic found!")
        if not self.notify_char:
            print("WARNING: No notify characteristic found. Responses won't be received.")

    async def disconnect(self):
        """Disconnect from the device."""
        if self.client and self.client.is_connected:
            await self.client.disconnect()
            print("Disconnected")

    async def write_command(self, data: bytes, wait_response: bool = True) -> bytearray:
        """Write a command and optionally wait for response."""
        hex_str = data.hex()
        print(f"  >> WRITE [{self.write_char}]: {hex_str} ({len(data)} bytes)")
        print(f"       Raw bytes: {list(data)}")

        self.response_event.clear()
        self.last_response = None

        await self.client.write_gatt_char(self.write_char, data, response=True)

        if wait_response and self.notify_char:
            try:
                await asyncio.wait_for(self.response_event.wait(), RESPONSE_TIMEOUT)
            except asyncio.TimeoutError:
                print("  !! No response received (timeout)")

        return self.last_response

    async def vibration_test(self, mode: int = 0, intensity: int = 4):
        """Trigger a vibration test on the device.

        Since the exact command bytes need to be discovered from your device,
        this method tries common BLE wearable command formats.

        mode: 0-8 (vibration pattern)
        intensity: 0-8 (vibration strength)
        """
        mode = max(0, min(8, mode))
        intensity = max(0, min(8, intensity))

        mode_name = VIBRATION_MODES.get(mode, "Unknown")
        print(f"\nVibration test: mode={mode} ({mode_name}), intensity={intensity}")

        # The exact command format needs to be discovered from your device.
        # Common Chinese BLE wearable command formats:
        #
        # Format A: [header, cmd_type, mode, intensity, checksum]
        # Format B: [0xAA, length, cmd_id, mode, intensity, checksum]
        # Format C: [cmd_id, sub_cmd, mode, intensity]
        #
        # We'll try the most common formats. Check the responses to determine
        # which one your device accepts.

        # Try writing vibration test commands in common formats
        # The user should monitor notifications to see which format gets a valid response

        # Approach: construct a minimal vibration test command
        # Based on the app's writeVibrationTest method
        commands_to_try = [
            # Format: [command_byte, mode, intensity]
            bytes([0x09, mode, intensity]),
            bytes([0x09, mode + 1, intensity + 1]),  # 1-indexed
            # With header byte
            bytes([0xAA, 0x09, mode, intensity]),
            bytes([0xAA, 0x03, 0x09, mode, intensity]),
            # With checksum (XOR of all bytes)
            self._with_checksum(bytes([0x09, mode, intensity])),
            self._with_checksum(bytes([0xAA, 0x09, mode, intensity])),
        ]

        for i, cmd in enumerate(commands_to_try):
            print(f"\n--- Trying command format {i+1} ---")
            resp = await self.write_command(cmd)
            if resp:
                print(f"  Got response! This format may be correct.")
                # Wait a bit for the vibration to complete
                await asyncio.sleep(2)
                return resp
            await asyncio.sleep(1)

        print("\nNone of the standard formats got a response.")
        print("Use 'sniff' mode while using the phone app to capture the exact command bytes.")
        return None

    def _with_checksum(self, data: bytes) -> bytes:
        """Append XOR checksum byte."""
        checksum = 0
        for b in data:
            checksum ^= b
        return data + bytes([checksum & 0xFF])

    def _with_sum_checksum(self, data: bytes) -> bytes:
        """Append sum checksum byte."""
        checksum = sum(data) & 0xFF
        return data + bytes([checksum])

    async def read_battery(self):
        """Request battery level from the device."""
        print("\nRequesting battery level...")
        # Common battery request commands
        commands = [
            bytes([0x06]),
            bytes([0xAA, 0x06]),
            bytes([0x03]),
        ]
        for cmd in commands:
            resp = await self.write_command(cmd)
            if resp:
                return resp
        return None

    async def set_time(self):
        """Sync current time to the device."""
        now = datetime.now()
        print(f"\nSetting time: {now.strftime('%Y-%m-%d %H:%M:%S')}")

        # Common time sync formats
        time_data = bytes([
            now.year - 2000,  # Year offset from 2000
            now.month,
            now.day,
            now.hour,
            now.minute,
            now.second,
            now.weekday() + 1,  # 1=Monday, 7=Sunday
        ])

        commands = [
            bytes([0x01]) + time_data,
            bytes([0xAA, 0x01]) + time_data,
            bytes([0xAA, len(time_data) + 1, 0x01]) + time_data,
        ]
        for cmd in commands:
            resp = await self.write_command(cmd)
            if resp:
                return resp
        return None

    async def set_alarm(self, hour: int, minute: int, vibration: int = 0,
                        intensity: int = 4, days: int = 0x7F, enabled: bool = True,
                        alarm_id: int = 1):
        """Set an alarm on the device.

        hour: 0-23
        minute: 0-59
        vibration: 0-8 (vibration mode index)
        intensity: 0-8 (intensity level)
        days: bitmask (0x01=Mon, 0x02=Tue, 0x04=Wed, 0x08=Thu, 0x10=Fri, 0x20=Sat, 0x40=Sun)
        enabled: True to enable, False to disable
        alarm_id: alarm slot (1-based)
        """
        mode_name = VIBRATION_MODES.get(vibration, "Unknown")
        days_str = self._days_to_str(days)
        print(f"\nSetting alarm: {hour:02d}:{minute:02d}")
        print(f"  Vibration: {vibration} ({mode_name})")
        print(f"  Intensity: {intensity}")
        print(f"  Days: {days_str}")
        print(f"  Enabled: {enabled}")

        alarm_data = bytes([
            alarm_id,
            1 if enabled else 0,
            hour,
            minute,
            vibration,
            intensity,
            days,
        ])

        commands = [
            bytes([0x02]) + alarm_data,
            bytes([0xAA, 0x02]) + alarm_data,
            self._with_checksum(bytes([0x02]) + alarm_data),
        ]
        for cmd in commands:
            resp = await self.write_command(cmd)
            if resp:
                return resp
        return None

    def _days_to_str(self, days: int) -> str:
        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        result = []
        for i, name in enumerate(day_names):
            if days & (1 << i):
                result.append(name)
        return ", ".join(result) if result else "None"

    async def sniff(self, duration: int = 60):
        """Sniff all BLE notifications for the specified duration.

        Use this while operating the phone app to capture command/response pairs.
        """
        print(f"\nSniffing notifications for {duration}s...")
        print("Use the phone app to trigger actions and observe the captured data.")
        print("Press Ctrl+C to stop.\n")

        try:
            await asyncio.sleep(duration)
        except asyncio.CancelledError:
            pass
        print("\nSniff complete.")

    async def write_raw(self, hex_bytes: list):
        """Write raw hex bytes to the device."""
        data = bytes(int(b, 16) for b in hex_bytes)
        print(f"\nWriting raw bytes...")
        return await self.write_command(data)


async def cmd_scan(args):
    """Scan for WakeBand devices."""
    print(f"Scanning for BLE devices ({SCAN_TIMEOUT}s)...")
    devices = await BleakScanner.discover(timeout=SCAN_TIMEOUT)

    wakebands = []
    others = []
    for d in sorted(devices, key=lambda x: x.rssi or -999, reverse=True):
        if d.name and DEVICE_NAME.lower() in d.name.lower():
            wakebands.append(d)
        else:
            others.append(d)

    if wakebands:
        print(f"\nWakeBand devices found:")
        for d in wakebands:
            print(f"  {d.name:20s} [{d.address}] RSSI={d.rssi}")
    else:
        print(f"\nNo WakeBand devices found.")

    if args.all:
        print(f"\nAll other BLE devices ({len(others)}):")
        for d in others[:20]:
            name = d.name or "(unknown)"
            print(f"  {name:30s} [{d.address}] RSSI={d.rssi}")


async def cmd_discover(args):
    """Discover services on the device."""
    # Import and run the discovery script logic
    from wakeband_discover import discover_services, scan_for_wakeband

    address = args.address
    if not address:
        wakebands = await scan_for_wakeband()
        if wakebands:
            address = wakebands[0].address
    if address:
        await discover_services(address)


async def cmd_vibrate(args):
    """Trigger a vibration test."""
    ctrl = WakeBandController(args.address)
    try:
        await ctrl.connect()
        await ctrl.vibration_test(mode=args.mode, intensity=args.intensity)
    finally:
        await ctrl.disconnect()


async def cmd_battery(args):
    """Read battery level."""
    ctrl = WakeBandController(args.address)
    try:
        await ctrl.connect()
        await ctrl.read_battery()
    finally:
        await ctrl.disconnect()


async def cmd_set_time(args):
    """Sync time to device."""
    ctrl = WakeBandController(args.address)
    try:
        await ctrl.connect()
        await ctrl.set_time()
    finally:
        await ctrl.disconnect()


async def cmd_set_alarm(args):
    """Set an alarm."""
    hour, minute = map(int, args.time.split(":"))
    days = 0
    if args.days:
        for d in args.days.split(","):
            d = d.strip().lower()[:3]
            if d in DAYS:
                days |= DAYS[d]
    else:
        days = 0x7F  # Every day

    ctrl = WakeBandController(args.address)
    try:
        await ctrl.connect()
        await ctrl.set_alarm(
            hour=hour,
            minute=minute,
            vibration=args.vibration,
            intensity=args.intensity,
            days=days,
            enabled=True,
        )
    finally:
        await ctrl.disconnect()


async def cmd_sniff(args):
    """Sniff BLE notifications."""
    ctrl = WakeBandController(args.address)
    try:
        await ctrl.connect()
        await ctrl.sniff(duration=args.duration)
    finally:
        await ctrl.disconnect()


async def cmd_write(args):
    """Write raw hex bytes."""
    ctrl = WakeBandController(args.address)
    try:
        await ctrl.connect()
        await ctrl.write_raw(args.bytes)
    finally:
        await ctrl.disconnect()


def main():
    parser = argparse.ArgumentParser(
        description="WakeBand BLE Control Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s scan                           # Scan for WakeBand devices
  %(prog)s scan --all                     # Show all BLE devices
  %(prog)s discover                       # Discover BLE services/characteristics
  %(prog)s vibrate                        # Test vibration (default mode & intensity)
  %(prog)s vibrate --mode 3 --intensity 7 # Specific mode & intensity
  %(prog)s battery                        # Read battery level
  %(prog)s set-time                       # Sync current time
  %(prog)s set-alarm 07:30                # Set alarm for 7:30 AM (every day)
  %(prog)s set-alarm 07:30 --days mon,tue,wed,thu,fri --vibration 2 --intensity 5
  %(prog)s sniff --duration 120           # Sniff notifications for 2 minutes
  %(prog)s write AA BB CC DD              # Send raw hex bytes

Vibration modes (--mode):
  0: Steady Vibe    3: Wink        6: Rapid Pulse
  1: Ramp Climb     4: Jolt        7: Ascension Vibe
  2: Rumble         5: Pulse Beat  8: Random

Intensity levels (--intensity): 0 (lightest) to 8 (strongest)

Day codes (--days): mon,tue,wed,thu,fri,sat,sun
""")

    parser.add_argument("--address", "-a", help="Device BLE address (XX:XX:XX:XX:XX:XX)")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # scan
    p_scan = subparsers.add_parser("scan", help="Scan for WakeBand devices")
    p_scan.add_argument("--all", action="store_true", help="Show all BLE devices")

    # discover
    p_discover = subparsers.add_parser("discover", help="Discover BLE services")

    # vibrate
    p_vib = subparsers.add_parser("vibrate", help="Trigger vibration test")
    p_vib.add_argument("--mode", "-m", type=int, default=0, choices=range(9),
                       help="Vibration mode (0-8, default: 0)")
    p_vib.add_argument("--intensity", "-i", type=int, default=4, choices=range(9),
                       help="Intensity level (0-8, default: 4)")

    # battery
    subparsers.add_parser("battery", help="Read battery level")

    # set-time
    subparsers.add_parser("set-time", help="Sync current time to device")

    # set-alarm
    p_alarm = subparsers.add_parser("set-alarm", help="Set an alarm")
    p_alarm.add_argument("time", help="Alarm time (HH:MM)")
    p_alarm.add_argument("--vibration", "-v", type=int, default=0, choices=range(9),
                         help="Vibration mode (0-8)")
    p_alarm.add_argument("--intensity", "-i", type=int, default=4, choices=range(9),
                         help="Intensity level (0-8)")
    p_alarm.add_argument("--days", "-d", help="Repeat days (mon,tue,wed,thu,fri,sat,sun)")

    # sniff
    p_sniff = subparsers.add_parser("sniff", help="Sniff BLE notifications")
    p_sniff.add_argument("--duration", "-d", type=int, default=60,
                         help="Sniff duration in seconds (default: 60)")

    # write
    p_write = subparsers.add_parser("write", help="Write raw hex bytes")
    p_write.add_argument("bytes", nargs="+", help="Hex bytes (e.g., AA BB CC)")

    args = parser.parse_args()

    commands = {
        "scan": cmd_scan,
        "discover": cmd_discover,
        "vibrate": cmd_vibrate,
        "battery": cmd_battery,
        "set-time": cmd_set_time,
        "set-alarm": cmd_set_alarm,
        "sniff": cmd_sniff,
        "write": cmd_write,
    }

    asyncio.run(commands[args.command](args))


if __name__ == "__main__":
    main()
