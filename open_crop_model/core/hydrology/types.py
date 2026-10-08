"""All fluxes are field-equivalent mm; column states are local-area mm."""

from __future__ import annotations
from dataclasses import dataclass, field
from math import isfinite
from typing import Optional


def bounded(value, name, low=0.0, high=None):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
    ):
        raise ValueError(f"{name}: finite numeric value required")
    if value < low or (high is not None and value > high):
        raise ValueError(f"{name}: outside bounds")
    return value


@dataclass(frozen=True)
class SoilLayerParameters:
    thickness_mm: float
    air_dry: float
    wilting_point: float
    field_capacity: float
    saturation: float
    ksat_mm_day: float
    source: str = "explicit input; independent provenance required"
    retention_alpha_mm_inv: Optional[float] = None
    retention_n: Optional[float] = None

    def __post_init__(self):
        bounded(self.thickness_mm, "thickness_mm", 1e-9)
        for name in ("air_dry", "wilting_point", "field_capacity", "saturation"):
            bounded(getattr(self, name), name, 0, 1)
        if (
            not self.air_dry
            <= self.wilting_point
            < self.field_capacity
            < self.saturation
        ):
            raise ValueError("Require air_dry <= WP < FC < SAT")
        bounded(self.ksat_mm_day, "ksat_mm_day")
        if self.retention_alpha_mm_inv is not None or self.retention_n is not None:
            bounded(self.retention_alpha_mm_inv, "retention_alpha_mm_inv", 1e-9, 1.)
            bounded(self.retention_n, "retention_n", 1.00001, 10.)


@dataclass
class SoilColumnState:
    water_mm: list[float]
    pond_mm: float = 0.0


@dataclass
class DualDomainState:
    wetted_fraction: float
    wet: SoilColumnState
    dry: Optional[SoilColumnState]
    canopy_water_mm: float = 0.0
    last_date: Optional[str] = None

    def storage_mm(self):
        total = (
            self.wetted_fraction * (sum(self.wet.water_mm) + self.wet.pond_mm)
            + self.canopy_water_mm
        )
        if self.wetted_fraction < 1:
            total += (1 - self.wetted_fraction) * (
                sum(self.dry.water_mm) + self.dry.pond_mm
            )
        return total


