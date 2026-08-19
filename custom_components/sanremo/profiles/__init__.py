"""Machine profiles and the runtime detection that picks one."""

from __future__ import annotations

import logging

from ..api import SanremoClient, SanremoError
from .base import MachineProfile
from .cube import CubeProfile
from .generic import GenericProfile
from .you import YouProfile

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "CubeProfile",
    "GenericProfile",
    "MachineProfile",
    "YouProfile",
    "async_detect_profile",
]

#: Model page name -> profile, for the cases where the landing page is decisive.
_PAGE_PROFILES: dict[str, type[MachineProfile]] = {
    "cube": CubeProfile,
    "you": YouProfile,
}


async def async_detect_profile(client: SanremoClient) -> MachineProfile:
    """Work out which profile fits the machine at ``client``."""
    page = await client.probe_model_page()
    if page and (profile_class := _PAGE_PROFILES.get(page)):
        _LOGGER.debug("Detected %s from landing page %s.html", page, page)
        return profile_class(client)

    try:
        read_only = await client.get_read_only()
    except SanremoError:
        read_only = {}

    if isinstance(read_only.get("registers"), list) and read_only["registers"]:
        _LOGGER.debug(
            "Machine at %s uses the register protocol; assuming the Cube map",
            client.host,
        )
        return CubeProfile(client)

    try:
        system = await client.get_system_params()
    except SanremoError:
        system = {}

    if isinstance(system.get("status"), list) and isinstance(
        system.get("settings"), list
    ):
        _LOGGER.debug("Machine at %s uses the array protocol (YOU)", client.host)
        return YouProfile(client)

    _LOGGER.debug("Machine at %s matched no known profile", client.host)
    return GenericProfile(client)
