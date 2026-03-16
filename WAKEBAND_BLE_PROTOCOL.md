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

## BLE Service Discovery

The app does NOT hardcode BLE service/characteristic UUIDs. It uses **dynamic service discovery**:

1. Scans for devices advertising the name `WakeBand`
2. Connects to the device
3. Calls `discoverServices()` to enumerate all GATT services
4. Selects the appropriate service and characteristics for read/write/notify

The only hardcoded UUID is the standard **CCCD (Client Characteristic Configuration Descriptor)**:
`00002902-0000-1000-8000-00805f9b34fb`

**To discover the actual UUIDs, use the `wakeband_discover.py` script** which will connect
to your device and enumerate all services and characteristics.

## BLE Write Commands (App → Device)

| Command Method              | Purpose                          |
|-----------------------------|----------------------------------|
| `writeSetTime`              | Sync current time to device      |
| `writeAddAlarmClock`        | Add a new alarm                  |
| `writeEditAlarmClock`       | Edit an existing alarm           |
| `writeDeleteMoreAlarmClock` | Delete specific alarm(s)         |
| `writeDeleteAllAlarmClock`  | Delete all alarms                |
| `writeGetAlarmClock`        | Request current alarms from device |
| `writeVibrationTest`        | **Trigger a vibration test**     |
| `writeBattery`              | Request battery level            |
| `writeLightStatus`          | Control LED light                |
| `writeResetDevice`          | Factory reset                    |
| `writeVerifyString`         | Authentication/pairing verify    |
| `writeSendVerifyString`     | Send verification string         |
| `writeUpdateMD5Data`        | Firmware update (MD5 check)      |
| `writeUpdateData`           | Firmware update (data transfer)  |

## BLE Read/Notification Responses (Device → App)

| Response Method                  | Purpose                          |
|----------------------------------|----------------------------------|
| `readSetTimeResult`              | Time sync confirmation           |
| `readAddAlarmClockResult`        | Add alarm confirmation           |
| `readEditAlarmClockResult`       | Edit alarm confirmation          |
| `readDeleteMoreAlarmClockResult` | Delete alarm(s) confirmation     |
| `readDeleteAllAlarmClockResult`  | Delete all confirmation          |
| `readGetAlarmClockData`          | Alarm data response              |
| `readGetAlarmClockFinishResult`  | All alarms retrieved signal      |
| `readVibrationTestResult`        | Vibration test confirmation      |
| `readOneTimeResult`              | One-time alarm response          |
| `readOpenFirstAlarmClock`        | First alarm activation notice    |
| `readVerifyStringResult`         | Verification response            |
| `readUpdateDataSendResult`       | Firmware update progress         |
| `readUpdateResult`               | Firmware update result           |

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
    hex_str TEXT,                     -- **BLE command hex bytes**
    time INTEGER DEFAULT 0,          -- alarm time as integer
    time_hex_str TEXT,                -- **BLE time command hex bytes**
    is_delete INTEGER DEFAULT 0,     -- soft delete flag
    year INTEGER DEFAULT 0,
    month INTEGER DEFAULT 0,
    day INTEGER DEFAULT 0,
    light_status INTEGER DEFAULT 0   -- LED on/off for this alarm
);
```

## Device Info Table

```sql
CREATE TABLE IF NOT EXISTS device_1000 (
    device_mac TEXT PRIMARY KEY,
    device_name TEXT,
    colour INTEGER DEFAULT 1,
    light_status INTEGER DEFAULT 0,
    is_default INTEGER DEFAULT 0,
    timestamp INTEGER DEFAULT 0,
    is_require_bind INTEGER DEFAULT 0,
    is_require_default INTEGER DEFAULT 0,
    is_require_edit INTEGER DEFAULT 0,
    is_require_unbind INTEGER DEFAULT 0
);
```

## Key Protocol Functions

- `dealAlarmData` - Constructs alarm BLE command bytes from alarm parameters
- `getWeekHex` - Converts selected weekdays to a hex bitmask
- `dealVibrationAndIntensityAndWeek` - Combines vibration mode, intensity, and repeat days
- `loadBleAlarmData` - Loads alarm data from device via BLE
- `getDeleteAlarmData` - Constructs delete alarm command

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

- The app uses the `BleWriteType` and `BleReadType` enums to categorize command types
- All BLE communication goes through a `BluetoothManage` singleton class
- Command bytes are stored as hex strings in the `hex_str` DB field
- The protocol uses a request-response pattern: write a command, receive notification
- The firmware update binary is served from GitHub: `db_update_data.bin` (encrypted)
