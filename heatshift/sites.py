"""The DigitalHeat fleet: reference house + other eligible sites jobs can migrate to.

House -> Basel -> Nordwestschweiz -> Schweiz (matches the DigitalHeat deck, slide 5).
Each site scales the reference house's heat-loss coefficient (UA) and hot-water
demand. Alpine sites keep heating demand in summer nights, hotels and indoor pools
need hot water year-round: that is where summer compute goes.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Site:
    key: str
    name: str
    region: str
    lat: float
    lon: float
    kind: str            # sfh | mfh | hotel | pool
    ua_factor: float     # x reference house heat-loss coefficient
    hw_kwh_day: float    # hot water / process heat per day
    tank_kwh: float
    servers: int
    bfs: str = ""            # Swiss municipality number (BFS) -> ElCom tariff lookup
    absences: tuple = ()     # planned absences (holidays): ((start "MM-DD", end "MM-DD"), ...)


REFERENCE = "basel_ref"
# Holidays: 2 weeks ski holidays (Feb) + 3 weeks summer (Jul). The owner enters them in the DigitalHeat
# app, so they are known to the planner. Used by the Riehen house and the "reference + holidays" scenario.
HOLIDAYS = (("02-08", "02-22"), ("07-05", "07-26"))

SITES: list[Site] = [
    Site("basel_ref",  "Reference house Basel",       "Basel",           47.5584, 7.5733, "sfh",   1.0,  12.0,  35.0, 0, bfs="2701"),  # servers set by sweep; exactly the case spec
    Site("basel_mfh",  "Apartment block Basel (8 flats)", "Basel",       47.5480, 7.5900, "mfh",   4.0,  70.0, 150.0, 30, bfs="2701"),
    Site("riehen_sfh", "House Riehen",                "Basel",           47.5850, 7.6480, "sfh",   1.1,  12.0,  35.0, 8, bfs="2703", absences=HOLIDAYS),
    Site("liestal_mfh","Apartment block Liestal",     "Nordwestschweiz", 47.4840, 7.7340, "mfh",   3.0,  50.0, 120.0, 22, bfs="2829"),
    Site("aarau_pool", "Indoor pool Aarau",           "Nordwestschweiz", 47.3925, 8.0444, "pool",  2.0, 650.0, 400.0, 40, bfs="4001"),
    Site("zurich_mfh", "Apartment block Zurich",      "Schweiz",         47.3769, 8.5417, "mfh",   5.0,  90.0, 200.0, 40, bfs="261"),
    Site("davos_hotel","Hotel Davos (1,560 m)",       "Schweiz",         46.8027, 9.8360, "hotel", 4.0, 280.0, 300.0, 40, bfs="3851"),
    Site("stmoritz_sfh","House St. Moritz (1,822 m)", "Schweiz",         46.4908, 9.8355, "sfh",   1.0,  12.0,  35.0, 8, bfs="3787"),
    Site("zermatt_hotel","Hotel Zermatt (1,608 m)",   "Schweiz",         46.0207, 7.7491, "hotel", 3.5, 240.0, 300.0, 40, bfs="6300"),
    Site("lugano_mfh", "Apartment block Lugano",      "Schweiz",         46.0037, 8.9511, "mfh",   3.0,  50.0, 120.0, 16, bfs="5192"),
]

SITE_BY_KEY = {s.key: s for s in SITES}
