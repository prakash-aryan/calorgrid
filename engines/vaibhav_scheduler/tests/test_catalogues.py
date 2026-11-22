"""The job and power catalogues built from the open datasets."""

from pathlib import Path

import pytest

import json

from digitalheat import (Placement, PowerProfile, TraceJob, gpu_hours_by_step,
                         load_fleet_machine, load_gas_price, load_power_profiles,
                         load_trace_jobs)

DATA = Path(__file__).resolve().parent.parent / "data"


def profile(full_load_kw=10.4, gpus=8):
    return PowerProfile("p", "System", "GPU", gpus, "benchmark", "Offline", full_load_kw, "test")


def job(gpus=2.0, duration_h=1.5):
    return TraceJob("j", gpus, duration_h, "worker", "test")


def test_power_is_proportional_to_the_gpus_working():
    p = profile(10.4, 8)
    assert p.power_kw(0) == 0.0
    assert p.power_kw(2) == pytest.approx(2.6)
    assert p.power_kw(8) == pytest.approx(10.4)
    assert p.power_kw(20) == pytest.approx(10.4)        # cannot draw more than full load


def test_a_job_asks_for_gpus_only_while_it_runs():
    placed = Placement(job(2.0, 1.5), start_h=3)
    assert gpu_hours_by_step([placed], 6) == pytest.approx([0, 0, 0, 2.0, 1.0, 0])
    assert sum(gpu_hours_by_step([placed], 6)) == pytest.approx(placed.job.gpu_hours)


def test_kept_running_job_fills_the_whole_window():
    placed = Placement(job(1.0, 0.1), start_h=2, keep_running_h=3)
    assert gpu_hours_by_step([placed], 6) == pytest.approx([0, 0, 1, 1, 1, 0])


def test_jobs_on_one_server_add_up_and_can_overbook_it():
    hours = gpu_hours_by_step([Placement(job(5.0, 2), 0), Placement(job(5.0, 2), 1)], 3)
    assert hours == pytest.approx([5, 10, 5])            # 10 > 8 GPUs: overbooked in hour 1


def test_a_job_is_cut_off_at_the_end_of_the_run():
    assert gpu_hours_by_step([Placement(job(1.0, 10), 22)], 24)[-2:] == pytest.approx([1, 1])


def test_bad_values_are_rejected():
    with pytest.raises(ValueError):
        job(gpus=0)
    with pytest.raises(ValueError):
        profile(full_load_kw=0)


def test_mlperf_file_gives_measured_systems():
    profiles = load_power_profiles(DATA / "mlperf_power.json")
    assert profiles
    for p in profiles:
        assert p.gpus == 8 and 1 < p.full_load_kw < 20
        assert "MLPerf Inference" in p.source and p.url.startswith("https://")


def test_trace_file_gives_jobs_that_fit_one_server():
    jobs = load_trace_jobs(DATA / "alibaba_gpu_jobs.json")
    assert len(jobs) == 15 and len({j.id for j in jobs}) == 15
    for j in jobs:
        assert 0 < j.gpus <= 8
        assert "Alibaba" in j.source


# --- the fleet machine: every figure describes the same server ---


def machine():
    return load_fleet_machine(DATA / "fleet_machine.json", DATA / "mlperf_power.json")


def test_power_price_and_market_all_describe_the_same_gpu():
    data = json.loads((DATA / "fleet_machine.json").read_text(encoding="utf-8"))
    m = machine()
    model = m.gpu.split()[-1]                                   # "H100"
    assert model in m.profile.accelerator                       # the measured power
    assert m.profile.gpus == data["machine"]["gpus_per_server"]
    assert model in data["hardware"]["system"]                  # the purchase price
    assert all(row["gpu"] == m.gpu for row in data["gpu_hour"])     # the market prices
    years = {data["hardware"]["year"], *(row["year"] for row in data["gpu_hour"]),
             *(e["year"] for e in data["electricity"])}
    assert years == {m.year}                                    # and all in the same year
    assert all(row[key] for row in data["gpu_hour"]
               for key in ("on_demand_source", "on_demand_url", "spot_source", "spot_url"))
    assert data["hardware"]["source"] and data["hardware"]["url"]


