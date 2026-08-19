# The WiNET protocol

Sanremo's Wi-Fi machines embed a WiNET module. It serves its own web app over HTTP
and drives it through one form-encoded RPC endpoint. There is no cloud and no
documented API. The "Sanremo Connector" app is only a WebView launcher and contains
no protocol code, so the machine's own page is the specification.

Everything here was verified against a Cube R running WiNET 0.24.000 with board
firmware 1.26, unless marked otherwise. Sources are credited in the README.

## 1. Transport

```
POST /ajax/post          key=151
```

* HTTP only, port 80, no auth.
* Form-encoded request, JSON reply. The `Content-Type` is often wrong and the body
  is gzipped without being asked for, so decode defensively.
* One request at a time. Concurrent requests get connection resets. Serialise and
  retry.
* `GET /` returns `302 /index.html`, which redirects to a model page such as
  `cube.html`. That is the cheapest model probe. Unknown paths drop the connection.
* `result` is not a success signal. Keys 250 and 252 return `{"result": false}`
  while applying the change. Confirm writes by reading back.

## 2. Commands

| Key | Payload | Purpose |
| --- | --- | --- |
| `100` / `101` | `pin` | Technician login and logout. Not implemented |
| `102` | – | Scan Wi-Fi networks |
| `103` / `104` | network / IP config | Not implemented |
| `105` | – | Device and network info |
| `150` | – | System parameters (meaning differs per family, §3) |
| `151` | – | Read-only machine state |
| `152` | – | Read/write machine settings |
| `200` | `id`, `value` | Set a parameter. The main control surface |
| `202` / `203` | – | Reboot the module / check for firmware |
| `249` | `hh`,`mm`,`DD`,`MM`,`YY` | Set the clock (2-digit year works; the app sends 4) |
| `250` | `day`, `enabled` | Enable one scheduler day (day-of-week convention) |
| `251` | `name` | Rename |
| `252` | `enabled` | Scheduler master switch |
| `253` | see §4.4 | Save one day's three slots |
| `255` | `waterUnit`, `pressUnit` | Persist app-side display units |

### `key=105`

```json
{"key":105,"status":5,"useDhcp":1,"currentIp":"…","currentMask":"…","currentGw":"…",
 "mac":"AABBCCDDEEFF","client":3,"ssid":"…","rssi":-88,"fwVer":"0.24.000","name":"cube1"}
```

`fwVer` is the module firmware, not the board's. `mac` is the only stable
identifier the device offers. `status`: `0` operating, `1` connecting, `2` wrong
password, `3` AP not found, `4` error, `5` connected, `255` not in station mode.
`client`: `0` operating, `1` pause, `2` connected, `3` not connected.

## 3. Two protocol families

Detect by probing, not by model name:

```
key=151 has "registers"                 → register family (Cube)
else key=150 has "status" + "settings"  → array family (YOU)
else                                    → unknown, generic profile
```

Register family: `151` and `152` return `registers` as `[index, value]` pairs, an
index-addressed 16-bit register file. `150` carries only network and identity.

Array family: `150` carries everything as parallel arrays, and a WebSocket on port
81 pushes brew telemetry at about 5 Hz. `151` degenerates to an RSSI and OTA poll.
The Cube has no port 81.

## 4. Cube

### 4.1 `key=150`

```json
{"key":150,"tz":0,"ota":false,"signal":2,"name":"cube1","mUnitWater":0,
 "mUnitPress":1,"ver":1.26,"status":5,"ssid":"…","rssi":-88}
```

`ver` is the board firmware. Above 1.14 it unlocks the ECO setpoint, its delay and
the SteamBooster. `mUnitPress` is `0` for PSI and `1` for bar, and affects display
only, since registers are always metric. `signal` is `rssi` bucketed 0 to 4 using
the same thresholds the app recomputes itself, so it carries nothing new.

### 4.2 `key=151`, read-only

