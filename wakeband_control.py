#!/usr/bin/env python3
"""
WakeBand BLE Control Tool

Control your HoMedics WakeBand from Linux via BLE.
Protocol reverse-engineered from the companion app (com.homedics.bracelet v1.2.0).

Usage:
    python3 wakeband_control.py scan                   # Scan for devices
    python3 wakeband_control.py vibrate                # Test vibration (mode=0, intensity=4)
    python3 wakeband_control.py vibrate --mode 3 --intensity 7
    python3 wakeband_control.py battery                # Read battery level
    python3 wakeband_control.py set-time               # Sync current time
    python3 wakeband_control.py sniff                  # Sniff all notifications
    python3 wakeband_control.py write 5A 04 55 09 00 04 6B  # Send raw hex bytes

Requirements: pip install bleak
"""

import asyncio
import argparse
import sys
from datetime import datetime
from bleak import BleakScanner, BleakClient, BleakGATTCharacteristic

# BLE UUIDs (from app's BluetoothManage._internal())
SERVICE_UUID = "0000ac00-0000-1000-8000-00805f9b34fb"
NOTIFY_CHAR_UUID = "0000ac01-0000-1000-8000-00805f9b34fb"  # device -> app
WRITE_CHAR_UUID = "0000ac02-0000-1000-8000-00805f9b34fb"   # app -> device

DEVICE_NAME = "WakeBand"
SCAN_TIMEOUT = 10
CONNECT_TIMEOUT = 20
RESPONSE_TIMEOUT = 5

# Protocol constants
HEADER = 0x5A

# Command IDs (app -> device, written to AC02)
CMD_SET_TIME = (0x55, 0x01)
CMD_BATTERY = (0x55, 0x02)
CMD_GET_ALARM_COUNT = (0x55, 0x03)
CMD_ADD_ALARM = (0x55, 0x04)
CMD_EDIT_ALARM = (0x55, 0x05)
CMD_DELETE_ALARM = (0x55, 0x06)
CMD_GET_ALARM_LIST = (0x55, 0x07)
CMD_VIBRATION_TEST = (0x55, 0x09)
CMD_DELETE_ALL_ALARMS = (0x55, 0x0A)
CMD_VERIFY_STRING = (0x55, 0x0C)
CMD_SEND_VERIFY = (0x55, 0x0E)
CMD_LIGHT_STATUS = (0x55, 0x10)

# Response IDs (device -> app, received on AC01)
RESP_VERIFY_CODE = "550e"
RESP_VERIFY_RESULT = "550f"
RESP_SET_TIME = "5501"
RESP_BATTERY = "5502"
RESP_ALARM_DATA = "5503"
RESP_ADD_ALARM = "5504"
RESP_EDIT_ALARM = "5505"
RESP_DELETE_ALARM = "5506"
RESP_ALARM_LIST_DONE = "5507"
RESP_VIBRATION = "5509"
RESP_DELETE_ALL = "550a"
RESP_LIGHT = "5510"

# Vibration modes (index 0-8)
VIBRATION_MODES = {
    0: "Steady Vibe",    1: "Ramp Climb",     2: "Rumble",
    3: "Wink",           4: "Jolt",           5: "Pulse Beat",
    6: "Rapid Pulse",    7: "Ascension Vibe", 8: "Random",
}

# Day encoding bitmask
DAYS = {
    "mon": 0x01, "tue": 0x02, "wed": 0x04, "thu": 0x08,
    "fri": 0x10, "sat": 0x20, "sun": 0x40,
}


def int_to_hex(value: int) -> str:
    """Convert int to 2-char hex string (zero-padded), matching app's intToHex()."""
    h = format(value, "x")
    if len(h) % 2 == 1:
        h = "0" + h
    return h


def checksum(data_bytes: list[int]) -> int:
    """Compute checksum: (sum(length + command + payload) - 1) & 0xFF."""
    return (sum(data_bytes) - 1) & 0xFF


def build_frame(cmd: tuple[int, int], payload: bytes = b"") -> bytes:
    """Build a complete BLE command frame.

    Frame format: [0x5A] [length] [cmd_hi] [cmd_lo] [payload...] [checksum]

    length = number of payload bytes only (NOT including command bytes)
    checksum = (sum(length + command + payload) - 1) & 0xFF
    """
    cmd_hi, cmd_lo = cmd
    data_bytes = list(payload)
    length = len(data_bytes)  # payload bytes only, per calculateDataLength()

    # Bytes that go into checksum: length + command + payload
    check_input = [length, cmd_hi, cmd_lo] + data_bytes
    chk = checksum(check_input)

    return bytes([HEADER, length, cmd_hi, cmd_lo] + data_bytes + [chk])