def test_the_hardware_estimate_has_a_low_and_a_high_case_and_markets_are_ordered():
    m = machine()
    low, mid, high = m.hardware_range_chf
    assert 0 < low < mid < high
    specialist, cloud = m.markets["h100-specialist"], m.markets["h100-aws"]
    assert 0 < specialist.spot_chf < specialist.on_demand_chf
    assert 0 < cloud.spot_chf < cloud.on_demand_chf
    assert specialist.on_demand_chf < cloud.on_demand_chf       # a large cloud charges more
    assert m.tariff_chf_per_kwh > 0 and 0.5 < m.chf_per_usd < 1.5


def test_every_trace_gpu_has_a_speed_factor_and_jobs_shorten_by_it():
    m = machine()
    rows = json.loads((DATA / "alibaba_gpu_jobs.json").read_text(encoding="utf-8"))["jobs"]
    assert {row["gpu_type"] for row in rows} <= set(m.speed_up)
    assert all(factor >= 1 for factor in m.speed_up.values())   # the fleet's GPU is the newer one
    assert set(m.assumed_speed_up) < set(m.speed_up)            # some factors are sourced
    recorded = load_trace_jobs(DATA / "alibaba_gpu_jobs.json")
    faster = load_trace_jobs(DATA / "alibaba_gpu_jobs.json", m.speed_up)
    for before, after in zip(recorded, faster):
        assert after.gpus == before.gpus and after.gpu_type == before.gpu_type
        assert after.duration_h == pytest.approx(before.duration_h / m.speed_up[before.gpu_type])
        assert "shortened" in after.source and "shortened" not in before.source
    with pytest.raises(ValueError):
        load_trace_jobs(DATA / "alibaba_gpu_jobs.json", {"T4": 15.0})   # a GPU type is missing


# --- the house's gas bills ---

BILL_HEADER = ("Messpunkt CH;Zeitraum (KalJahr/Monat);Sparte;Vertragskonto-Nr.;Objekt-Adresse;"
               "Zähler-Nr.;Kunden-Nr.;Kundenname;Ableseeinheit;Verbrauch Medium Normaltarif (kWh);"
               "Verbrauch Medium Spartarif (kWh);Verbrauch Medium Einfachtarif (kWh/m3);"
               "Verbrauch Netz Normaltarif (kWh);Verbrauch Netz Spartarif (kWh);"
               "Verbrauch Netz Einfachtarif (kWh/m3);Betrag Normaltarif (CHF);"
               "Betrag Spartarif (CHF);Betrag Einfachtarif (CHF);Betrag Energielieferung (CHF);"
               "Betrag Netznutzung (CHF);Betrag Abgaben (CHF);Gesamtbetrag (CHF)")


def bill(meter, month, unit, kwh, energy, levies, total):
    return f"{meter};{month};Gas;V;O;Z;K;P;{unit};0;0;{kwh};0;0;{kwh};0;0;0;{energy};0;{levies};{total}"


def test_gas_price_is_the_variable_part_weighted_by_use(tmp_path):
    import sys
    sys.path.insert(0, str(DATA.parent / "scripts"))
    import gas_price
    path = tmp_path / "bills.csv"
    path.write_text("\n".join([
        BILL_HEADER,
        bill("MESSPUNKT_004", "202501", "KWH", 1000, 100, 20, 120),     # 0.12
        bill("MESSPUNKT_004", "202501", "KW", 0, 40, 0, 40),            # capacity charge
        bill("MESSPUNKT_004", "202502", "KWH", 3000, 420, 60, 480),     # 0.16
        bill("MESSPUNKT_002", "202501", "KWH", 10, 5, 0, 5),            # not the heating meter
    ]), encoding="utf-8")
    months = gas_price.monthly(path)
    assert [m["chf_per_kwh"] for m in months] == [0.12, 0.16]
    assert months[0]["fixed_chf"] == 40 and months[1]["fixed_chf"] == 0
    year = gas_price.yearly(months)["2025"]
    assert year["chf_per_kwh"] == pytest.approx(0.15)                  # (120 + 480) / 4000
    assert year["fixed_chf_per_month"] == 20 and year["months"] == 2


def test_the_2025_gas_price_comes_from_the_heating_meter():
    gas = load_gas_price(DATA / "gas_price.json", 2025)
    assert gas.chf_per_kwh == pytest.approx(0.1355)
    assert gas.fixed_chf_per_month == pytest.approx(39.29)
    assert "gas bills" in gas.source
