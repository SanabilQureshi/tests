# HoMedics WakeBand BLE Protocol Analysis

## Overview

The WakeBand is a haptic alarm wristband by HoMedics (manufactured by Greatpower-SZ).
The companion app (`com.homedics.bracelet` v1.2.0) is a Flutter app using `flutter_blue_plus`
for BLE communication. The device firmware version is 1.8.0.

## Device Identification

- **Advertised BLE Name**: `WakeBand`
- **BLE Feature**: Bluetooth 5.0 (BLE)
- **Compatibility**: iOS 12+, Android 8+
- **App Package**: `com.homedics.bracelet`
- **Manufacturer**: Greatpower-SZ (Shenzhen)
- **Firmware Updates**: https://raw.githubusercontent.com/greatpower-sz/WakeBand/main/WakeBandFirmwareVersion.txt

## BLE GATT UUIDs

The app hardcodes three 16-bit UUIDs in `BluetoothManage._internal()`:

| UUID | Full UUID (128-bit) | Role |
|------|---------------------|------|
| **AC00** | `0000AC00-0000-1000-8000-00805F9B34FB` | **Service UUID** |
| **AC01** | `0000AC01-0000-1000-8000-00805F9B34FB` | **Notify characteristic** (device → app) |
| **AC02** | `0000AC02-0000-1000-8000-00805F9B34FB` | **Write characteristic** (app → device) |

The app discovers services dynamically but matches characteristics by checking if
the characteristic UUID string contains `"AC01"` or `"AC02"` within service `"AC00"`.

## Command Frame Format

All commands use hex string encoding internally. The frame format is:

```
┌────────┬────────┬───────────┬─────────┬──────────┐
│ Header │ Length │ Command   │ Payload │ Checksum │
│ 1 byte │ 1 byte│ 2 bytes   │ N bytes │ 1 byte   │
└────────┴────────┴───────────┴─────────┴──────────┘
```

### Regular Commands (Header: 0x5A)

Built by `packBleData(command, data)`:

1. **Header**: `0x5A` (fixed)
2. **Length**: Number of payload bytes only (NOT including command), as a single byte
   - Computed by `calculateDataLength(data)`: `len(data_hex_string) / 2`
3. **Command**: 2-byte command ID (e.g., `0x55 0x09`)
4. **Payload**: Variable-length data (may be empty)
5. **Checksum**: `(sum_of_all_bytes_except_header - 1) & 0xFF`
   - Sum covers: length byte + command bytes + payload bytes
   - Then subtract 1, then AND with 0xFF

### Firmware Update Commands (Header: 0xE5)

Built by `packBleUpdateData()` — used only for firmware updates.

### Checksum Algorithm (`checkNum`)

```python
def checksum(data_bytes):
    """data_bytes includes length + command + payload (NOT the header 0x5A)"""
    return (sum(data_bytes) - 1) & 0xFF
```

## Command ID Table (App → Device)

All commands are written to characteristic **AC02**.

| BleWriteType | Command ID | Payload | Purpose |
|---|---|---|---|
| 0 (writeVerifyString) | `550C` | `636865636B` (ASCII "check") | Initial connection verification |
| 1 (writeSendVerifyString) | `550E` | (random code from device) | Send back verification code |
| 2 (writeSetTime) | `5501` | time data (see below) | Sync current time |
| 3 (writeBattery) | `5502` | `00` | Request battery level |
| 4 (writeGetAlarmClock) | `5503` | `00` | Request alarm count |
| 5 (writeLightStatus) | `5510` | light status data | Control LED |
| 6 (writeAddAlarmClock) | `5504` | alarm hex data | Add a new alarm |
| 7 (writeEditAlarmClock) | `5505` | alarm hex data | Edit existing alarm |
| 8 (writeVibrationTest) | `5509` | mode + intensity | **Trigger vibration** |
| 9 (writeGetAlarmList) | `5507` | `00` | Request alarm data list |
| 10 (writeDeleteMoreAlarmClock) | `5506` | `00` | Delete specific alarm(s) |
| 11 (writeDeleteAllAlarmClock) | `550A` | (data) | Delete all alarms |
| 12 (writeUpdateMD5Data) | `0901` | MD5 data | Firmware update: MD5 |
| 13 (writeUpdateData) | `0902` | firmware data | Firmware update: data |