`config == 2` marks the CUBE R, whose boiler setpoint is adjustable.
`ThesholdWarningChangeFilter` (vendor's spelling) is the filter interval in days;
`0` disables monitoring.

`time` is `[dow, year, month, day, hour, minute]`. A `dow` of 0 does not mean the
clock is unset. It means no weekday is established, which includes the state right
after a `key=249` write, since that command carries no weekday. It was observed
going from 1 to 0 across a write. Registers 2 to 6 are a second, separate clock:
one reply reported minute 51 there and 52 in `time`.

| Reg | Meaning | Scale |
| --- | --- | --- |
| 0 | Boiler temperature | °C |
| 1 | Probably RTC seconds (2 samples) | |
| 2–6 | RTC minute, hour, day, month, year | |
| 9 | Last extraction time | ÷10 → s |
| 10 | Days until filter change | days |
| 12 | Machine status bitfield | below |
| 14 | Alarm bitfield | below |
| 21 / 22 | Coffees today / this week | |
| 23+24 / 25+26 / 29+30 | Coffees month / year / total | `lo`+`hi` |
| 31+32 / 33+34 / 35+36 | Water dispensed / to boiler / total | `lo`+`hi`, ml |
| 38 | Energy-saving countdown | s |

32-bit counters combine as `(hi << 16) | lo`. The vendor app writes `hi | lo`,
which truncates above 65535. The identity `dispensed + to_boiler == total` holds
only with the shift.

Registers 7, 8, 11, 13, 15–20, 27, 28, 37 are read by no part of the vendor app.

#### `151[12]` machine status

| Bit | Meaning |
| --- | --- |
| 0 / 1 | Tank level OK / boiler level OK |
| 2 | Tank pre-alarm (suppressed while the low-water alarm is set) |
| 3 | Water source: `0` tank, `1` mains |
| 4 | Energy saving active |
| 5 | Ready to brew |
| 8 / 9 | SteamBooster heating / at setpoint |

Bits 6 and 7 are unread by the vendor app, and bit 6 is set on the test machine.
The app's own `Ready` assignment uses `==` where it means `=`, so its flag never
updates.

#### `151[14]` alarms

Bits 0 to 7 each carry a code. Two of the vendor's variable names mislead, so
trust the displayed message:

| Bit | Code | Displayed | Vendor name |
| --- | --- | --- | --- |
| 0 | E03 | Boiler probe open | `NtcBoilerBroken` |
| 1 | E02 | Boiler heating timeout | `TemperatureTimeout` |
| 2 | E01 | Boiler filling timeout | `LoadBoilerTimeout` |
| 3 | E04 | Boiler probe short-circuit | `NtcBoilerShortCircuit` |
| 4 | E06 | EEPROM corrupted | `EepromError` |
| 5 | E05 | Potentiometer disconnected | `WirewoundResistor` |
| 6 | E07 | Coffee dispensing timeout | `ErogationTimeout` |
| 7 | E08 | Water filter replacement required | `NeedChangeFilters` |
| 8 | – | *(no message exists)* | `H2oLevelLow` |

Bit 5 is easy to get wrong. "Wirewound resistor" is a literal reading of the
internal name, but the machine tells the user the potentiometer is disconnected,
which is a different repair. The vendor's "any alarm" test is `f & 255`, which
excludes bit 8.

### 4.3 `key=152`, read/write

| Reg | Meaning | Scale |
| --- | --- | --- |
| 0 | Boiler setpoint | ÷10 → °C |
| 8 | Filter interval | months |
| 12–16 | Filter expiry marker; `id=23` sets bit 6 of `[12]` | unconfirmed |
| 17 | Setup bitfield | below |
| 18–59 | Scheduler, 21 rows × 2 | §4.4 |
| 60 | Per-day enable bitmask | bit 0 = Sunday … 6 = Saturday |
| 62 | A live countdown, unread by the vendor | s |
| 67 | Energy-saving delay | s |
| 68 | ECO setpoint | ÷10 → °C |

67 and 68 exist only when board `ver > 1.14`; guard on array length.

#### `152[17]` setup

| Bit | Meaning |
| --- | --- |
| 0 | Standby 30 min after last coffee |
| 1 | Filter warning counted in litres |
| 2 | Display in Fahrenheit |
| 3 | Scheduler enabled |
| 4 | Wi-Fi circuit enabled |
| 5 | Pre-infusion enabled |
| 6 | Energy-saving mode: `1` ECO, `0` standby |
| 7 | SteamBooster enabled |

### 4.4 Scheduler

`152[18]` to `152[59]` holds 21 rows, being 7 days of 3 slots. Rows 0 to 2 are
Monday and rows 18 to 20 are Sunday. Each row is two registers:

```
reg[u]     & 7          day-of-week value, or 7 = slot unused
reg[u + 1] >> 8 & 255   on-time,  in quarter-hours
reg[u + 1]      & 255   off-time, in quarter-hours
```

`hour = q // 4`, `minute = (q % 4) * 15`. Example: `[1, 7206]` → Monday,
`7206 >> 8 = 28` → 07:00, `7206 & 255 = 38` → 09:30.

Two day conventions are live at once. Row order starts at Monday, while stored
day-of-week values run Sunday = 0 to Saturday = 6. `key=250` uses the latter.

`key=253` mixes both in one request:

| Field | Meaning |
| --- | --- |
| `day` | Row index, 0 = Monday |
| `en1`, `en2`, `en3` | Day-of-week value per slot (Sunday = 0), or `7` to disable |
| `on1H`, `on1M`, `off1H`, `off1M` | Slot 1 times, and likewise for 2 and 3 |
| `copyMon` to `copySun` | `1` to copy this day onto that day |

Sending `day=1` with Monday's `en` values rewrites Tuesday.

An off-time of 24:00 is legal and is stored as 96 quarter-hours. The hour spinner
allows 24 and forces minutes to 0. `datetime.time` cannot hold that value, so it
needs an explicit next-day marker. Clamping the hour to 23 moves the window an hour
earlier.

An off-time earlier than the on-time is also stored verbatim, but what the
scheduler does with such a slot is unverified: it may wrap or it may never fire.
Since 24:00 is the vendor's way of saying "until midnight", wrapping may not be
intended.

### 4.5 `key=200` parameters

All verified by reading back the affected register.

| ID | Argument | Effect |
| --- | --- | --- |
| 1 | whole °C | Boiler setpoint. Writes whole degrees, reads back tenths |
| 8 / 9 | *(none)* | Energy-saving mode → ECO / standby (`[17]` bit 6) |
| 10 | 0/1 | Standby 30 min after last coffee (bit 0) |
| 11 / 12 | 1 | Power on / standby |
| 20 | 0/1 | Clock display; `1` = 24-hour, read back as `use24H` on `key=151` |
| 21 | 0/1 | Display temperature unit (bit 2) |
| 22 | months | Filter interval → `[8]`; `0` disables |
| 23 | 0 | Reset filter expiry. No inverse command exists |
| 24 | 0/1 | SteamBooster (bit 7) |
| 25 | seconds | Energy-saving delay → `[67]` |
| 26 | tenths °C | ECO setpoint → `[68]` |

For ID 1, send 121 and read back 1210. IDs 8 and 9 take no value, and the app sends
an empty field. That 11 is on and 12 is standby is confirmed three ways. The
vendor's own JavaScript binds them to a switch named the opposite way, which looks
like a naming slip.

### 4.6 Write bounds

From `min`/`max` attributes in `cube.html`, enforced by its spinner handlers.
Prefer these over the homebridge plugin's boiler maximum of 130, which disagrees
with the vendor and with its own brew-temperature table.

| Control | Range | Step |
| --- | --- | --- |
| Boiler setpoint | 115 – 126 °C | 1 |
| ECO setpoint | 80 – 100 °C | 1 |
| Energy-saving delay | 1 – 90 min | 1 |
| Filter interval | 0 – 12 months | 1 |

### 4.7 Derived values

Both are hardcoded integer-keyed tables over the setpoint rather than formulas, and
both clamp outside their range.

Estimated temperature at the group: `115→89.5 116→89.8 117→90.2 118→90.5 119→90.7
120→91.5 121→92.4 122→92.7 123→94.0 124→95.0 125→95.7 126→96.3`. A setpoint of 0
yields nothing.

Equivalent boiler pressure in bar: `115→0.75` through `130→1.75`, with no default,
so an out-of-table setpoint renders as 0. This integration does not expose it,
because it restates the setpoint.

### 4.8 Polling

The vendor app enqueues every 400 ms, cycling `151` most often, `150` every third
and `152` every fifth. This integration polls every 10 s and refreshes `150` every
sixth cycle, because setpoints only change when something writes them.

## 5. YOU, unverified

Ported, not re-tested; no YOU was available.

`key=150` returns `status[]` and `settings[]`. Status indices: `10` phase, `12`
machine status, `13` alarms, `14` warnings, `15` group temp (÷10), `16` heater temp
(÷10), `17` service heater temp, `18` service heater pressure (÷100), `19` pump
pressure (÷10), `20` counter volume, `22` dose time (÷10), `23` level sensor, `29`
deep sleep, `30` real-time flow, `31` paddle pressure. `settings[0…3]` are the
steam-heater, group, filter-holder and steam-pressure setpoints. Machine status:
`0` off, `1` on, `2` ECO, `3` deep sleep.

Unlike the Cube's ID 1, all YOU setpoint writes take tenths.

Firmware 0.12+ adds `GET /api/action/on`, `/api/action/standby`,
`/api/action/{p1,p2,p3,man}/{start,stop}`, `/api/doses`, `/api/counters`, and a
WebSocket on port 81 carrying live brew telemetry. None exist on the Cube.

## 6. Known unknowns

* `151` registers 7, 8, 11, 13, 15–20, 27, 28, 37, and `151[12]` bits 6–7.
* `152[12…16]`. One reading of `19, 17, 25, 8, 38` is the timestamp
  2025-08-19 17:38, which would extend the block by a register.
* `151[38]` and `152[62]` both count down once per second but disagree. Values of
  527 and 3591 were observed together with the delay set to 3600 s. Only `151[38]`
  is read by the vendor app.
* Whether the board accepts 127–130 °C. The UI caps at 126; its pressure table
  goes to 130.
* Whether `152[68]` honours a half degree. The vendor only sends whole degrees.
* Whether a window with `off < on` wraps past midnight.
* `config`, whose vendor variable name is `haveDisplay`. The setpoint control may
  be a consequence of the machine having a display rather than the meaning.
* Whether other register-family models share the Cube's register meanings. The
  module is generic, and the meanings come from each machine's control board.
