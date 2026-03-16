#!/usr/bin/env python3
"""
WakeBand BLE Service Discovery Tool

Discovers the BLE services and characteristics exposed by the WakeBand device.
Run this first to identify the exact UUIDs needed for communication.

Requirements: pip install bleak
"""

import asyncio
import sys
from bleak import BleakScanner, BleakClient

DEVICE_NAME = "WakeBand"
SCAN_TIMEOUT = 15  # seconds


async def scan_for_wakeband():
    """Scan for WakeBand devices."""
    print(f"Scanning for '{DEVICE_NAME}' devices ({SCAN_TIMEOUT}s timeout)...")
    devices = await BleakScanner.discover(timeout=SCAN_TIMEOUT)

    wakebands = []
    for d in devices:
        if d.name and DEVICE_NAME.lower() in d.name.lower():
            wakebands.append(d)
            print(f"  Found: {d.name} [{d.address}] RSSI={d.rssi}")

    if not wakebands:
        print(f"\nNo '{DEVICE_NAME}' devices found.")
        print("Make sure the device is powered on (hold button 2s) and nearby.")
        # Show all found devices for debugging
        print("\nAll BLE devices found:")
        for d in sorted(devices, key=lambda x: x.rssi or -999, reverse=True):
            name = d.name or "(unknown)"
            print(f"  {name:30s} [{d.address}] RSSI={d.rssi}")
    return wakebands


async def discover_services(address: str):
    """Connect to a WakeBand and discover all GATT services."""
    print(f"\nConnecting to {address}...")

    async with BleakClient(address, timeout=20.0) as client:
        print(f"Connected: {client.is_connected}")
        print(f"MTU: {client.mtu_size}")
        print()

        services = client.services
        write_chars = []
        notify_chars = []
        read_chars = []

        for service in services:
            print(f"SERVICE: {service.uuid}")
            print(f"  Description: {service.description}")

            for char in service.characteristics:
                props = char.properties
                props_str = ", ".join(props)
                print(f"  CHARACTERISTIC: {char.uuid}")
                print(f"    Properties: {props_str}")
                print(f"    Handle: 0x{char.handle:04X}")

                if "write" in props or "write-without-response" in props:
                    write_chars.append(char)
                if "notify" in props or "indicate" in props:
                    notify_chars.append(char)
                if "read" in props:
                    read_chars.append(char)

                for desc in char.descriptors:
                    print(f"    DESCRIPTOR: {desc.uuid} (handle=0x{desc.handle:04X})")
                    try:
                        val = await client.read_gatt_descriptor(desc.handle)
                        print(f"      Value: {val.hex()}")
                    except Exception as e:
                        print(f"      Read error: {e}")

                # Try reading readable characteristics
                if "read" in props:
                    try:
                        val = await client.read_gatt_char(char.uuid)
                        print(f"    Value: {val.hex()} ({val})")
                    except Exception as e:
                        print(f"    Read error: {e}")

            print()

        # Summary
        print("=" * 60)
        print("SUMMARY")
        print("=" * 60)
        print(f"\nWrite characteristics ({len(write_chars)}):")
        for c in write_chars:
            print(f"  {c.uuid} [{', '.join(c.properties)}]")

        print(f"\nNotify characteristics ({len(notify_chars)}):")
        for c in notify_chars:
            print(f"  {c.uuid} [{', '.join(c.properties)}]")

        print(f"\nRead characteristics ({len(read_chars)}):")
        for c in read_chars:
            print(f"  {c.uuid} [{', '.join(c.properties)}]")

        # Generate config snippet
        if write_chars and notify_chars:
            print("\n" + "=" * 60)
            print("CONFIGURATION FOR wakeband_control.py")
            print("=" * 60)
            print(f'\nSERVICE_UUID = "{write_chars[0].service_uuid}"')
            print(f'WRITE_CHAR_UUID = "{write_chars[0].uuid}"')
            print(f'NOTIFY_CHAR_UUID = "{notify_chars[0].uuid}"')
            print(f'DEVICE_ADDRESS = "{address}"')
            print("\nCopy these values into wakeband_control.py to configure it.")

        return write_chars, notify_chars


async def main():
    address = None
    if len(sys.argv) > 1:
        address = sys.argv[1]
        print(f"Using provided address: {address}")
    else:
        wakebands = await scan_for_wakeband()
        if wakebands:
            address = wakebands[0].address
            if len(wakebands) > 1:
                print(f"\nMultiple devices found. Using first: {address}")
                print("Specify address as argument to choose: python3 wakeband_discover.py XX:XX:XX:XX:XX:XX")

    if address:
        await discover_services(address)


if __name__ == "__main__":
    asyncio.run(main())