## Response ID Table (Device → App, via AC01 notifications)

Responses are received as notifications on characteristic **AC01**.
The app reads `substring(0, 4)` of the hex string to identify the response type.

| Response ID | BleReadType | Purpose |
|---|---|---|
| `550E` | readGetVerifyString | Device sends random verification code |
| `550F` | readVerifyStringResult | Verification result |
| `5501` | readSetTimeResult | Time sync confirmation |
| `5502` | readBatteryResult | Battery/standby time data |
| `5503` | readGetAlarmClockData | Alarm data (one alarm per response) |
| `5504` | readAddAlarmClockResult | Add alarm confirmation |
| `5505` | readEditAlarmClockResult | Edit alarm confirmation |
| `5506` | readDeleteMoreAlarmClockResult | Delete alarm confirmation |
| `5507` | readGetAlarmClockFinishResult | All alarms retrieved signal |
| `5509` | readVibrationTestResult | Vibration test confirmation |
| `550A` | readDeleteAllAlarmClockResult | Delete all confirmation |
| `5510` | readSetLightStatus | Light status confirmation |

### Connection/Authentication Flow

1. App discovers services, finds AC00 service with AC01 (notify) and AC02 (write)
2. App subscribes to notifications on AC01
3. Received data starting with `"A5"` → normal response processing
4. Received data starting with `"5E"` → ignored (likely keep-alive or status)
5. App sends `writeVerifyString` (`550C` + `"636865636B"` = "check") to device
6. Device responds with `550E` containing a random verification code
7. If app has stored verification data, it checks; otherwise treats as new pairing
8. On success ("01" in response), app emits "connectSuccess" event

## Vibration Test Command Details

**Command ID**: `5509`

**Payload**: `intToHex(mode) + intToHex(intensity)`
- `mode`: 0-8 (vibration pattern index)
- `intensity`: 0-8 (vibration strength index)

Each value is converted to a 2-character hex string (zero-padded).

### Example: Vibration Test (mode=0 "Steady Vibe", intensity=4)

```
Payload: "0004"  (mode=0x00, intensity=0x04)
Command: "5509"
Length: len("0004") / 2 = 2 → "02"  (payload bytes only)
Checksum input: [0x02, 0x55, 0x09, 0x00, 0x04]
Checksum: (0x02 + 0x55 + 0x09 + 0x00 + 0x04 - 1) & 0xFF = (0x64 - 1) & 0xFF = 0x63
Full frame: 5A 02 55 09 00 04 63
```

### Example: Vibration Test (mode=3 "Wink", intensity=8)

```
Payload: "0308"
Command: "5509"
Length: len("0308") / 2 = 2 → "02"
Checksum input: [0x02, 0x55, 0x09, 0x03, 0x08]
Checksum: (0x02 + 0x55 + 0x09 + 0x03 + 0x08 - 1) & 0xFF = (0x6B - 1) & 0xFF = 0x6A
Full frame: 5A 02 55 09 03 08 6A
```

## Verify String Command Details

**writeVerifyString** (Command `550C`):
```
Payload: "636865636B" (ASCII "check")
Length: len("636865636B") / 2 = 5 → "05"  (payload bytes only)
Checksum input: [0x05, 0x55, 0x0C, 0x63, 0x68, 0x65, 0x63, 0x6B]
Checksum: (0x05 + 0x55 + 0x0C + 0x63 + 0x68 + 0x65 + 0x63 + 0x6B - 1) & 0xFF
         = (0x264 - 1) & 0xFF = 0x63
Full frame: 5A 05 55 0C 63 68 65 63 6B 63
```

