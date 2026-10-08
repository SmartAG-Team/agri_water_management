from __future__ import annotations


PHASES = [
    ("establishment", 0, 13, 0.06, 0.05, 0.35, 0.06),
    ("tillering", 21, 25, 0.18, 0.16, 1.90, 0.12),
    ("recovery", 26, 30, 0.30, 0.26, 2.80, 0.14),
    ("jointing", 31, 39, 0.56, 0.50, 3.90, 0.26),
    ("booting", 43, 49, 0.74, 0.68, 5.10, 0.16),
    ("heading_flowering", 51, 69, 0.88, 0.84, 5.80, 0.12),
    ("grain_filling", 71, 87, 0.98, 0.95, 3.20, 0.10),
    ("maturity", 89, 92, 1.00, 1.00, 0.80, 0.04),
]


def phase_for_bbch(bbch: int) -> dict:
    for name, uptake_fraction, biomass_fraction, lai_target, window_fraction in [
        (p[0], p[3], p[4], p[5], p[6]) for p in PHASES if p[1] <= bbch <= p[2]
    ]:
        return {
            "phase_name": name,
            "uptake_fraction": uptake_fraction,
            "expected_biomass_fraction": biomass_fraction,
            "expected_lai": lai_target,
            "window_fraction": window_fraction,
        }
    last = PHASES[-1]
    return {
        "phase_name": last[0],
        "uptake_fraction": last[3],
        "expected_biomass_fraction": last[4],
        "expected_lai": last[5],
        "window_fraction": last[6],
    }
