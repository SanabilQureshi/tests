# WakeBand Linux Control Toolkit

Reverse-engineering toolkit for the **Homedics WakeBand** silent vibrating alarm wristband.
Control your WakeBand programmatically from Linux via BLE instead of using the official app.

## Quick Start

```bash
pip install bleak pycryptodome

# Step 1: Find your WakeBand
python3 wakeband.py scan

# Step 2: Discover its BLE services and characteristics
python3 wakeband_discover.py --addr XX:XX:XX:XX:XX:XX --monitor

# Step 3: Update UUIDs in wakeband.py, then control it
python3 wakeband.py set-alarm 07:30 --pattern 3 --intensity 5 --addr XX:XX:XX:XX:XX:XX
python3 wakeband.py vibrate --pattern 1 --intensity 9 --addr XX:XX:XX:XX:XX:XX
```

## Toolkit Contents

| File | Purpose |
|------|---------|
| `wakeband.py` | Main control library and CLI tool |
| `wakeband_discover.py` | BLE service/characteristic discovery |
| `firmware_analyze.py` | Firmware binary analysis and decryption |
| `db_update_data.bin` | WakeBand firmware v1.8.0 (encrypted) |

## Reverse Engineering Guide

### Phase 1: BLE Discovery (no app needed)

The WakeBand communicates over Bluetooth Low Energy (BLE). To discover its protocol:

1. **Scan for the device:**
   ```bash
   python3 wakeband_discover.py --all
   ```

2. **Connect and dump GATT table:**
   ```bash
   python3 wakeband_discover.py --addr XX:XX:XX:XX:XX:XX --monitor
   ```
   This reveals all services, characteristics, and their properties (read/write/notify).

3. **Update UUIDs** in `wakeband.py` based on what you find.

### Phase 2: Protocol Capture (with Android phone)

To decode the actual command format, capture BLE traffic from the official app:

1. **Enable HCI snoop logging** on Android:
   - Settings > Developer Options > Enable Bluetooth HCI Snoop Log
   - Or: `adb shell settings put secure bluetooth_hci_log 1`

2. **Use the WakeBand app** (`com.homedics.bracelet`) to:
   - Set an alarm
   - Change vibration pattern/intensity
   - Enable/disable snooze
   - Check battery

3. **Pull the HCI log:**
   ```bash
   adb pull /sdcard/btsnoop_hci.log
   ```

4. **Analyze in Wireshark:**
   ```bash
   wireshark btsnoop_hci.log
   ```
   Filter: `btatt.opcode == 0x12` (Write Request) or `btatt.opcode == 0x1b` (Handle Value Notification)

5. **Alternative: use nRF Connect** app to read/write characteristics interactively.

### Phase 3: Firmware Analysis

The firmware file (`db_update_data.bin`) is AES-128-CBC encrypted:

```bash
python3 firmware_analyze.py db_update_data.bin
```

**Structure:**
```
Offset  Size   Description
------  -----  -----------
0x0000  2      Header prefix (0x643b)
0x0002  16     AES-128-CBC initialization vector
0x0012  176KB  AES-128-CBC encrypted firmware payload (11264 blocks)
```

**Key findings:**
- Firmware size: 180,242 bytes (18-byte header + 180,224-byte payload)
- Payload = exactly 176KB = 0x2C000 bytes = 11,264 AES blocks
- Entropy: 7.99 bits/byte (maximum = encrypted)
- No ECB block repetition (confirms CBC mode)
- Likely target SoC: **Telink TLSR82xx** (common in BLE wristbands)
- Telink firmware has magic bytes "KNLT" at plaintext offset 8

**To decrypt the firmware**, extract the AES key from the Android APK:
```bash
# Download APK (com.homedics.bracelet) from APKCombo or use adb
adb shell pm path com.homedics.bracelet
adb pull /data/app/.../base.apk wakeband.apk

# Decompile
jadx -d wakeband_src wakeband.apk

# Find the encryption key
grep -r "AES\|SecretKey\|Cipher\|decrypt\|encrypt" wakeband_src/ --include="*.java"
grep -r "db_update\|firmware\|ota\|update" wakeband_src/ --include="*.java"

# Decrypt with found key
python3 firmware_analyze.py db_update_data.bin --key <32_hex_chars>
```

## WakeBand Specifications

| Property | Value |
|----------|-------|
| Product | Homedics WakeBand Silent Alarm |
| App package | `com.homedics.bracelet` (Android) |
| Firmware version | 1.8.0 |
| App version | 1.2.0 |
| Vibration patterns | 9 |
| Intensity levels | 9 |
| Battery life | ~6 days |
| Connectivity | Bluetooth Low Energy (BLE) |
| Likely BLE SoC | Telink TLSR82xx |
| Firmware repos | [greatpower-sz/WakeBand](https://github.com/greatpower-sz/WakeBand), [xxz520-zhx/wakeband](https://github.com/xxz520-zhx/wakeband) |

## Library Usage

```python
import asyncio
from wakeband import WakeBand

async def main():
    async with WakeBand("XX:XX:XX:XX:XX:XX") as wb:
        # Discover services (do this first!)
        await wb.discover_services()

        # Read battery
        battery = await wb.read_battery()
        print(f"Battery: {battery}%")

        # Set alarm: 7:30 AM, pattern 3, intensity 5, with snooze
        await wb.set_alarm(7, 30, pattern=3, intensity=5, snooze=True)

        # Test vibration
        await wb.vibrate(pattern=1, intensity=9, duration=2)

        # Write raw bytes (for experimentation)
        await wb.raw_write("0000ff01-...", bytes.fromhex("010203"))

        # Sniff all notifications (while using official app on another phone)
        await wb.sniff(duration=120)

asyncio.run(main())
```

## Troubleshooting

**"No WakeBand devices found"**
- Make sure the band is not connected to the official app (only one BLE connection at a time)
- Check Bluetooth is enabled: `bluetoothctl power on`
- Try: `bluetoothctl scan on` to verify your adapter works

**"Permission denied"**
- Run with sudo, or add your user to the `bluetooth` group:
  ```bash
  sudo usermod -aG bluetooth $USER
  ```

**"UUIDs not found"**
- The placeholder UUIDs in `wakeband.py` need to be replaced with actual ones from your device
- Run `wakeband_discover.py` first

## Dependencies

```bash
pip install bleak pycryptodome
```

- **bleak**: Cross-platform BLE library (Linux/macOS/Windows)
- **pycryptodome**: AES decryption for firmware analysis
- **Python 3.10+**
- **BlueZ 5.43+**: Linux Bluetooth stack (`sudo apt install bluez`)