**writeSendVerifyString** (Command `550E`):
- Payload is the random code received from the device in the `550E` notification response
- The code is stored locally for subsequent connections

## Battery Request

**Command ID**: `5502`, **Payload**: `00`
```
Length: len("00") / 2 = 1 → "01"  (payload bytes only)
Checksum input: [0x01, 0x55, 0x02, 0x00]
Checksum: (0x01 + 0x55 + 0x02 + 0x00 - 1) & 0xFF = (0x58 - 1) & 0xFF = 0x57
Full frame: 5A 01 55 02 00 57
```

Response `5502` contains battery/standby time data (decoded via `hexToInt`).

## Set Time Command

**Command ID**: `5501`, **Payload**: time bytes

The time payload is constructed from the current date/time using `intToHex()` for each component.

## Vibration Modes (9 modes, index 0-8)

| Index | Mode Name      | Description                                    |
|-------|----------------|------------------------------------------------|
| 0     | Steady Vibe    | Constant vibration                             |
| 1     | Ramp Climb     | Gradually increasing intensity                 |
| 2     | Rumble         | Deep rumbling pattern                          |
| 3     | Wink           | Brief pulse pattern                            |
| 4     | Jolt           | Sharp sudden vibration                         |
| 5     | Pulse Beat     | Rhythmic pulsing pattern                       |
| 6     | Rapid Pulse    | Quick succession of pulses                     |
| 7     | Ascension Vibe | Rising vibration pattern                       |
| 8     | Random         | Randomly varies pattern each alarm activation  |

## Intensity Levels (9 levels, index 0-8)

The device supports 9 intensity levels from lightest (0) to strongest (8).

## Alarm Data Model

Each alarm stored in the local SQLite database (`wakeband.db`) has:

```sql
CREATE TABLE IF NOT EXISTS alarm_{device_mac} (
    id INTEGER PRIMARY KEY,
    hour INTEGER DEFAULT 0,
    minute INTEGER DEFAULT 0,
    status INTEGER DEFAULT 0,        -- 0=disabled, 1=enabled
    repeat_ids TEXT,                  -- comma-separated day IDs
    repeat_str TEXT,                  -- human-readable repeat string
    instruction TEXT,                 -- alarm label/note
    vibration_price TEXT,             -- vibration mode index (0-8)
    vibration_str TEXT,               -- vibration mode name
    intensity_price TEXT,             -- intensity level index (0-8)
    intensity_str TEXT,               -- intensity level name
    is_snapze INTEGER DEFAULT 0,     -- snooze enabled
    is_one INTEGER DEFAULT 0,        -- one-time alarm flag
    hex_str TEXT,                     -- BLE command hex bytes
    time INTEGER DEFAULT 0,          -- alarm time as integer
    time_hex_str TEXT,                -- BLE time command hex bytes
    is_delete INTEGER DEFAULT 0,     -- soft delete flag
    year INTEGER DEFAULT 0,
    month INTEGER DEFAULT 0,
    day INTEGER DEFAULT 0,
    light_status INTEGER DEFAULT 0   -- LED on/off for this alarm
);
```

## Week Day Encoding

Days are encoded as a hex bitmask via `getWeekHex`:
- Bit 0: Monday
- Bit 1: Tuesday
- Bit 2: Wednesday
- Bit 3: Thursday
- Bit 4: Friday
- Bit 5: Saturday
- Bit 6: Sunday
- 0x7F = Every day

## Notes

- All BLE communication goes through `BluetoothManage` singleton
- The protocol works entirely with hex strings internally (each byte = 2 hex chars)
- `hexStrToInts()` converts hex string to byte array before BLE write
- `intsToHex()` converts received byte array to hex string for parsing
- The protocol uses a request-response pattern: write to AC02, receive notification on AC01
- Firmware update uses different header `0xE5` and command IDs `0901`/`0902`