class WakeBandController:
    def __init__(self, address: str = None):
        self.address = address
        self.client = None
        self.write_char = None
        self.notify_char = None
        self.response_event = asyncio.Event()
        self.last_response = None
        self.last_response_hex = ""
        self.verified = False
        self.verify_code = None

    def _notification_handler(self, sender: BleakGATTCharacteristic, data: bytearray):
        """Handle incoming BLE notifications from the device."""
        hex_str = data.hex()
        self.last_response = data
        self.last_response_hex = hex_str

        # Determine prefix type
        if hex_str.startswith("a5"):
            # Normal response - strip A5 header to get response body
            body = hex_str[2:]  # skip "a5" prefix byte
            resp_id = body[:4] if len(body) >= 4 else ""
            self._decode_response(resp_id, body, data)
        elif hex_str.startswith("5e"):
            # Keep-alive / status - ignore
            print(f"  << KEEPALIVE: {hex_str}")
        else:
            # Try parsing directly (some responses may not have A5 prefix)
            resp_id = hex_str[:4] if len(hex_str) >= 4 else ""
            self._decode_response(resp_id, hex_str, data)

        self.response_event.set()

    def _decode_response(self, resp_id: str, hex_str: str, raw: bytearray):
        """Decode known response types."""
        resp_id = resp_id.lower()

        if resp_id == RESP_VERIFY_CODE:
            # Device sends random verification code
            code = hex_str[4:]  # everything after "550e"
            self.verify_code = code
            print(f"  << VERIFY CODE: {code}")
        elif resp_id == RESP_VERIFY_RESULT:
            result = hex_str[4:6] if len(hex_str) >= 6 else ""
            if result == "01":
                self.verified = True
                print(f"  << VERIFY: Success")
            else:
                print(f"  << VERIFY: Failed ({result})")
        elif resp_id == RESP_BATTERY:
            # Battery data follows the response ID
            battery_data = hex_str[4:]
            if len(battery_data) >= 2:
                battery_pct = int(battery_data[:2], 16)
                print(f"  << BATTERY: {battery_pct}%")
                if len(battery_data) >= 4:
                    standby = int(battery_data[2:4], 16)
                    print(f"     Standby: {standby}h")
            else:
                print(f"  << BATTERY (raw): {hex_str}")
        elif resp_id == RESP_SET_TIME:
            print(f"  << TIME SYNC: OK")
        elif resp_id == RESP_VIBRATION:
            print(f"  << VIBRATION: Confirmed")
        elif resp_id == RESP_ALARM_DATA:
            print(f"  << ALARM DATA: {hex_str[4:]}")
        elif resp_id == RESP_ALARM_LIST_DONE:
            print(f"  << ALARM LIST: Complete")
        elif resp_id == RESP_ADD_ALARM:
            print(f"  << ADD ALARM: OK")
        elif resp_id == RESP_EDIT_ALARM:
            print(f"  << EDIT ALARM: OK")
        elif resp_id == RESP_DELETE_ALARM:
            print(f"  << DELETE ALARM: OK")
        elif resp_id == RESP_DELETE_ALL:
            print(f"  << DELETE ALL ALARMS: OK")
        elif resp_id == RESP_LIGHT:
            print(f"  << LIGHT STATUS: OK")
        else:
            print(f"  << UNKNOWN [{resp_id}]: {hex_str}")

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
        """Connect to the WakeBand, discover services, and authenticate."""
        address = await self.find_device()
        print(f"Connecting to {address}...")

        self.client = BleakClient(address, timeout=CONNECT_TIMEOUT)
        await self.client.connect()
        print(f"Connected (MTU={self.client.mtu_size})")

        # Find AC00 service with AC01/AC02 characteristics
        self._discover_characteristics()

        # Enable notifications on AC01
        await self.client.start_notify(self.notify_char, self._notification_handler)
        print(f"Notifications enabled on {self.notify_char}")

        # Authenticate
        await self._authenticate()

    def _discover_characteristics(self):
        """Find AC00 service with AC01 (notify) and AC02 (write) characteristics."""
        for service in self.client.services:
            if "ac00" in service.uuid.lower():
                for char in service.characteristics:
                    uuid_lower = char.uuid.lower()
                    if "ac01" in uuid_lower:
                        self.notify_char = char.uuid
                    elif "ac02" in uuid_lower:
                        self.write_char = char.uuid
                break

        if not self.write_char or not self.notify_char:
            # Fallback: use hardcoded UUIDs
            self.write_char = self.write_char or WRITE_CHAR_UUID
            self.notify_char = self.notify_char or NOTIFY_CHAR_UUID
            print(f"  Using fallback UUIDs (write={self.write_char}, notify={self.notify_char})")
        else:
            print(f"  Service AC00 found: write={self.write_char}, notify={self.notify_char}")

    async def _authenticate(self):
        """Perform the connection verification handshake.

        1. Send writeVerifyString: command 550C + payload "636865636B" ("check")
        2. Device responds with 550E + random verification code
        3. Send writeSendVerifyString: command 550E + the received code
        4. Device responds with 550F + "01" on success
        """
        print("Authenticating...")

        # Step 1: Send "check" string
        check_payload = bytes.fromhex("636865636B")  # ASCII "check"
        frame = build_frame(CMD_VERIFY_STRING, check_payload)
        print(f"  >> VERIFY: {frame.hex()}")
        await self._write_and_wait(frame)

        if not self.verify_code:
            print("  WARNING: No verification code received. Device may not require auth.")
            return

        # Step 2: Send back the verification code
        code_payload = bytes.fromhex(self.verify_code)
        frame = build_frame(CMD_SEND_VERIFY, code_payload)
        print(f"  >> SEND VERIFY: {frame.hex()}")
        await self._write_and_wait(frame)

        if self.verified:
            print("Authentication successful!")
        else:
            print("WARNING: Authentication may have failed. Continuing anyway...")

    async def disconnect(self):
        """Disconnect from the device."""
        if self.client and self.client.is_connected:
            await self.client.disconnect()
            print("Disconnected")

    async def _write_and_wait(self, data: bytes, timeout: float = RESPONSE_TIMEOUT) -> bytearray:
        """Write data and wait for a notification response."""
        self.response_event.clear()
        self.last_response = None

        await self.client.write_gatt_char(self.write_char, data, response=True)

        try:
            await asyncio.wait_for(self.response_event.wait(), timeout)
        except asyncio.TimeoutError:
            print("  !! No response (timeout)")

        return self.last_response

    async def vibration_test(self, mode: int = 0, intensity: int = 4):
        """Trigger a vibration test.

        Command 5509, payload: intToHex(mode) + intToHex(intensity)
        mode: 0-8, intensity: 0-8
        """
        mode = max(0, min(8, mode))
        intensity = max(0, min(8, intensity))
        mode_name = VIBRATION_MODES.get(mode, "Unknown")
        print(f"\nVibration: mode={mode} ({mode_name}), intensity={intensity}")

        payload = bytes([mode, intensity])
        frame = build_frame(CMD_VIBRATION_TEST, payload)
        print(f"  >> {frame.hex()}")
        return await self._write_and_wait(frame)

    async def read_battery(self):
        """Request battery level. Command 5502, payload: 00."""
        print("\nRequesting battery level...")
        frame = build_frame(CMD_BATTERY, bytes([0x00]))
        print(f"  >> {frame.hex()}")
        return await self._write_and_wait(frame)

    async def set_time(self):
        """Sync current time to the device. Command 5501."""
        now = datetime.now()
        print(f"\nSetting time: {now.strftime('%Y-%m-%d %H:%M:%S')}")

        # Build time payload using intToHex for each component
        payload = bytes([
            now.year >> 8, now.year & 0xFF,  # year as 2 bytes
            now.month,
            now.day,
            now.hour,
            now.minute,
            now.second,
            now.weekday() + 1,  # 1=Monday, 7=Sunday
        ])

        frame = build_frame(CMD_SET_TIME, payload)
        print(f"  >> {frame.hex()}")
        return await self._write_and_wait(frame)

    async def get_alarms(self):
        """Request alarm list from device."""
        print("\nRequesting alarm list...")

        # First get alarm count
        frame = build_frame(CMD_GET_ALARM_COUNT, bytes([0x00]))
        print(f"  >> GET COUNT: {frame.hex()}")
        await self._write_and_wait(frame)

        # Then request alarm data
        frame = build_frame(CMD_GET_ALARM_LIST, bytes([0x00]))
        print(f"  >> GET LIST: {frame.hex()}")
        resp = await self._write_and_wait(frame)

        # Wait for additional alarm data notifications
        for _ in range(10):
            self.response_event.clear()
            try:
                await asyncio.wait_for(self.response_event.wait(), 2)
                if self.last_response_hex[:4].lower() == RESP_ALARM_LIST_DONE:
                    break
            except asyncio.TimeoutError:
                break

        return resp

    async def delete_all_alarms(self):
        """Delete all alarms. Command 550A."""
        print("\nDeleting all alarms...")
        frame = build_frame(CMD_DELETE_ALL_ALARMS, bytes([0x00]))
        print(f"  >> {frame.hex()}")
        return await self._write_and_wait(frame)

    async def sniff(self, duration: int = 60):
        """Sniff all BLE notifications for the specified duration."""
        print(f"\nSniffing notifications for {duration}s...")
        print("Use the phone app to trigger actions and observe the captured data.")
        print("Press Ctrl+C to stop.\n")
        try:
            await asyncio.sleep(duration)
        except asyncio.CancelledError:
            pass
        print("\nSniff complete.")

    async def write_raw(self, hex_bytes: list):
        """Write raw hex bytes to the device (no framing)."""
        data = bytes(int(b, 16) for b in hex_bytes)
        print(f"\nWriting raw: {data.hex()} ({len(data)} bytes)")
        return await self._write_and_wait(data)


