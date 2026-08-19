"""Guards that every string the integration references exists."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from custom_components.sanremo import binary_sensor, button, number, sensor, switch
from custom_components.sanremo.const import EnergySavingMode, WifiStatus
from custom_components.sanremo.profiles import cube, you

COMPONENT_DIR = Path(__file__).parents[1] / "custom_components" / "sanremo"


def _strings() -> dict[str, Any]:
    """Load strings.json."""
    return json.loads((COMPONENT_DIR / "strings.json").read_text())


def _icons() -> dict[str, Any]:
    """Load icons.json."""
    return json.loads((COMPONENT_DIR / "icons.json").read_text())


PLATFORM_DESCRIPTIONS = {
    "sensor": sensor.SENSORS,
    "binary_sensor": binary_sensor.BINARY_SENSORS,
    "switch": switch.SWITCHES,
    "number": number.NUMBERS,
    "button": button.BUTTONS,
}


def test_every_description_translation_key_exists() -> None:
    """Each platform description's translation_key is defined."""
    strings = _strings()["entity"]
    missing: list[str] = []

    for platform, descriptions in PLATFORM_DESCRIPTIONS.items():
        for description in descriptions:
            key = description.translation_key
            if key and key not in strings.get(platform, {}):
                missing.append(f"{platform}.{key}")

    assert not missing, f"missing entity translations: {missing}"


def test_every_alarm_slug_has_a_name() -> None:
    """Both profiles' alarm bits map onto translated binary sensors."""
    strings = _strings()["entity"]["binary_sensor"]
    slugs = set(cube.ALARM_BITS) | set(you.ALARM_BITS) | set(you.WARNING_BITS)

    missing = [slug for slug in slugs if f"alarm_{slug}" not in strings]
    assert not missing, f"alarm slugs without a translation: {missing}"


def test_scheduler_day_switches_are_translated() -> None:
    """The per-weekday schedule switches are generated, not declared."""
    strings = _strings()["entity"]["switch"]

    for day in switch.WEEKDAY_KEYS:
        assert f"scheduler_{day}" in strings, day


def test_enum_sensor_options_are_translated() -> None:
    """Enum sensors need a state translation for every option they advertise."""
    strings = _strings()["entity"]

    wifi_states = strings["sensor"]["wifi_status"]["state"]
    for status in WifiStatus:
        assert status.value in wifi_states, status

    mode_states = strings["select"]["energy_saving_mode"]["state"]
    for mode in EnergySavingMode:
        assert mode.value in mode_states, mode


def test_translations_match_strings() -> None:
    """translations/en.json is kept in sync with strings.json."""
    english = json.loads((COMPONENT_DIR / "translations" / "en.json").read_text())
    assert english == _strings()


def _leaves(node: dict[str, Any], prefix: str = "") -> set[str]:
    """Return every leaf path in a nested translation dict."""
    found: set[str] = set()
    for key, value in node.items():
        path = f"{prefix}/{key}" if prefix else key
        if isinstance(value, dict):
            found |= _leaves(value, path)
        else:
            found.add(path)
    return found


def test_locale_files_are_subsets_of_english() -> None:
    """Every translated key must exist in English."""
    english = _leaves(_strings())

    for path in sorted((COMPONENT_DIR / "translations").glob("*.json")):
        if path.stem == "en":
            continue
        translated = _leaves(json.loads(path.read_text()))
        assert translated, f"{path.name} is empty"
        orphans = sorted(translated - english)
        assert not orphans, f"{path.name} has keys absent from English: {orphans}"


def test_alarm_names_carry_their_vendor_error_code() -> None:
    """Each E-coded alarm is named with the code the machine displays."""
    for path in sorted((COMPONENT_DIR / "translations").glob("*.json")):
        entity = json.loads(path.read_text()).get("entity", {})
        for slug, payload in entity.get("binary_sensor", {}).items():
            if (code := re.match(r"alarm_(e\d\d)_", slug)) and "name" in payload:
                expected = code.group(1).upper()
                assert expected in payload["name"], (
                    f"{path.name}: {slug} is named {payload['name']!r}, "
                    f"which omits {expected}"
                )


#: Vendor wording not used, and why. The machine's makers are
REJECTED_VENDOR_WORDING = {
    "Programming": "the vendor's 'Programmazione'; scheduling, not software. Use Schedule",
    "EEprom": "acronym capitalisation; use EEPROM",
    "Ip obtained": "acronym capitalisation; use IP obtained",
    "Setpoint ECO temperature": "Italian word order; use ECO setpoint",
    "Filter report": "the vendor's 'Report filtri'; this is an interval in months",
    "Unit of measurement": "a vendor page heading, too vague for a boolean switch",
    "Water filters": "plural heading; the problem sensor covers one filter",
}


def _all_strings(node: Any) -> list[str]:
    """Return every string value in a nested translation dict."""
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [text for value in node.values() for text in _all_strings(value)]
    return []


def test_no_rejected_vendor_wording_survives() -> None:
    """None of the vendor's ill-translated English is shipped."""
    offences: list[str] = []

    for path in sorted((COMPONENT_DIR / "translations").glob("*.json")):
        if path.stem != "en":
            # Only English was curated; the vendor's Italian is native and stays.
            continue
        for text in _all_strings(json.loads(path.read_text())):
            for phrase, reason in REJECTED_VENDOR_WORDING.items():
                if phrase in text:
                    offences.append(
                        f"{path.name}: {text!r} contains {phrase!r} -- {reason}"
                    )

    assert not offences, "rejected vendor wording is back:\n  " + "\n  ".join(offences)


def test_icon_keys_reference_real_entities() -> None:
    """Every icon override points at a description that exists."""
    icons = _icons()["entity"]
    known: dict[str, set[str]] = {
        platform: {d.translation_key for d in descriptions if d.translation_key}
        for platform, descriptions in PLATFORM_DESCRIPTIONS.items()
    }
    # Generated entities are not in the static description tuples.
    known["switch"] |= {f"scheduler_{day}" for day in switch.WEEKDAY_KEYS}
    known["binary_sensor"] |= {
        f"alarm_{slug}"
        for slug in set(cube.ALARM_BITS) | set(you.ALARM_BITS) | set(you.WARNING_BITS)
    } | {"alarm"}
    known["sensor"] |= {"raw_register"}
    known["select"] = {"energy_saving_mode"}
    known["calendar"] = {"schedule"}
    known["text"] = {"machine_name"}

    stale: list[str] = []
    for platform, entries in icons.items():
        for key in entries:
            if key not in known.get(platform, set()):
                stale.append(f"{platform}.{key}")

    assert not stale, f"icon keys with no matching entity: {stale}"
