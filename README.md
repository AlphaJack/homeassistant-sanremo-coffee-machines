# Sanremo Coffee Machines for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Validate](https://github.com/AlphaJack/homeassistant-sanremo-coffee-machines/actions/workflows/validate.yml/badge.svg)](https://github.com/AlphaJack/homeassistant-sanremo-coffee-machines/actions/workflows/validate.yml)

Local control of Wi-Fi capable Sanremo espresso machines. It talks to the WiNET
module inside the machine over HTTP on your LAN, with no cloud, no account and no
Python dependencies.

## Supported machines

| Machine | Support | Tested on hardware |
| --- | --- | --- |
| Cube, Cube R | Full | Yes |
| YOU | Full | No |
| Other WiNET machines | Partial | No |

Cube support was verified against a Cube R running WiNET 0.24.000 with board
firmware 1.26. YOU support is a port of another project's protocol work and has not
been re-tested, so please report issues.

Sanremo's app is only a launcher, so there is no vendor model list to consult.
Machines are identified by probing, and the entity set follows what each one
reports.

Other models are not guessed at. The WiNET module is generic, but register meanings
come from each machine's control board, so assuming a Zoe lays out its registers
like a Cube would report wrong temperatures with full confidence. An unrecognised
machine gets a generic profile: identity, network, clock, and every raw register as
a disabled diagnostic sensor. See [Adding a model](#adding-a-model).

## Installation

Requires Home Assistant 2026.8.2 or newer, which is the version this is tested
against.

In HACS, open the three-dot menu, choose Custom repositories, and add this
repository as an Integration. Install it and restart.

[![Add to HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=AlphaJack&repository=homeassistant-sanremo-coffee-machines&category=integration)

To install by hand, copy `custom_components/sanremo` into
`config/custom_components/` and restart.

## Configuration

Go to Settings, Devices & services, Add integration, Sanremo. It needs the
machine's IP address, which the Sanremo Connector app shows in its device list. A
pasted `http://…` URL also works.

There is no password, because the module's local endpoint is unauthenticated. Give
the machine a static DHCP lease so its address does not move.

Configure sets the polling interval, which defaults to 10 seconds. The module is a
small embedded server that answers one request at a time, so a shorter interval
gains little.

## Entities

Entities are created only where the machine supports them, so the list depends on
the model, its board firmware and its configuration. A Cube R on board 1.26 gets
all of them.

### Controls

| Entity | Domain | Needs |
| --- | --- | --- |
| Power | `switch` | any |
| Boiler | `water_heater` | any |
| Boiler setpoint | `number` | Cube R |
| ECO setpoint | `number` | board > 1.14 |
| Energy saving delay | `number` | board > 1.14 |
| Energy saving | `select` | board > 1.14 |
| SteamBooster | `switch` | board > 1.14 |
| Standby after last coffee | `switch` | any |
| Schedule | `switch` | any |
| Schedule *\<weekday\>* | `switch` | weekday scheduler |
| Water filter interval | `number` | any |
| Machine name | `text` | any |
| Reset water filter | `button` | any |
| Sync clock | `button` | any |
| Check for firmware | `button` | any |
| Reboot Wi-Fi module | `button` | any |
| 12-hour clock | `switch` | any |
| Display in Fahrenheit | `switch` | any |

The boiler entity carries the current and target temperature and the performance,
eco and off modes. Each weekday switch lists that day's slots in its attributes. A
filter interval of 0 turns filter monitoring off. The machine name is the one the
vendor app shows, which is separate from the Home Assistant device name. Resetting
the water filter cannot be undone. Rebooting affects the Wi-Fi module and not the
machine, so that button is disabled by default. The clock and Fahrenheit switches
change the machine's own display.

### Readings

Temperatures and pressures are stored in metric units and converted by Home
Assistant, so switching unit system works without further configuration.

Boiler, group and steam temperature. Estimated brew temperature. Pump and steam
pressure. Last shot time, flow rate and shot volume. Coffee counters for today, the
week, the month, the year and the total. Water dispensed, water into the boiler and
the total. Water filter days remaining and remaining life. Ready to brew. Tank and
boiler water warnings. SteamBooster state. Pre-infusion. One aggregate alarm that
lists the active ones in its attributes, plus a disabled sensor per alarm bit.
Wi-Fi signal and status. Machine clock and clock drift. Energy-saving countdown.
Firmware update flag.

### Schedule

The on-board scheduler appears as a read-only `calendar`. Editing goes through an
action, because the machine stores a fixed three slots per weekday and no standard
entity expresses that.

```yaml
action: sanremo.set_schedule_day
data:
  device_id: abcdef0123456789
  day: wednesday
  slots:
    - on: "06:30"
      "off": "09:00"
    - on: "17:00"
      "off": "22:30"
  copy_to: [thursday, friday]
```

Times snap to the machine's 15-minute grid. Slots you leave out are cleared.

## Examples

```yaml
automation:
  - alias: Morning espresso
    triggers:
      - trigger: time
        at: "06:45:00"
    conditions:
      - condition: time
        weekday: [mon, tue, wed, thu, fri]
      - condition: state
        entity_id: person.me
        state: home
    actions:
      - action: water_heater.set_operation_mode
        target: { entity_id: water_heater.cube1 }
        data: { operation_mode: performance }

  - alias: Espresso ready
    triggers:
      - trigger: state
        entity_id: binary_sensor.cube1_ready_to_brew
        to: "on"
        for: "00:00:30"
    actions:
      - action: notify.mobile_app
        data:
          message: "{{ states('sensor.cube1_boiler_temperature') }}°C, ready."

  - alias: Espresso needs attention
    triggers:
      - trigger: state
        entity_id: binary_sensor.cube1_alarm
        to: "on"
    actions:
      - action: notify.mobile_app
        data:
          message: >-
            {{ state_attr('binary_sensor.cube1_alarm', 'active_alarms') | join(', ') }}
```

## Languages

English and Italian, taken from the catalogue embedded in the machine's own web
app. Alarms therefore read `E01 - Boiler filling timeout` exactly as the machine
displays them.

The Italian is the vendor's own and is left alone. The English is edited, because in
places theirs is a literal rendering of the Italian: `Programmazione` had become
"Programming", which in English means writing software. The catalogue also holds
German, Spanish, French, Portuguese, Arabic, Chinese, Japanese, Korean and Russian.
Adding one is a small change, though its English would want the same review.

## How data is updated

The integration polls over HTTP every 10 seconds. Requests are serialised and
retried, because the module resets connections when asked two things at once.

Writes are not applied optimistically. Several commands reply `{"result": false}`
while applying the change, so the machine is read back after every write.

The YOU also pushes brew telemetry over a WebSocket. The Cube has no such port.

## Known limitations

- Only the Cube family is tested on hardware. YOU support is a port, and other
  models fall back to the generic profile.
- No discovery. WiNET devices do not advertise over mDNS, and the vendor app finds
  them by SSID prefix. Add machines by IP.
- Resetting the water filter cannot be undone. The protocol offers no way to write
  an expiry date back.
- No authentication, by the machine's design. Anything on your network can control
  it.
- Some registers are unmapped. The vendor's own app does not read them either.
- Whether a schedule window can wrap past midnight is unverified. An off-time of
  24:00 works.
- The technician area and Wi-Fi reconfiguration are not implemented. Use
  `http://<machine-ip>/` for those.

## Troubleshooting

If the machine cannot be reached, check that `http://<machine-ip>/` loads in a
browser on the same network. The module is HTTP only, on port 80.

If entities go unavailable intermittently, the cause is usually weak Wi-Fi. Check
the Wi-Fi signal diagnostic sensor. The test machine works at -87 dBm, but there is
little headroom below that. A longer polling interval also helps.

If a value looks wrong, open an issue with a diagnostics download, from the
three-dot menu on the device page. It carries the raw register dumps with network
details redacted.

## Adding a model

### Which machines qualify

Any Sanremo with a WiNET module, meaning `http://<machine-ip>/` loads a web page.
That is the only requirement. The integration already sets such a machine up under
the generic profile, so identity, network, clock and raw registers work before
anyone writes code. What a new profile adds is meaning: which register holds the
boiler temperature, which bit means ready.

The Cube and the YOU are done. Candidates from Sanremo's current range are D8,
D8 ONE, ZOE, X-ONE, Café Racer, Opera, F18 and F18SB, plus older machines with a
retrofitted module. Whether a given one has WiNET at all is answered by loading
that URL.

### What to send if you do not want to write code

Open an issue with:

1. A diagnostics download, from the three-dot menu on the device page. It carries
   keys 105, 150, 151 and 152 verbatim.
2. The file the machine serves for itself. Fetch `http://<machine-ip>/index.html`
   and read the `GoToUrl('…')` line in it, which names a page such as `cube.html`.
   Attach that page and the `.js` file beside it, for example:

   ```bash
   curl -O http://<machine-ip>/index.html
   curl -O http://<machine-ip>/zoe.html
   curl -O http://<machine-ip>/zoe.js
   ```

3. What the machine's page displays at the same moment as the diagnostics: boiler
   temperature, counters, status.

The `.js` file is the important one. It is the vendor's own web app, so it contains
the register indices, the scale factors, the bitfield meanings and the translated
strings for that model. Everything in `docs/PROTOCOL.md` was read out of the Cube's
copy. Pairing it against a diagnostics dump is enough to write a profile.

### Writing the profile

Four files, plus tests:

| File | Change |
| --- | --- |
| `profiles/<model>.py` | New. Subclass `MachineProfile` |
| `profiles/__init__.py` | Add to `_PAGE_PROFILES` and to `async_detect_profile` |
| `const.py` | Write bounds and any new `Capability` |
| `strings.json`, `translations/` | Names for entities the model adds |

A profile implements `async_setup`, which returns a `DeviceInfo` and populates
`self._capabilities`, and `async_poll`, which returns a `MachineState`. It then
overrides only the commands its machine supports. Nothing else needs touching:
platforms build their entities from the capability set, so declaring
`Capability.STEAM_BOOSTER` is what makes the switch appear.

Copy `profiles/cube.py` as the starting point if the model is in the register
family, or `profiles/you.py` if `key=150` returns `status` and `settings` arrays.

For tests, drop the four captured replies into `tests/fixtures/<model>_1xx.json` and
follow `tests/test_cube_profile.py`. Assert against values the machine's own page
showed at capture time, so the test proves the decoding rather than restating it.

Do not copy a register mapping from another model. The WiNET module is generic, but
the meanings come from each machine's control board, and a wrong guess reports
plausible but wrong numbers.

## Development

```bash
pip install -r requirements-test.txt
pytest tests --cov=custom_components.sanremo
ruff check custom_components tests && ruff format --check custom_components tests
```

The Cube tests run against captures from a real machine. The register map was
reverse engineered, so a synthetic fixture would only confirm its author's
assumptions.

[`docs/PROTOCOL.md`](docs/PROTOCOL.md) is the protocol reference: the command set,
both register maps, the scheduler's two conflicting day conventions, and what is
verified against what is inferred. It records, among other things, that
`key=200 id=1` takes whole degrees but reads back tenths.

## Credits

- [`ha-sanremo-you`](https://github.com/chrisfuss/ha-sanremo-you) for the YOU
  protocol, its REST endpoints and its WebSocket telemetry.
- [`homebridge-sanremo-coffee-machines`](https://github.com/nsinenian/homebridge-sanremo-coffee-machines)
  for independent confirmation of the Cube's power and filter commands.
- The WiNET module and its web app are by Net Software Srl, Italian patent
  n. 102017000107766. This project is unaffiliated with them and with Sanremo.

MIT licensed. Upstream copyright notices are retained in [NOTICE](NOTICE).