# CLI command handlers

async def cmd_scan(args):
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


async def cmd_vibrate(args):
    ctrl = WakeBandController(args.address)
    try:
        await ctrl.connect()
        await ctrl.vibration_test(mode=args.mode, intensity=args.intensity)
    finally:
        await ctrl.disconnect()


async def cmd_battery(args):
    ctrl = WakeBandController(args.address)
    try:
        await ctrl.connect()
        await ctrl.read_battery()
    finally:
        await ctrl.disconnect()


async def cmd_set_time(args):
    ctrl = WakeBandController(args.address)
    try:
        await ctrl.connect()
        await ctrl.set_time()
    finally:
        await ctrl.disconnect()


async def cmd_alarms(args):
    ctrl = WakeBandController(args.address)
    try:
        await ctrl.connect()
        await ctrl.get_alarms()
    finally:
        await ctrl.disconnect()


async def cmd_delete_alarms(args):
    ctrl = WakeBandController(args.address)
    try:
        await ctrl.connect()
        await ctrl.delete_all_alarms()
    finally:
        await ctrl.disconnect()


async def cmd_sniff(args):
    ctrl = WakeBandController(args.address)
    try:
        await ctrl.connect()
        await ctrl.sniff(duration=args.duration)
    finally:
        await ctrl.disconnect()


