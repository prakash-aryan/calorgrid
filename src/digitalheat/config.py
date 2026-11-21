"""Physical, thermal, economic and schema definitions for DigitalHeat."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

PACKAGE_ROOT: Path = Path(__file__).resolve().parent.parent.parent
DATA_DIR: Path = PACKAGE_ROOT / "data"

# Server and water-loop recovery constants
SERVER_ELECTRICAL_POWER_KW: float = 1.0  # Case spec: 1 kW reference rack server
HEAT_RECOVERY_FRACTION: float = 0.80  # Case spec + Aquasar (Zimmermann et al., Energy 2012)
UNRECOVERED_HEAT_FRACTION: float = 0.20  # Conservation complement (air loss to basement)
SERVER_HEAT_OUTPUT_KW: float = 0.80  # SERVER_ELECTRICAL_POWER_KW * HEAT_RECOVERY_FRACTION
SERVER_HEAT_PER_DAY_KWH: float = 19.2  # 0.80 kW * 24 h

# Buffer tank constants (lossless 1,000 L / 30 K spread)
TANK_CAPACITY_KWH: float = 35.0  # Case spec usable thermal capacity
TANK_VOLUME_LITERS: float = 1000.0  # Case spec water volume
TANK_TEMP_SPREAD_K: float = 30.0  # Case spec temperature spread
TANK_TEMP_MIN_C: float = 30.0  # Return water floor
TANK_TEMP_MAX_C: float = 60.0  # Top supply temperature at 100% SOC
TANK_DEFAULT_START_KWH: float = 0.0  # Empty start for annual conservation balance

# Reference house thermal demand constants (Basel 2025)
SPACE_HEATING_PEAK_KW: float = 10.0  # Case spec peak at Basel design temp -5.6 C
INDOOR_SETPOINT_C: float = 20.0  # Case spec indoor temperature setpoint
DESIGN_TEMP_SPAN_K: float = 25.6  # 20.0 - (-5.6) C
HEATING_DAY_MEAN_THRESHOLD_C: float = 12.0  # Case spec: 24h local calendar mean < 12 C
DHW_KWH_PER_DAY: float = 12.0  # Case spec: domestic hot water daily demand
DHW_POWER_KW: float = 0.5  # 12.0 kWh / 24 h continuous rate (4,380 kWh/yr)

# Economic and tariff constants (ElCom 2026 IWB Basel)
ELCOM_BASEL_H4_CHF_PER_KWH: float = 0.3325  # ElCom 2026 IWB Basel H4 pinned (excl. VAT)
HEAT_PUMP_REF_COP: float = 2.5  # Case spec displaced heat-pump COP
HEAT_VALUE_CHF_PER_KWH: float = ELCOM_BASEL_H4_CHF_PER_KWH / HEAT_PUMP_REF_COP  # 0.133 CHF/kWh_th
SWISS_VAT_RATE: float = 0.081  # Swiss standard VAT rate 8.1%
DEFAULT_COMPUTING_PRICE_CHF_PER_SERVER_H: float = 0.25  # ASSUMPTION: illustrative batch rate

# ASSUMPTION: Amortized hardware + plumbing CAPEX per server-year (low, central, high)
CAPEX_CHF_PER_SERVER_YR_BANDS: dict[str, float] = {
    "low": 800.0,
    "central": 1400.0,
    "high": 2200.0,
}

DEFAULT_SERVERS_PER_HOUSE: int = 4  # Reference house cluster size (configurable 1..16)
HOURS_PER_YEAR: int = 8760
CONSERVATION_TOL_KWH: float = 1e-6  # Max allowed conservation residual per hour and year

# 5-line lumped 1-node basement air model parameters (ASSUMPTION, explicitly not 3D CFD)
BASEMENT_CAP_KWH_PER_K: float = 0.5  # Effective thermal mass of basement air and masonry surface
BASEMENT_UA_KW_PER_K: float = 0.12  # Conductance + trickle ventilation (120 W/K)
BASEMENT_START_TEMP_C: float = 12.0  # Initial Jan 1 basement air temperature

# Water specific heat capacity for loop flow calculations
WATER_CP_KJ_PER_KG_K: float = 4.184
ASHRAE_W45_MAX_SUPPLY_C: float = 45.0


class HourlyWeather(BaseModel):
    """Single localized hourly weather record in Europe/Zurich."""

    hour_index: int
    timestamp_utc: str
    timestamp_zurich: str
    local_date: str
    local_hour: int
    utc_offset_hours: float
    outdoor_temp_c: float
    shortwave_radiation_wm2: float
    direct_normal_irradiance_wm2: float

    @property
    def utc_iso(self) -> str:
        return self.timestamp_utc

    @property
    def zurich_iso(self) -> str:
        return self.timestamp_zurich

    @property
    def zurich_date(self) -> str:
        return self.local_date

    @property
    def zurich_hour(self) -> int:
        return self.local_hour

    @property
    def temperature_c(self) -> float:
        return self.outdoor_temp_c


class WeatherDataset(BaseModel):
    """Pinned 8,760-hour calendar year weather dataset."""

    site: Literal["basel", "davos"]
    latitude: float
    longitude: float
    elevation_m: float
    hours: list[HourlyWeather]
    heating_days: set[str]
    days_count: int
    manifest_sha256: str


class JobTypeSpec(BaseModel):
    """Digital Humanities batch workload definition and 1 kW scaling rule."""

    job_type: Literal["archive_ocr", "iiif_embeddings", "audio_transcription"]
    label: str
    unit_description: str
    duration_hours_at_1kw: int
    energy_kwh_per_job: float
    deadline_hours: int
    interruptible: bool
    arrival_share: float
    local_cpu_runnable: bool
    scaling_formula: str


class JobSpec(BaseModel):
    """Concrete batch job instance in the annual arrival stream."""

    job_id: int
    job_type: str
    arrival_hour: int
    duration_hours: int
    remaining_hours: int
    deadline_hour: int
    interruptible: bool = True


class SchedulerDecision(BaseModel):
    """Single hourly control decision emitted by a scheduler."""

    timestamp_zurich: str
    server_power_kw: float
    active_servers: int
    jobs_completed: int
    jobs_missed_deadline: int
    heat_signal_on: bool


class HourlySimulationRow(BaseModel):
    """Hourly thermal and electrical simulation output."""

    timestamp_zurich: str
    outdoor_temp_c: float
    solar_irradiance_wm2: float
    space_heating_kw: float
    dhw_kw: float
    heat_demand_kw: float
    extra_sink_demand_kw: float = 0.0
    server_power_kw: float
    active_servers: int
    heat_recovered_kw: float
    tank_soc_kwh: float
    heat_used_kw: float
    heat_used_primary_kw: float
    heat_used_secondary_kw: float = 0.0
    heat_dumped_kw: float
    backup_heat_kw: float
    basement_air_temp_c: float
    heat_signal_on: bool
    conservation_residual_kwh: float


class KpiReport(BaseModel):
    """Seven mandatory DigitalHeat KPIs plus supporting accounting fields."""

    servers_per_house: int
    heat_demand_covered_share: float
    heat_used_kwh: float
    heat_used_primary_kwh: float
    heat_used_secondary_kwh: float
    heat_demand_total_kwh: float
    backup_heat_kwh: float
    heat_dumped_kwh: float
    heat_recovered_kwh: float
    heat_dumped_share: float
    server_utilisation: float
    missed_deadlines_pct: float
    total_jobs_arrived: int
    total_jobs_completed: int
    total_jobs_missed: int
    jobs_in_queue_year_end: int
    electricity_cost_chf: float
    computing_income_chf: float
    heat_value_used_chf: float
    net_opex_margin_chf: float
    break_even_computing_price_chf_per_server_hour: float
    break_even_defined: bool
    server_hours_sold: float
    electricity_consumed_kwh: float


class SimulationResult(BaseModel):
    """Complete verified annual simulation result."""

    strategy: str
    site: str
    kpis: KpiReport
    negative_payoff_statement: str
    arrival_rate_source: str
    scaling_notes: str
    conservation_residual_kwh: float
    max_hourly_residual_kwh: float
    s_start_kwh: float
    s_end_kwh: float
    heating_days_count: int
    hourly: list[HourlySimulationRow] = Field(default_factory=list)
