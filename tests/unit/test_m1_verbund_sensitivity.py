"""Unit tests for all 7 Verbund thermal scenes and 5 Pilot Reality sensitivity analyses."""

import pytest

from digitalheat.config import CONSERVATION_TOL_KWH
from digitalheat.physics import (
    VERBUND_SCENE_IDS,
    compute_loop_flow_l_per_min,
    compute_pilot_reality_sensitivity,
    list_verbund_scenes,
    simulate_verbund_scene,
)


def test_all_seven_verbund_scenes_conserve_energy_and_compare_sinks() -> None:
    scenes = list_verbund_scenes(n_servers=4)
    assert [s["scene_id"] for s in scenes] == list(VERBUND_SCENE_IDS)

    by_id = {s["scene_id"]: s for s in scenes}
    for sid, scene in by_id.items():
        assert scene["continuous_mode"]["conservation_residual_kwh"] <= CONSERVATION_TOL_KWH
        assert scene["heat_led_mode"]["conservation_residual_kwh"] <= CONSERVATION_TOL_KWH
        assert scene["primary_flow_l_per_min"] > 0.0
        valve = scene["july_valve_split_pct"]
        assert valve["primary_pct"] + valve["secondary_pct"] + valve["dump_pct"] == pytest.approx(
            100.0, abs=1e-6
        )

    # Dual-sink Erdsonde and Pool eliminate >=85% of Basel dump and beat 64.6% single-house util
    assert by_id["basel_erdsonde"]["ashrae_w45_compliant"] is False
    assert by_id["basel_erdsonde"]["primary_w45_compliant"] is False
    assert by_id["basel_erdsonde"]["secondary_w45_compliant"] is True
    assert by_id["basel_erdsonde"]["continuous_mode"]["dump_elimination_vs_basel_house_pct"] > 85.0
    assert by_id["basel_erdsonde"]["heat_led_mode"]["server_utilisation_pct"] > 90.0
    assert by_id["basel_pool_st_jakob"]["heat_led_mode"]["server_utilisation_pct"] > 90.0
    assert (
        by_id["basel_pool_st_jakob"]["separate_vs_paired_comparison"][
            "standalone_seasonal_pool_dump_kwh"
        ]
        > 10000.0
    )
    assert (
        by_id["sole_uno"]["separate_vs_paired_comparison"]["standalone_seasonal_pool_dump_kwh"]
        > 10000.0
    )
    assert (
        by_id["sole_uno"]["separate_vs_paired_comparison"][
            "standalone_seasonal_pool_heat_led_util_pct"
        ]
        is not None
    )

    # Year-round W45 baseload hosts (Sole Uno and ARA Basel) eliminate 100% of dumping
    assert by_id["sole_uno"]["continuous_mode"]["heat_dumped_kwh"] == 0.0
    assert by_id["ara_basel_biogas"]["continuous_mode"]["heat_dumped_kwh"] == 0.0
    assert by_id["ara_basel_biogas"]["ashrae_w45_compliant"] is True

    # Davos alpine climate covers higher utilisation than Basel single-house
    assert by_id["davos_alpine"]["heat_led_mode"]["server_utilisation_pct"] > 80.0


def test_july_valve_split_sums_to_100_across_cluster_sizes() -> None:
    for n in range(1, 17):
        for sid in VERBUND_SCENE_IDS:
            scene = simulate_verbund_scene(sid, n_servers=n)
            v = scene["july_valve_split_pct"]
            assert v["primary_pct"] >= 0.0
            assert v["secondary_pct"] >= 0.0
            assert v["dump_pct"] >= 0.0
            assert (v["primary_pct"] + v["secondary_pct"] + v["dump_pct"]) == pytest.approx(
                100.0, abs=1e-6
            )


def test_unknown_verbund_scene_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Unknown Verbund scene_id"):
        simulate_verbund_scene("nonexistent_scene")


def test_loop_flow_calculation_matches_thermodynamics() -> None:
    # 0.8 kW_th at 10 K spread: (0.8 * 60) / (4.184 * 10) = 1.1472 L/min
    assert compute_loop_flow_l_per_min(0.8, 10.0) == pytest.approx(1.1472, abs=1e-3)
    with pytest.raises(ValueError, match="positive"):
        compute_loop_flow_l_per_min(0.8, 0.0)
    with pytest.raises(ValueError, match=">= 0"):
        compute_loop_flow_l_per_min(-1.0, 10.0)
    with pytest.raises(ValueError, match="finite"):
        compute_loop_flow_l_per_min(float("nan"), 10.0)


def test_pilot_reality_sensitivity_all_five_panels() -> None:
    sens = compute_pilot_reality_sensitivity(n_servers=4)
    assert set(sens.keys()) == {
        "servers_per_house",
        "heatwise",
        "mlperf_v51",
        "ashrae_w45",
        "capex_utilisation_cliff",
        "tariff_gap",
    }

    hw = sens["heatwise"]
    assert 0.20 <= hw["idle_to_full_ratio_range"]["min"] <= 0.25
    assert len(hw["sigstop_idle_penalty_sweep"]) == 3
    assert hw["throttling_comparison"]["air_frequency_slowdown_pct"] > 5.0
    assert [r["recovery_fraction"] for r in hw["recovery_fraction_sweep"]] == [0.3, 0.65, 0.8]
    assert "idle_power_assumption" in hw

    ml = sens["mlperf_v51"]
    assert len(ml["rows"]) == 11
    for row in ml["rows"]:
        assert row["chf_per_1m_units_at_full_reuse"] < row["chf_per_1m_units_at_zero_reuse"]

    w45 = sens["ashrae_w45"]
    assert w45["mixed_w45_effective_capacity_kwh"] == pytest.approx(17.5, abs=1e-6)
    assert w45["mixed_w45_server_utilisation_pct"] <= w45["stratified_server_utilisation_pct"]

    cliff = sens["capex_utilisation_cliff"]["cliff_rows"]
    assert [r["servers_per_house"] for r in cliff] == [1, 2, 4, 8, 12]
    assert cliff[0]["server_utilisation_pct"] > cliff[-1]["server_utilisation_pct"]
    assert (
        cliff[-1]["total_break_even_chf_per_srv_h"]["central"]
        > cliff[0]["total_break_even_chf_per_srv_h"]["central"]
    )

    tariffs = {r["category"]: r for r in sens["tariff_gap"]["tariffs"]}
    assert tariffs["H4"]["total_excl_vat_chf_per_kwh"] == pytest.approx(0.3325, abs=1e-5)
    assert tariffs["H4"]["total_incl_vat_chf_per_kwh"] == pytest.approx(0.3325 * 1.081, abs=1e-5)
    assert tariffs["C2"]["h4_penalty_excl_vat_chf_per_kwh"] > 0.0
    assert tariffs["C6"]["h4_penalty_excl_vat_chf_per_kwh"] == pytest.approx(0.07983, abs=1e-5)
    assert tariffs["C6"]["h4_penalty_incl_vat_chf_per_kwh"] == pytest.approx(0.08630, abs=1e-5)
    assert tariffs["C5"]["h4_penalty_incl_vat_chf_per_kwh"] > 0.04