async def cmd_write(args):
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
  %(prog)s vibrate                        # Test vibration (default mode & intensity)
  %(prog)s vibrate --mode 3 --intensity 7 # Specific mode & intensity
  %(prog)s battery                        # Read battery level
  %(prog)s set-time                       # Sync current time
  %(prog)s alarms                         # List alarms on device
  %(prog)s delete-alarms                  # Delete all alarms
  %(prog)s sniff --duration 120           # Sniff notifications for 2 minutes
  %(prog)s write 5A 04 55 09 00 04 6B     # Send raw hex bytes

Vibration modes (--mode):
  0: Steady Vibe    3: Wink        6: Rapid Pulse
  1: Ramp Climb     4: Jolt        7: Ascension Vibe
  2: Rumble         5: Pulse Beat  8: Random

Intensity levels (--intensity): 0 (lightest) to 8 (strongest)
""")

    parser.add_argument("--address", "-a", help="Device BLE address (XX:XX:XX:XX:XX:XX)")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # scan
    p_scan = subparsers.add_parser("scan", help="Scan for WakeBand devices")
    p_scan.add_argument("--all", action="store_true", help="Show all BLE devices")

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

    # alarms
    subparsers.add_parser("alarms", help="List alarms on device")

    # delete-alarms
    subparsers.add_parser("delete-alarms", help="Delete all alarms")

    # sniff
    p_sniff = subparsers.add_parser("sniff", help="Sniff BLE notifications")
    p_sniff.add_argument("--duration", "-d", type=int, default=60,
                         help="Sniff duration in seconds (default: 60)")

    # write
    p_write = subparsers.add_parser("write", help="Write raw hex bytes")
    p_write.add_argument("bytes", nargs="+", help="Hex bytes (e.g., 5A 04 55 09 00 04 6B)")

    args = parser.parse_args()

    commands = {
        "scan": cmd_scan,
        "vibrate": cmd_vibrate,
        "battery": cmd_battery,
        "set-time": cmd_set_time,
        "alarms": cmd_alarms,
        "delete-alarms": cmd_delete_alarms,
        "sniff": cmd_sniff,
        "write": cmd_write,
    }

    asyncio.run(commands[args.command](args))


if __name__ == "__main__":
    main()