@dataclass(frozen=True)
class HydrologyParameters:
    layers: tuple[SoilLayerParameters, ...]
    substeps: int = 24
    pond_capacity_mm: float = 10.0
    drainage_rate_day: float = 1.0
    exchange_mm_day: float = 0.0
    bottom_boundary: str = "free_drainage"
    water_table_conductance_day: float = 1.0
    capillary_length_mm: float = 500.0
    canopy_capacity_mm: float = 1.0
    dynamic_evaporation: bool = True
    root_extraction_fraction_day: float = 1.0
    source: str = "engineering priors; not field calibrated"
    # fixed_rate retains archived behavior. The conductivity reservoir uses
    # drainable pore storage / Ksat as its residence time, not drainage_rate_day.
    drainage_method: str = "fixed_rate"
    soil_evaporation_method: str = "linear_storage"
    # Readily evaporable storage / (FC - air-dry) surface-layer storage.
    # The surface layer is physically resolved in the supplied soil profile.
    readily_evaporable_fraction: float = 0.3
    # Effective root activity declines exponentially with depth (m-1).
    # Zero retains the archived uniform rooted-volume uptake law.
    root_density_decay_m_inv: float = 0.0
    # Mean-normalized activity redistributes a fixed extraction rate. Surface
    # normalization defines a local extraction coefficient declining at depth.
    root_activity_normalization: str = "rooted_volume_mean"
    plant_water_stress_method: str = "none"
    readily_available_water_fraction: float = 0.55
    # Numerical water-content change bound, independent of crop calibration.
    matric_max_theta_step: float = 0.005
    # Interpolate relative mobility separately from saturated layer resistance.
    # The archived nodal harmonic mean can lock a wet/dry interface.
    matric_interface_method: str = "relative_arithmetic"
    # FAO56 nominal p at ETc=5 mm/day can respond to daily potential ET.
    readily_available_water_adjustment: str = "none"
    # Fraction of unmet local uptake that can move to better supplied roots.
    # Applies only to compensated_layer_depletion; it cannot create water.
    root_compensation_fraction: float = 0.5
    # Fraction of activity assigned to a depth-uniform background within the
    # reference profile. Zero preserves the archived exponential mechanism.
    # Root dry mass can constrain this geometry only as a conditional proxy.
    root_activity_background_fraction: float = 0.0
    root_activity_reference_depth_mm: float = 1000.0

    def __post_init__(self):
        if not self.layers:
            raise ValueError("Empty soil profile")
        if (
            isinstance(self.substeps, bool)
            or not isinstance(self.substeps, int)
            or self.substeps < 1
        ):
            raise ValueError("Positive integer substeps required")
        for name in (
            "pond_capacity_mm",
            "drainage_rate_day",
            "exchange_mm_day",
            "water_table_conductance_day",
            "canopy_capacity_mm",
        ):
            bounded(getattr(self, name), name)
        bounded(self.capillary_length_mm, "capillary_length_mm", 1e-9)
        bounded(self.root_extraction_fraction_day, "root_extraction_fraction_day", 0, 1)
        bounded(self.root_density_decay_m_inv, "root_density_decay_m_inv", 0, 20)
        if isinstance(self.root_activity_background_fraction, bool):
            raise ValueError("Numeric root_activity_background_fraction required")
        bounded(self.root_activity_background_fraction, "root_activity_background_fraction", 0, 1)
        if isinstance(self.root_activity_reference_depth_mm, bool):
            raise ValueError("Numeric root_activity_reference_depth_mm required")
        bounded(self.root_activity_reference_depth_mm, "root_activity_reference_depth_mm", 1e-9)
        if not isinstance(self.root_activity_normalization, str) or self.root_activity_normalization not in {
            "rooted_volume_mean", "surface"
        }:
            raise ValueError("Unknown root_activity_normalization")
        bounded(self.readily_available_water_fraction, "readily_available_water_fraction", 0, .99)
        bounded(self.matric_max_theta_step, "matric_max_theta_step", 1e-5, .05)
        if not isinstance(self.matric_interface_method, str) or self.matric_interface_method not in {
            "relative_arithmetic", "harmonic_nodal", "pressure_integrated"
        }:
            raise ValueError("Unknown matric_interface_method")
        if self.plant_water_stress_method not in {
            "none", "root_zone_depletion", "layer_root_depletion", "compensated_layer_depletion"
        }:
            raise ValueError("Unknown plant_water_stress_method")
        if isinstance(self.root_compensation_fraction, bool):
            raise ValueError("Numeric root_compensation_fraction required")
        bounded(self.root_compensation_fraction, "root_compensation_fraction", 0, 1)
        if not isinstance(self.readily_available_water_adjustment, str) or self.readily_available_water_adjustment not in {
            "none", "fao56_daily_demand"
        }:
            raise ValueError("Unknown readily_available_water_adjustment")
        bounded(self.readily_evaporable_fraction, "readily_evaporable_fraction", 0, .99)
        if self.soil_evaporation_method not in {"linear_storage", "two_stage_storage"}:
            raise ValueError("Unknown soil_evaporation_method")
        if self.bottom_boundary not in {"free_drainage", "prescribed_water_table"}:
            raise ValueError("Unknown bottom boundary")
        if not isinstance(self.drainage_method, str) or self.drainage_method not in {
            "fixed_rate", "conductivity_reservoir", "matric_gradient"
        }:
            raise ValueError("Unknown drainage_method")
        if self.drainage_method in {"conductivity_reservoir", "matric_gradient"} and self.bottom_boundary != "free_drainage":
            raise ValueError(f"{self.drainage_method} requires free_drainage")
        if self.drainage_method == "matric_gradient" and any(layer.retention_n is None for layer in self.layers):
            raise ValueError("matric_gradient requires independent retention curves")


@dataclass(frozen=True)
class IrrigationEvent:
    event_id: str
    amount_mm: float
    method: str
    wetted_fraction: float
    application_evaporation_fraction: float
    drift_fraction: float
    canopy_fraction: float
    application_depth_mm: float = 0.0
    duration_minutes: Optional[float] = None
    start_minute: float = 0.0
    measurement_location: str = "field"
    conveyance_fraction: Optional[float] = None


@dataclass(frozen=True)
class IrrigationBoundary:
    field_input_mm: float
    wet_local_mm: float
    dry_local_mm: float
    canopy_input_mm: float
    application_evaporation_mm: float
    off_field_drift_mm: float
    pump_input_mm: float
    conveyance_loss_mm: float


@dataclass(frozen=True)
class WaterDayForcing:
    date: str
    precipitation_mm: float = 0.0
    irrigation_events: tuple[IrrigationEvent, ...] = ()
    canopy_cover: float = 0.0
    root_depth_mm: float = 0.0
    potential_transpiration_mm: float = 0.0
    potential_soil_evaporation_mm: float = 0.0
    water_table_depth_mm: Optional[float] = None
    potential_canopy_evaporation_mm: float = 0.0
    # Optional hourly amounts for intervals [00:00,01:00), ..., [23:00,24:00).
    # The sum must equal precipitation_mm; the caller declares the day clock.
    precipitation_hourly_mm: Optional[tuple[float, ...]] = None


@dataclass
class WaterBalanceResult:
    state: DualDomainState
    fluxes: dict[str, float]
    balance_residual_mm: float
    diagnostics: dict = field(default_factory=dict)
