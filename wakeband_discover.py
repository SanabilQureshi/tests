#!/usr/bin/env python3
"""
WakeBand BLE Discovery Tool

Scans for Homedics WakeBand devices, connects, and dumps all GATT
services, characteristics, and descriptors. This is the first step
in reverse-engineering the BLE protocol — run this with your WakeBand
nearby to discover the UUIDs and data formats used.

Requirements:
    pip install bleak

Usage:
    python3 wakeband_discover.py              # Scan and discover
    python3 wakeband_discover.py --monitor    # Also subscribe to notifications
    python3 wakeband_discover.py --addr XX:XX:XX:XX:XX:XX  # Connect to specific address
"""

import argparse
import asyncio
import struct
import sys
from datetime import datetime

from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic

# Known WakeBand identifiers (update these after first scan)
WAKEBAND_NAMES = ["WakeBand", "Wakeband", "WAKEBAND", "HMD-", "Homedics"]
WAKEBAND_SERVICE_UUIDS: list[str] = []  # Populate after discovery


def format_properties(char: BleakGATTCharacteristic) -> str:
    """Format characteristic properties as a readable string."""
    props = []
    for p in char.properties:
        props.append(p)
    return ", ".join(props)


def format_value(data: bytes) -> str:
    """Format raw bytes as hex + ASCII + common interpretations."""
    if not data:
        return "(empty)"
    hex_str = data.hex()
    ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in data)
    parts = [f"hex={hex_str}", f'ascii="{ascii_str}"']

    # Try common integer interpretations
    if len(data) == 1:
        parts.append(f"uint8={data[0]}")
    elif len(data) == 2:
        parts.append(f"uint16_le={struct.unpack('<H', data)[0]}")
    elif len(data) == 4:
        parts.append(f"uint32_le={struct.unpack('<I', data)[0]}")

    return " | ".join(parts)


def is_wakeband(device) -> bool:
    """Check if a discovered BLE device might be a WakeBand."""
    name = device.name or ""
    for known in WAKEBAND_NAMES:
        if known.lower() in name.lower():
            return True
    return False


async def scan_for_devices(timeout: float = 10.0) -> list:
    """Scan for BLE devices and identify potential WakeBand devices."""
    print(f"[*] Scanning for BLE devices ({timeout}s)...")
    devices = await BleakScanner.discover(timeout=timeout, return_adv=True)

    wakebands = []
    other = []

    for device, adv_data in devices.values():
        entry = {
            "device": device,
            "adv": adv_data,
            "name": device.name or adv_data.local_name or "(unknown)",
            "address": device.address,
            "rssi": adv_data.rssi,
            "service_uuids": adv_data.service_uuids,
            "manufacturer_data": adv_data.manufacturer_data,
        }
        if is_wakeband(device) or (adv_data.local_name and is_wakeband(type("", (), {"name": adv_data.local_name})())):
            wakebands.append(entry)
        else:
            other.append(entry)

    return wakebands, other


async def dump_gatt_table(address: str, monitor: bool = False):
    """Connect to a device and dump its complete GATT table."""
    print(f"\n[*] Connecting to {address}...")

    async with BleakClient(address, timeout=20.0) as client:
        print(f"[+] Connected: {client.is_connected}")
        print(f"[+] MTU size: {client.mtu_size}")

        services = client.services
        print(f"\n{'='*70}")
        print(f" GATT SERVICE TABLE")
        print(f"{'='*70}")

        notification_chars = []

        for service in services:
            print(f"\n[Service] {service.uuid}")
            print(f"  Description: {service.description}")
            print(f"  Handle: 0x{service.handle:04x}")

            for char in service.characteristics:
                props = format_properties(char)
                print(f"\n  [Characteristic] {char.uuid}")
                print(f"    Description: {char.description}")
                print(f"    Handle: 0x{char.handle:04x}")
                print(f"    Properties: {props}")

                # Try to read if readable
                if "read" in char.properties:
                    try:
                        value = await client.read_gatt_char(char)
                        print(f"    Value: {format_value(value)}")
                    except Exception as e:
                        print(f"    Value: (read failed: {e})")

                # Track notify/indicate characteristics
                if "notify" in char.properties or "indicate" in char.properties:
                    notification_chars.append(char)
                    print(f"    ** Supports notifications **")

                # Dump descriptors
                for desc in char.descriptors:
                    print(f"    [Descriptor] {desc.uuid}")
                    print(f"      Description: {desc.description}")
                    try:
                        value = await client.read_gatt_descriptor(desc.handle)
                        print(f"      Value: {format_value(value)}")
                    except Exception as e:
                        print(f"      Value: (read failed: {e})")

        # Monitor notifications if requested
        if monitor and notification_chars:
            print(f"\n{'='*70}")
            print(f" MONITORING NOTIFICATIONS (Ctrl+C to stop)")
            print(f"{'='*70}")

            def make_handler(char_uuid):
                def handler(sender, data):
                    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                    print(f"  [{timestamp}] {char_uuid}: {format_value(data)}")
                return handler

            for char in notification_chars:
                try:
                    await client.start_notify(char, make_handler(char.uuid))
                    print(f"[+] Subscribed to {char.uuid}")
                except Exception as e:
                    print(f"[-] Failed to subscribe to {char.uuid}: {e}")

            print("\n[*] Listening... (Press Ctrl+C to stop)")
            try:
                while True:
                    await asyncio.sleep(1)
            except KeyboardInterrupt:
                print("\n[*] Stopping...")

            for char in notification_chars:
                try:
                    await client.stop_notify(char)
                except Exception:
                    pass

        print(f"\n{'='*70}")
        print(f" DISCOVERY COMPLETE")
        print(f"{'='*70}")


