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

# Step 3: Control it
python3 wakeband.py set-alarm 07:30 --pattern 3 --intensity 5 --addr XX:XX:XX:XX:XX:XX
python3 wakeband.py vibrate --pattern 1 --intensity 9 --addr XX:XX:XX:XX:XX:XX
python3 wakeband.py battery --addr XX:XX:XX:XX:XX:XX
```

## Toolkit Contents

| File | Purpose |
|------|---------|
| `wakeband.py` | Main control library and CLI tool (real UUIDs from APK) |
| `wakeband_discover.py` | BLE service/characteristic discovery |
| `firmware_analyze.py` | Firmware binary analysis and decryption |
| `db_update_data.bin` | WakeBand firmware v1.8.0 (encrypted) |

## BLE Protocol (Reverse-Engineered from APK)

The protocol was extracted by decompiling the official Android app (`com.homedics.bracelet`
v1.2.0) using jadx. The app is built with **Flutter** (Dart AOT compiled to `libapp.so`)
using **flutter_blue_plus** for BLE communication.

### Characteristic UUIDs

The WakeBand uses custom 16-bit UUIDs in the Bluetooth Base UUID format
(`0000XXXX-0000-1000-8000-00805f9b34fb`):

| Short UUID | Full UUID | Function |
|------------|-----------|----------|
| `5501` | `00005501-...` | Primary write - alarm commands |
| `5502` | `00005502-...` | Device MAC / identification |
| `5503` | `00005503-...` | General purpose / completion |
| `5504` | `00005504-...` | Selection / config |
| `5505` | `00005505-...` | UI sync |
| `5506` | `00005506-...` | Intensity settings |
| `5507` | `00005507-...` | LED/light status |
| `5508` | `00005508-...` | Encrypted data / firmware (ENCRYPTED_SIZE) |
| `5509` | `00005509-...` | Editable settings |
| `550A` | `0000550a-...` | Time sync (timerMillisecondClock) |
| `550C` | `0000550c-...` | Scan/notify start |
| `550D` | `0000550d-...` | Elements / settings |
| `550E` | `0000550e-...` | Alarm list operations |
| `550F` | `0000550f-...` | Battery (writeBattery) |
| `5510` | `00005510-...` | Async operations |
| `5511` | `00005511-...` | Device list / launch |
| `AC00` | `0000ac00-...` | Bluetooth connection state |
| `AC01` | `0000ac01-...` | Event routing / chaining |
| `AC02` | `0000ac02-...` | Bind/unbind management |

Standard UUIDs also used: `1801` (Generic Attribute), `2A05` (Service Changed),
`2902` (CCCD).

### Command Protocol

Commands are hex-encoded byte strings written to specific characteristics.
The app uses `intToHex`/`intsToHex` helpers to build command payloads.

**Verification handshake** (on connect):
```
writeSendVerifyString -> CHAR_5501: "636865636b" = hex("check")
readGetVerifyString   <- response from device
```

**Time sync** (on connect, via setConnectDeviceTime):
```
writeSetTime -> CHAR_550A: [year_hi, year_lo, month, day, hour, min, sec]
readSetTimeResult <- confirmation
```

**Set alarm** (operationSetAlarm):
```
write -> CHAR_5501: [alarm_id, hour, minute, pattern, intensity, flags, repeat]
  flags: bit0=enabled, bit1=snooze
  repeat: bitmask of weekdays
