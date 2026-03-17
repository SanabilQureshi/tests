#!/usr/bin/env python3
"""
WakeBand BLE Control Library

Control a Homedics WakeBand programmatically from Linux via BLE.

Protocol reverse-engineered from the official Android app (com.homedics.bracelet
v1.2.0) — a Flutter app using flutter_blue_plus. The BLE protocol uses custom
16-bit UUID characteristics in the 0x55xx and 0xACxx ranges, with hex-encoded
ASCII command strings for verification and device reset.

Features:
    - Set alarm time, vibration pattern (1-9), and intensity (1-9)
    - Enable/disable snooze (9-minute snooze)
    - Read battery level
    - Trigger vibration test
    - Sync time to device
    - Read/write light status (LED)
    - OTA firmware update (AES-128-CBC encrypted)
    - Factory reset

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
# Extracted from the decompiled WakeBand APK (com.homedics.bracelet v1.2.0).
# The app is a Flutter app using flutter_blue_plus for BLE communication.
#
# UUIDs use the Bluetooth Base UUID: 0000XXXX-0000-1000-8000-00805f9b34fb
# Short UUIDs below are the XXXX portion; full 128-bit forms provided.
# ============================================================================

# --- Custom WakeBand characteristics (0x55xx range) ---
# These are vendor-specific characteristics extracted from the Dart AOT snapshot.
# The 55xx range is the primary command/data interface.

CHAR_5501 = "00005501-0000-1000-8000-00805f9b34fb"  # Alarm data / alarm tip
CHAR_5502 = "00005502-0000-1000-8000-00805f9b34fb"  # Device MAC / identification
CHAR_5503 = "00005503-0000-1000-8000-00805f9b34fb"  # General purpose / completion
CHAR_5504 = "00005504-0000-1000-8000-00805f9b34fb"  # Selection / config
CHAR_5505 = "00005505-0000-1000-8000-00805f9b34fb"  # Circle widget / UI sync
CHAR_5506 = "00005506-0000-1000-8000-00805f9b34fb"  # Intensity settings
CHAR_5507 = "00005507-0000-1000-8000-00805f9b34fb"  # LED status / light
CHAR_5508 = "00005508-0000-1000-8000-00805f9b34fb"  # Encrypted data / firmware
CHAR_5509 = "00005509-0000-1000-8000-00805f9b34fb"  # Editable settings
CHAR_550A = "0000550a-0000-1000-8000-00805f9b34fb"  # Timer / time sync
CHAR_550C = "0000550c-0000-1000-8000-00805f9b34fb"  # Scan / notify start
CHAR_550D = "0000550d-0000-1000-8000-00805f9b34fb"  # Elements / settings
CHAR_550E = "0000550e-0000-1000-8000-00805f9b34fb"  # Alarm list
CHAR_550F = "0000550f-0000-1000-8000-00805f9b34fb"  # Battery read (near writeBattery)
CHAR_5510 = "00005510-0000-1000-8000-00805f9b34fb"  # Async ops / named lock
CHAR_5511 = "00005511-0000-1000-8000-00805f9b34fb"  # Device list / launch

# --- Custom WakeBand characteristics (0xACxx range) ---
# Secondary control/management characteristics
CHAR_AC00 = "0000ac00-0000-1000-8000-00805f9b34fb"  # Bluetooth state / connection
CHAR_AC01 = "0000ac01-0000-1000-8000-00805f9b34fb"  # Chaining / event routing
CHAR_AC02 = "0000ac02-0000-1000-8000-00805f9b34fb"  # Bind/unbind management

# --- Standard BLE services ---
GENERIC_ATTRIBUTE_SERVICE = "00001801-0000-1000-8000-00805f9b34fb"
SERVICE_CHANGED_CHAR = "00002a05-0000-1000-8000-00805f9b34fb"
CCCD_UUID = "00002902-0000-1000-8000-00805f9b34fb"  # Client Characteristic Config
BATTERY_SERVICE_UUID = "0000180f-0000-1000-8000-00805f9b34fb"
BATTERY_LEVEL_UUID = "00002a19-0000-1000-8000-00805f9b34fb"
DEVICE_INFO_SERVICE_UUID = "0000180a-0000-1000-8000-00805f9b34fb"

# --- Primary protocol UUIDs (best candidates from APK analysis) ---
# The app uses writeSetTime, writeGetAlarmClock, writeResetDevice, etc.
# These write to specific characteristics. Based on proximity analysis:
WAKEBAND_WRITE_UUID = CHAR_5501       # Primary write (alarm/command data)
WAKEBAND_NOTIFY_UUID = CHAR_550C      # Primary notify (scan/notification start)
WAKEBAND_BATTERY_UUID = CHAR_550F     # Battery read (near writeBattery function)
WAKEBAND_TIME_UUID = CHAR_550A        # Time sync (near timerMillisecondClock)
WAKEBAND_ALARM_LIST_UUID = CHAR_550E  # Alarm list operations
WAKEBAND_INTENSITY_UUID = CHAR_5506   # Intensity settings
WAKEBAND_LIGHT_UUID = CHAR_5507       # LED/light status
WAKEBAND_FIRMWARE_UUID = CHAR_5508    # Firmware/encrypted data (ENCRYPTED_SIZE)
WAKEBAND_BIND_UUID = CHAR_AC02       # Bind/unbind device management
WAKEBAND_CONN_UUID = CHAR_AC00       # Connection state management

# WakeBand device name patterns for scanning
DEVICE_NAME_PATTERNS = ["WakeBand", "Wakeband", "WAKEBAND", "HMD-", "Homedics"]

# All known WakeBand characteristic UUIDs for comprehensive scanning
ALL_WAKEBAND_UUIDS = [
    CHAR_5501, CHAR_5502, CHAR_5503, CHAR_5504, CHAR_5505, CHAR_5506,
    CHAR_5507, CHAR_5508, CHAR_5509, CHAR_550A, CHAR_550C, CHAR_550D,
    CHAR_550E, CHAR_550F, CHAR_5510, CHAR_5511,
    CHAR_AC00, CHAR_AC01, CHAR_AC02,
]


# ============================================================================
# COMMAND PROTOCOL
#
# Reverse-engineered from the WakeBand APK's Dart AOT snapshot (libapp.so).
#
# The app uses hex-encoded ASCII strings for certain commands:
#   - Verify/auth: "636865636b" = hex("check")
#   - Factory reset: "7365742b7265736574" = hex("set+reset")
#
# Protocol functions found in the APK:
#   WRITE operations (phone -> device):
#     writeSetTime          - Sync current time to device
#     writeGetAlarmClock    - Request alarm data from device
#     writeResetDevice      - Factory reset
#     writeDeleteAllAlarmClock - Delete all alarms
#     writeDeleteMoreAlarmClock - Delete specific alarms
#     writeBattery          - Request battery level
#     writeSendVerifyString - Send verification/auth string
#     writeVerifyString     - Write verify string
#     writeVibrationTest    - Trigger vibration test
#
#   READ/response operations (device -> phone via notify):
#     readGetAlarmClockData        - Alarm data response
#     readGetAlarmClockFinishResult - Alarm set confirmation
#     readGetVerifyString          - Verification string response
#     readSetTimeResult            - Time sync confirmation
#     readSetLightStatus           - LED status response
#     readDeleteMoreAlarmClockResult - Delete confirmation
#     readDeleteAllAlarmClockResult  - Delete all confirmation
#     readVerifyStringResult       - Verify result
#     readVibrationTestResult      - Vibration test result
#
#   DB schema (alarm fields):
#     id, hour, minute, day, month, year, timestamp
#     hex_str, time_hex_str (hex-encoded command data)
#     vibration_str, vibration_price, intensity_str, intensity_price
#     repeat_ids, repeat_str, colour, status
#     is_snapze (snooze), is_one (one-time), is_default, is_delete
#     is_require_bind, is_require_unbind, is_require_edit
#     light_status, instruction
#
#   Helper functions:
#     intToHex, intsToHex - Convert integers to hex string commands
#     setConnectDeviceTime - Set time on successful connection
#     dealVibrationAndIntensityAndWeek - Process vibration/intensity/repeat
#     operationSetAlarm - Main alarm set orchestration
# ============================================================================

class WakeBandProtocol:
    """
    WakeBand BLE command protocol.

    Reverse-engineered from com.homedics.bracelet v1.2.0 (Flutter/Dart).

    The protocol uses hex-encoded string commands written to BLE characteristics.
    The app converts integers to hex with intToHex/intsToHex helpers and builds
    command strings stored in hex_str/time_hex_str database fields.

    Verification handshake:
        1. Phone sends "636865636b" (hex for "check") via writeSendVerifyString
        2. Device responds with verification result via readGetVerifyString
        3. On success, phone syncs time via writeSetTime

    Command characteristics (0x55xx range, 0xACxx range):
        Commands are written to specific characteristics based on function.
        Responses arrive as notifications from the same or related characteristics.
    """

    # Hex-encoded ASCII command strings (extracted from APK)
    VERIFY_STRING = "636865636b"          # hex("check") - auth/verify handshake
    RESET_STRING = "7365742b7265736574"   # hex("set+reset") - factory reset

    # Command sub-IDs (extracted from APK constant pool)
    CMD_ID_0901 = "0901"  # Sub-command ID
    CMD_ID_0902 = "0902"  # Sub-command ID
    CMD_ID_0903 = "0903"  # Sub-command ID
    CMD_ID_0A01 = "0A01"  # Sub-command ID

    @staticmethod
    def int_to_hex(value: int) -> str:
        """Convert integer to 2-char hex string (mirrors Dart intToHex)."""
        return f"{value:02x}"

    @staticmethod
    def ints_to_hex(values: list[int]) -> str:
        """Convert list of integers to hex string (mirrors Dart intsToHex)."""
        return "".join(f"{v:02x}" for v in values)

    @classmethod
    def verify_command(cls) -> bytes:
        """Build the verification/handshake command: hex("check")."""
        return bytes.fromhex(cls.VERIFY_STRING)

    @classmethod
    def reset_command(cls) -> bytes:
        """Build the factory reset command: hex("set+reset")."""
        return bytes.fromhex(cls.RESET_STRING)

    @classmethod
    def time_sync_command(cls) -> bytes:
        """
        Build a time sync command with current time.

        The app calls writeSetTime on successful BLE connection via
        setConnectDeviceTime. Time fields map to the DB schema:
        year, month, day, hour, minute + timestamp.
        """
        now = datetime.now()
        # The app uses intToHex for each time component
        hex_str = cls.ints_to_hex([
            now.year >> 8, now.year & 0xFF,  # Year as 2 bytes
            now.month,
            now.day,
            now.hour,
            now.minute,
            now.second,
        ])
        return bytes.fromhex(hex_str)

    @classmethod
    def set_alarm_command(cls, hour: int, minute: int, pattern: int = 1,
                          intensity: int = 5, enabled: bool = True,
                          snooze: bool = False, alarm_id: int = 0,
                          repeat_days: list[int] = None) -> bytes:
        """
        Build a set-alarm command.

        Based on the app's operationSetAlarm function and DB schema.
        The app stores alarm data as hex_str and time_hex_str.

        Args:
            hour: 0-23
            minute: 0-59
            pattern: 1-9 vibration pattern (vibration_str)
            intensity: 1-9 vibration intensity (intensity_str)
            enabled: Whether alarm is active (status field)
            snooze: Whether snooze is enabled (is_snapze field, 9-min snooze)
            alarm_id: Alarm slot ID (id field)
            repeat_days: List of weekday ints for repeat (repeat_ids field)
        """
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("Invalid time")
        if not (1 <= pattern <= 9 and 1 <= intensity <= 9):
            raise ValueError("Pattern and intensity must be 1-9")

        flags = (1 if enabled else 0) | (2 if snooze else 0)
        repeat = 0
        if repeat_days:
            for day in repeat_days:
                repeat |= (1 << day)

        hex_str = cls.ints_to_hex([
            alarm_id, hour, minute, pattern, intensity, flags, repeat
        ])
        return bytes.fromhex(hex_str)

    @classmethod
    def delete_alarm_command(cls, alarm_id: int) -> bytes:
        """Build a delete-alarm command (writeDeleteMoreAlarmClock)."""
        return bytes.fromhex(cls.int_to_hex(alarm_id))

    @classmethod
    def delete_all_alarms_command(cls) -> bytes:
        """Build a delete-all-alarms command (writeDeleteAllAlarmClock)."""
        return bytes.fromhex("ff")

    @classmethod
    def vibrate_command(cls, pattern: int = 1, intensity: int = 5) -> bytes:
        """Build a vibration test command (writeVibrationTest)."""
        if not (1 <= pattern <= 9 and 1 <= intensity <= 9):
            raise ValueError("Pattern and intensity must be 1-9")
        hex_str = cls.ints_to_hex([pattern, intensity])
        return bytes.fromhex(hex_str)

    @classmethod
    def battery_request(cls) -> bytes:
        """Build a battery level request (writeBattery)."""
        return bytes.fromhex("00")

    @classmethod
    def get_alarm_clock(cls) -> bytes:
        """Build a get-alarm request (writeGetAlarmClock)."""
        return bytes.fromhex("01")


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
        """Connect to the WakeBand device and perform handshake."""
        self._client = BleakClient(self.address, timeout=self.timeout)
        await self._client.connect()
        print(f"[+] Connected to {self.address}")

        # Subscribe to notifications on all known notify characteristics
        notify_uuids = [WAKEBAND_NOTIFY_UUID] + ALL_WAKEBAND_UUIDS
        subscribed = []
        for uuid in notify_uuids:
            try:
                await self._client.start_notify(uuid, self._on_notify)
                subscribed.append(uuid[-8:-4])  # Short form for display
            except Exception:
                pass
        if subscribed:
            print(f"[+] Subscribed to notifications: {', '.join(subscribed)}")

        # Perform verification handshake (writeSendVerifyString)
        try:
            verify_cmd = WakeBandProtocol.verify_command()
            await self._client.write_gatt_char(WAKEBAND_WRITE_UUID, verify_cmd, response=False)
            print(f"[+] Sent verify handshake: {verify_cmd.hex()}")
        except Exception as e:
            print(f"[*] Verify handshake skipped: {e}")

        # Sync time on connect (setConnectDeviceTime)
        try:
            time_cmd = WakeBandProtocol.time_sync_command()
            await self._client.write_gatt_char(WAKEBAND_TIME_UUID, time_cmd, response=False)
            print(f"[+] Time synced: {time_cmd.hex()}")
        except Exception as e:
            print(f"[*] Time sync skipped: {e}")

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

    async def _send_command(self, data: bytes, write_uuid: str = None,
                            wait_response: bool = True,
                            response_timeout: float = 3.0) -> Optional[bytes]:
        """Send a command and optionally wait for response notification."""
        if write_uuid is None:
            write_uuid = WAKEBAND_WRITE_UUID
        self._notify_data.clear()
        self._notify_event.clear()

        try:
            await self._client.write_gatt_char(write_uuid, data, response=True)
        except Exception:
            await self._client.write_gatt_char(write_uuid, data, response=False)

        if wait_response:
            try:
                await asyncio.wait_for(self._notify_event.wait(), timeout=response_timeout)
                return self._notify_data[-1] if self._notify_data else None
            except asyncio.TimeoutError:
                return None

        return None

    async def read_battery(self) -> Optional[int]:
        """Read battery level (0-100%) via writeBattery / CHAR_550F."""
        try:
            # Try vendor-specific battery characteristic first
            value = await self._client.read_gatt_char(WAKEBAND_BATTERY_UUID)
            return value[0]
        except Exception:
            pass
        try:
            # Try standard BLE battery service
            value = await self._client.read_gatt_char(BATTERY_LEVEL_UUID)
            return value[0]
        except Exception:
            # Fall back to write-and-read
            cmd = WakeBandProtocol.battery_request()
            response = await self._send_command(cmd, write_uuid=WAKEBAND_BATTERY_UUID)
            if response and len(response) >= 1:
                return response[0]
            return None

    async def sync_time(self):
        """Sync current time to the device (writeSetTime -> CHAR_550A)."""
        cmd = WakeBandProtocol.time_sync_command()
        print(f"[*] Syncing time: {cmd.hex()}")
        await self._send_command(cmd, write_uuid=WAKEBAND_TIME_UUID, wait_response=True)

    async def set_alarm(self, hour: int, minute: int, pattern: int = 1,
                        intensity: int = 5, enabled: bool = True,
                        snooze: bool = False, alarm_id: int = 0,
                        repeat_days: list[int] = None):
        """
        Set an alarm on the WakeBand (operationSetAlarm).

        Snooze is fixed at 9 minutes (per app strings).
        Up to 10 alarms can be set (based on app's alarm list UI).
        """
        cmd = WakeBandProtocol.set_alarm_command(
            hour, minute, pattern, intensity, enabled, snooze, alarm_id, repeat_days
        )
        print(f"[*] Setting alarm {alarm_id}: {hour:02d}:{minute:02d} "
              f"pattern={pattern} intensity={intensity} snooze={snooze}: {cmd.hex()}")
        response = await self._send_command(cmd, write_uuid=WAKEBAND_WRITE_UUID)
        if response:
            print(f"[+] Device responded: {response.hex()}")

    async def delete_alarm(self, alarm_id: int):
        """Delete a specific alarm (writeDeleteMoreAlarmClock)."""
        cmd = WakeBandProtocol.delete_alarm_command(alarm_id)
        print(f"[*] Deleting alarm {alarm_id}: {cmd.hex()}")
        await self._send_command(cmd, write_uuid=WAKEBAND_WRITE_UUID)

    async def delete_all_alarms(self):
        """Delete all alarms (writeDeleteAllAlarmClock)."""
        cmd = WakeBandProtocol.delete_all_alarms_command()
        print(f"[*] Deleting all alarms: {cmd.hex()}")
        await self._send_command(cmd, write_uuid=WAKEBAND_WRITE_UUID)

    async def get_alarms(self) -> Optional[bytes]:
        """Read alarm list from device (writeGetAlarmClock -> readGetAlarmClockData)."""
        cmd = WakeBandProtocol.get_alarm_clock()
        print(f"[*] Requesting alarm data...")
        response = await self._send_command(cmd, write_uuid=WAKEBAND_ALARM_LIST_UUID)
        if response:
            print(f"[+] Alarm data: {response.hex()}")
        return response

    async def vibrate(self, pattern: int = 1, intensity: int = 5):
        """Trigger vibration test (writeVibrationTest)."""
        cmd = WakeBandProtocol.vibrate_command(pattern, intensity)
        print(f"[*] Vibrating: pattern={pattern} intensity={intensity}")
        await self._send_command(cmd, write_uuid=WAKEBAND_WRITE_UUID, wait_response=False)

    async def get_light_status(self) -> Optional[bytes]:
        """Read LED light status (readSetLightStatus via CHAR_5507)."""
        try:
            value = await self._client.read_gatt_char(WAKEBAND_LIGHT_UUID)
            print(f"[*] Light status: {value.hex()}")
            return value
        except Exception as e:
            print(f"[-] Could not read light status: {e}")
            return None

    async def factory_reset(self):
        """Factory reset the device (writeResetDevice with "set+reset")."""
        cmd = WakeBandProtocol.reset_command()
        print(f"[!] Factory resetting device: {cmd.hex()}")
        await self._send_command(cmd, write_uuid=WAKEBAND_WRITE_UUID, wait_response=False)

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


async def cmd_vibrate(address: str, pattern: int, intensity: int):
    """Trigger vibration."""
    async with WakeBand(address) as wb:
        await wb.vibrate(pattern, intensity)


async def cmd_sniff(address: str, duration: float):
    """Sniff BLE notifications."""
    async with WakeBand(address) as wb:
        await wb.sniff(duration)


async def async_with_wb(address: str, func):
    """Helper to run a function with a connected WakeBand."""
    async with WakeBand(address) as wb:
        await func(wb)


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

    p = sub.add_parser("vibrate", help="Trigger vibration test")
    p.add_argument("--pattern", type=int, default=1, help="Vibration pattern (1-9)")
    p.add_argument("--intensity", type=int, default=5, help="Vibration intensity (1-9)")

    sub.add_parser("get-alarms", help="Read alarm list from device")
    sub.add_parser("delete-all-alarms", help="Delete all alarms")
    p = sub.add_parser("delete-alarm", help="Delete a specific alarm")
    p.add_argument("alarm_id", type=int, help="Alarm ID to delete")
    sub.add_parser("light-status", help="Read LED light status")
    sub.add_parser("reset", help="Factory reset the device")

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
        asyncio.run(cmd_vibrate(args.addr, args.pattern, args.intensity))
    elif args.command == "get-alarms":
        if not args.addr:
            print("Error: --addr required")
            sys.exit(1)
        asyncio.run(async_with_wb(args.addr, lambda wb: wb.get_alarms()))
    elif args.command == "delete-all-alarms":
        if not args.addr:
            print("Error: --addr required")
            sys.exit(1)
        asyncio.run(async_with_wb(args.addr, lambda wb: wb.delete_all_alarms()))
    elif args.command == "delete-alarm":
        if not args.addr:
            print("Error: --addr required")
            sys.exit(1)
        asyncio.run(async_with_wb(args.addr, lambda wb: wb.delete_alarm(args.alarm_id)))
    elif args.command == "light-status":
        if not args.addr:
            print("Error: --addr required")
            sys.exit(1)
        asyncio.run(async_with_wb(args.addr, lambda wb: wb.get_light_status()))
    elif args.command == "reset":
        if not args.addr:
            print("Error: --addr required")
            sys.exit(1)
        asyncio.run(async_with_wb(args.addr, lambda wb: wb.factory_reset()))
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