async def write_and_observe(address: str, char_uuid: str, data: bytes):
    """Write data to a characteristic and observe the response."""
    print(f"[*] Writing {data.hex()} to {char_uuid} on {address}...")

    async with BleakClient(address, timeout=20.0) as client:
        # Subscribe to all notify characteristics first
        services = client.services
        for service in services:
            for char in service.characteristics:
                if "notify" in char.properties or "indicate" in char.properties:
                    try:
                        def handler(sender, received):
                            timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                            print(f"  [{timestamp}] NOTIFY {sender}: {format_value(received)}")
                        await client.start_notify(char, handler)
                    except Exception:
                        pass

        # Write the data
        try:
            await client.write_gatt_char(char_uuid, data, response=True)
            print(f"[+] Write successful (with response)")
        except Exception:
            try:
                await client.write_gatt_char(char_uuid, data, response=False)
                print(f"[+] Write successful (without response)")
            except Exception as e:
                print(f"[-] Write failed: {e}")

        # Wait for any notifications
        print("[*] Waiting 3s for responses...")
        await asyncio.sleep(3)


async def main():
    parser = argparse.ArgumentParser(description="WakeBand BLE Discovery Tool")
    parser.add_argument("--addr", help="Connect to specific BLE address")
    parser.add_argument("--monitor", action="store_true", help="Monitor notifications after discovery")
    parser.add_argument("--scan-time", type=float, default=10.0, help="Scan duration in seconds")
    parser.add_argument("--write", help="Write hex data to a characteristic (use with --char)")
    parser.add_argument("--char", help="Characteristic UUID for --write")
    parser.add_argument("--all", action="store_true", help="Show all discovered BLE devices")
    args = parser.parse_args()

    if args.write and args.char and args.addr:
        await write_and_observe(args.addr, args.char, bytes.fromhex(args.write))
        return

    address = args.addr

    if not address:
        wakebands, other = await scan_for_devices(args.scan_time)

        if wakebands:
            print(f"\n[+] Found {len(wakebands)} potential WakeBand device(s):")
            for i, wb in enumerate(wakebands):
                print(f"  [{i}] {wb['name']} ({wb['address']}) RSSI={wb['rssi']}dBm")
                if wb["service_uuids"]:
                    print(f"      Service UUIDs: {wb['service_uuids']}")
                if wb["manufacturer_data"]:
                    for company_id, mfr_data in wb["manufacturer_data"].items():
                        print(f"      Manufacturer data (0x{company_id:04x}): {mfr_data.hex()}")

            if len(wakebands) == 1:
                address = wakebands[0]["address"]
            else:
                idx = int(input("\nSelect device index: "))
                address = wakebands[idx]["address"]
        else:
            print("\n[-] No WakeBand devices found.")
            if args.all and other:
                print(f"\n[*] Other BLE devices ({len(other)}):")
                for dev in sorted(other, key=lambda d: d["rssi"], reverse=True):
                    print(f"  {dev['name']:30s} {dev['address']} RSSI={dev['rssi']}dBm")
                    if dev["service_uuids"]:
                        print(f"    Service UUIDs: {dev['service_uuids']}")
            print("\nTip: Use --all to see all devices, or --addr to connect directly.")
            return

    await dump_gatt_table(address, monitor=args.monitor)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[*] Interrupted.")
    except Exception as e:
        print(f"\n[!] Error: {e}")
        sys.exit(1)