readGetAlarmClockFinishResult <- confirmation
```

**Delete alarms**:
```
writeDeleteMoreAlarmClock -> CHAR_5501: [alarm_id]
writeDeleteAllAlarmClock  -> CHAR_5501: [0xff]
```

**Factory reset**:
```
writeResetDevice -> CHAR_5501: "7365742b7265736574" = hex("set+reset")
```

**Vibration test**:
```
writeVibrationTest -> CHAR_5501: [pattern, intensity]
readVibrationTestResult <- response
```

**Battery**:
```
writeBattery -> CHAR_550F: [0x00]
read CHAR_550F <- battery level
```

### App Functions (from Dart AOT snapshot)

Write operations (phone -> device):
- `writeSetTime` - Sync time
- `writeGetAlarmClock` - Request alarm data
- `writeResetDevice` - Factory reset
- `writeDeleteAllAlarmClock` / `writeDeleteMoreAlarmClock` - Delete alarms
- `writeBattery` - Battery request
- `writeSendVerifyString` / `writeVerifyString` - Auth handshake
- `writeVibrationTest` - Test vibration

Read responses (device -> phone):
- `readGetAlarmClockData` - Alarm data
- `readGetAlarmClockFinishResult` - Alarm set confirmation
- `readGetVerifyString` / `readVerifyStringResult` - Auth response
- `readSetTimeResult` - Time sync confirmation
- `readSetLightStatus` - LED status
- `readDeleteMoreAlarmClockResult` / `readDeleteAllAlarmClockResult`
- `readVibrationTestResult`

### Alarm Database Schema

The app stores alarms locally with these fields:
```sql
id INTEGER PRIMARY KEY, hour, minute, day, month, year, timestamp,
hex_str TEXT, time_hex_str TEXT,
vibration_str TEXT, vibration_price TEXT,
intensity_str TEXT, intensity_price TEXT,
repeat_ids TEXT, repeat_str TEXT, colour INTEGER,
status INTEGER, is_snapze INTEGER (snooze, 9 min),
is_one INTEGER (one-time), is_default INTEGER,
is_delete INTEGER, is_require_bind INTEGER,
is_require_unbind INTEGER, is_require_edit INTEGER,
light_status INTEGER, instruction TEXT
```

### Firmware Analysis

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

- Firmware size: 180,242 bytes (18-byte header + 180,224-byte payload)
- Payload = exactly 176KB = 0x2C000 bytes = 11,264 AES blocks
- Entropy: 7.99 bits/byte (maximum = encrypted)
- No ECB block repetition (confirms CBC mode)
- Likely target SoC: **Telink TLSR82xx** (common in BLE wristbands)
- Telink firmware has magic bytes "KNLT" at plaintext offset 8
- OTA via CHAR_5508 (references ENCRYPTED_SIZE in APK)

The AES key is not stored as a plaintext string in the APK. It is likely:
- Derived at runtime from device-specific data, or
- Embedded in the compiled Dart snapshot's object pool (needs Dart VM snapshot parsing), or
- Part of the Telink OTA bootloader protocol (standardized key exchange)

## WakeBand Specifications

| Property | Value |
|----------|-------|
| Product | Homedics WakeBand Silent Alarm |
| App package | `com.homedics.bracelet` (Android, Flutter) |
| BLE library | flutter_blue_plus |
| Firmware version | 1.8.0 |
| App version | 1.2.0 |
| Vibration patterns | 9 |
| Intensity levels | 9 |
| Snooze duration | 9 minutes (fixed) |
| Max alarms | ~10 (app UI limit) |
| Battery life | ~2+ weeks (per app FAQ) |
| Connectivity | Bluetooth 5.0 (BLE) |
| Minimum OS | iOS 12 / Android 8 |
| Likely BLE SoC | Telink TLSR82xx |
| BLE characteristics | 0x5501-0x5511, 0xAC00-0xAC02 |
| Manufacturer | greatpower-sz (Shenzhen) |
| Firmware repos | [greatpower-sz/WakeBand](https://github.com/greatpower-sz/WakeBand) |

## Library Usage

```python
import asyncio
from wakeband import WakeBand

async def main():
    async with WakeBand("XX:XX:XX:XX:XX:XX") as wb:
        # Auto-connects, verifies ("check"), and syncs time

        # Read battery
        battery = await wb.read_battery()
        print(f"Battery: {battery}%")

        # Set alarm: 7:30 AM, pattern 3, intensity 5, with snooze
        await wb.set_alarm(7, 30, pattern=3, intensity=5, snooze=True)

        # Set repeating alarm (Mon-Fri)
        await wb.set_alarm(6, 45, pattern=2, intensity=7, repeat_days=[1,2,3,4,5])

        # Read all alarms from device
        await wb.get_alarms()

        # Delete specific alarm
        await wb.delete_alarm(alarm_id=0)

        # Test vibration
        await wb.vibrate(pattern=1, intensity=9)

        # Read LED status
        await wb.get_light_status()

        # Write raw bytes to any characteristic
        await wb.raw_write("00005501-0000-1000-8000-00805f9b34fb", bytes.fromhex("010203"))

        # Sniff all notifications (while using official app on another phone)
        await wb.sniff(duration=120)

        # Factory reset (careful!)
        # await wb.factory_reset()

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

## Dependencies

```bash
pip install bleak pycryptodome
```

- **bleak**: Cross-platform BLE library (Linux/macOS/Windows)
- **pycryptodome**: AES decryption for firmware analysis
- **Python 3.10+**
- **BlueZ 5.43+**: Linux Bluetooth stack (`sudo apt install bluez`)
