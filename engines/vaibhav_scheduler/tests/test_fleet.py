"""Sizing, job priority and the two schedulers of the fleet model."""

import random

import pytest

from digitalheat import (FirstFreeScheduler, FleetRun, HeatSavingScheduler, PowerProfile,
                         QueuedJob, Scenario, Site, TraceJob, job_stream, mean_heat_need_kw,
                         servers_needed)

PROFILE = PowerProfile("p", "System", "GPU", 8, "benchmark", "Offline", 5.0, "test")
RECOVERY = 0.8


def house(size=1.0):
    return Scenario("test", 0, [], []).heat_model(indoor_c=19.0).scaled(size)


def site(name, outdoor_c, servers=1, size=1.0, hours=96):
    return Site(name, house(size), [outdoor_c] * hours, [outdoor_c < 12] * hours, PROFILE,
                servers, RECOVERY, size)


def queued(gpus=4.0, run_h=3.0, arrival=0, factor=3.0, priority=2):
    job = TraceJob(f"j{gpus}-{run_h}", gpus, run_h, "worker", "test")
    return QueuedJob(job, arrival, run_h, arrival + factor * run_h, priority)


# --- sizing and scaling ---


def test_servers_cover_the_need_at_85_percent_usage():
    assert servers_needed(3.4, PROFILE, RECOVERY) == 1          # one server gives 3.4 kW at 85%
    assert servers_needed(3.5, PROFILE, RECOVERY) == 2
    assert servers_needed(0.2, PROFILE, RECOVERY) == 1          # never fewer than one
    assert servers_needed(34.0, PROFILE, RECOVERY) == 10


def test_mean_heat_need_is_loss_at_the_floor_plus_hot_water():
    assert mean_heat_need_kw(house(), 3.0) == pytest.approx(10 / 25.6 * 16 + 0.5)
    assert mean_heat_need_kw(house(4.0), 3.0) == pytest.approx(4 * (10 / 25.6 * 16 + 0.5))
    assert mean_heat_need_kw(house(), 30.0) == pytest.approx(0.5)       # hot water only


def test_a_house_twice_the_size_needs_twice_the_heat():
    small, big = house(1.0), house(2.0)
    for hour in range(48):
        a = small.deliver(3.0, -4.0, hour % 24)
        b = big.deliver(6.0, -4.0, hour % 24)
        assert b.backup_kwh == pytest.approx(2 * a.backup_kwh)
        assert b.indoor_c == pytest.approx(a.indoor_c)


def test_look_ahead_matches_stepping_the_model():
    rng = random.Random(3)
    model = house(2.0)
    heats = [rng.choice([0.0, 4.0, 15.0, 40.0]) for _ in range(72)]
    temps = [rng.uniform(-15, 25) for _ in range(72)]
    heating = [rng.random() < 0.8 for _ in range(72)]
    ahead = model.backup_ahead_kwh(heats, temps, 5, heating)
    state = (model.building.temp_c, model.tank.level_kwh)
    twin = model.copy()
    stepped = sum(twin.deliver(heats[i], temps[i], (5 + i) % 24, heating[i]).backup_kwh
                  for i in range(72))
    assert ahead == pytest.approx(stepped)
    assert (model.building.temp_c, model.tank.level_kwh) == state      # the model is untouched


# --- jobs ---


def test_priority_rises_with_waiting_and_with_a_nearing_deadline():
    job = queued(run_h=4.0, factor=3.0)                 # may wait 8 hours
    assert [job.priority(t) for t in (0, 1, 4, 8)] == pytest.approx([2, 4.25, 11, 20])
    tight = queued(run_h=4.0, factor=1.5)               # may wait 2 hours
    assert tight.priority(1) > job.priority(1)          # same wait, nearer deadline


def test_deadline_bookkeeping():
    job = queued(run_h=4.0, factor=1.5)                 # deadline at 6, latest start at 2
    assert job.slack_h(0) == 2 and not job.must_start(0)
    assert job.must_start(2) and not job.missed(2)
    assert job.missed(3)
    assert job.must_start(0, margin_h=3)


def test_partial_last_hour_of_a_job():
    job = queued(gpus=2.0, run_h=2.5)
    assert job.hours == 3 and job.load() == pytest.approx([2, 2, 1])
    assert job.gpu_hours == pytest.approx(5.0)


def test_job_stream_is_repeatable_and_asks_for_the_requested_load():
    jobs = [TraceJob("short", 1.0, 0.1, "worker", "test"), TraceJob("long", 4.0, 6.0, "w", "test")]
    weights = {"short": 0.7, "long": 0.3}
    first = job_stream(jobs, weights, 500, gpu_hours_per_hour=40, seed=7)
    again = job_stream(jobs, weights, 500, gpu_hours_per_hour=40, seed=7)
    assert [[j.key for j in hour] for hour in first] == [[j.key for j in hour] for hour in again]
    asked = sum(j.gpu_hours for hour in first for j in hour) / 500
    assert asked == pytest.approx(40, rel=0.1)
    assert all(j.run_h == 4.0 for hour in first for j in hour if j.job.id == "short")  # batched


# --- schedulers ---


def test_heat_saving_sends_the_job_to_the_house_that_needs_the_heat():
    warm, cold = site("warm", 25.0), site("cold", -5.0)
    job = queued()
    assert HeatSavingScheduler().choose(job, 0, [warm, cold])[0] is cold
    assert FirstFreeScheduler().choose(job, 0, [warm, cold])[0] is warm


def test_saving_shrinks_as_a_house_fills_up():
    cold = site("cold", -5.0, servers=4)
    first = queued(gpus=8.0, run_h=4.0)
    before = cold.saving_kwh(first, 0)
    for server in range(3):
        cold.place(queued(gpus=8.0, run_h=4.0), server, 0)
    assert cold.saving_kwh(first, 0) < before


def test_no_house_is_overbooked_and_full_houses_are_skipped():
    a, b = site("a", -5.0), site("b", -5.0)
    scheduler = FirstFreeScheduler()
    for _ in range(4):
        job = queued(gpus=4.0)
        target, server = scheduler.choose(job, 0, [a, b])
        target.place(job, server, 0)
    assert scheduler.choose(queued(gpus=4.0), 0, [a, b]) is None
    assert max(max(row) for s in (a, b) for row in s.used) == 8


def test_holding_waits_for_useful_heat_until_the_margin():
    warm = site("warm", 25.0, servers=2)
    held = HeatSavingScheduler(min_useful_share=0.5, start_margin_h=2)
    job = queued(gpus=8.0, run_h=4.0, factor=3.0)       # may wait 8 hours
    assert held.choose(job, 0, [warm]) is None          # hot water alone uses too little
    assert held.choose(job, 7, [warm]) is not None      # under 2 hours of slack: start anyway
    assert HeatSavingScheduler().choose(job, 0, [warm]) is not None    # default: never hold


def test_run_conserves_heat_and_counts_missed_jobs():
    sites = [site("small", -5.0, servers=1, size=0.5), site("big", 2.0, servers=2, size=2.0)]
    jobs = [TraceJob("a", 4.0, 3.0, "worker", "test"), TraceJob("b", 8.0, 5.0, "worker", "test")]
    stream = job_stream(jobs, {"a": 0.6, "b": 0.4}, 72, gpu_hours_per_hour=40, seed=1)  # too much
    run = FleetRun(sites, HeatSavingScheduler(), stream).run(72)
    arrived = sum(len(hour) for hour in stream)
    assert sum(run.started) + sum(run.missed) + len(run.queue) == arrived
    assert sum(run.missed) > 0                                   # more work than the fleet can do
    for s in sites:
        record = run.records[s.name]
        for t in range(72):
            assert record.absorbed[t] + record.dumped[t] == pytest.approx(
                s.heat_kwh(record.gpu_hours[t]))
            assert record.gpu_hours[t] <= s.gpus + 1e-9


def test_heat_saving_avoids_more_backup_heat_than_first_free():
    def fleet():
        return [site("warm", 14.0, servers=2, hours=240), site("cold", -8.0, servers=2, hours=240)]
    jobs = [TraceJob("a", 4.0, 3.0, "worker", "test"), TraceJob("b", 2.0, 6.0, "worker", "test")]

    def backup(scheduler):
        stream = job_stream(jobs, {"a": 0.5, "b": 0.5}, 168, gpu_hours_per_hour=14, seed=2)
        run = FleetRun(fleet(), scheduler, stream).run(168)
        return sum(sum(r.backup) for r in run.records.values())

    assert backup(HeatSavingScheduler()) < backup(FirstFreeScheduler()) < backup(None)


# --- what the dashboard reads ---


def test_placed_jobs_know_their_server_and_running_matches_the_bookings():
    cold = site("cold", -5.0, servers=2)
    first, second, later = queued(gpus=6.0, run_h=2.0), queued(gpus=4.0, run_h=5.0), queued(gpus=2.0)
    cold.place(first, cold.server_with_room(first, 0), 0)
    cold.place(second, cold.server_with_room(second, 0), 0)        # no room next to the first
    cold.place(later, cold.server_with_room(later, 3), 3)
    assert (first.server, second.server, later.server) == (0, 1, 0)
    for t in range(6):
        running = cold.running(t)
        assert sum(job.gpus for job in running) == pytest.approx(
            sum(booked[t] for booked in cold.used))
    assert cold.running(6) == []                                    # all finished and forgotten


def test_hourly_records_are_consistent():
    sites = [site("small", -5.0, servers=1, size=0.5), site("mild", 9.0, servers=2, size=2.0)]
    jobs = [TraceJob("a", 4.0, 3.0, "worker", "test"), TraceJob("b", 8.0, 5.0, "worker", "test")]
    stream = job_stream(jobs, {"a": 0.6, "b": 0.4}, 72, gpu_hours_per_hour=20, seed=4)
    run = FleetRun(sites, HeatSavingScheduler(min_useful_share=0.5), stream).run(72)
    for s in sites:
        r = run.records[s.name]
        for t in range(72):
            assert r.to_space[t] + r.to_water[t] == pytest.approx(r.absorbed[t])
            assert r.to_space[t] <= r.space_max[t] + 1e-9
            assert r.to_water[t] <= r.water_max[t] + 1e-9
            assert r.must_run[t] <= r.space_max[t] + r.water_max[t] + 1e-9
            assert r.absorbed[t] + r.backup[t] >= r.must_run[t] - 1e-9
    numbers = [job.number for hour in stream for job in hour]
    assert numbers == list(range(1, len(numbers) + 1))


def test_dropped_jobs_record_when_they_were_missed():
    full = site("full", -5.0, servers=1)
    blocker, late = queued(gpus=8.0, run_h=10.0), queued(gpus=8.0, run_h=2.0, factor=1.5)
    run = FleetRun([full], FirstFreeScheduler(), [[blocker, late]])
    run.run(4)
    assert blocker.started_h == 0 and late.started_h is None
    assert late.missed_h == 2 and sum(run.missed) == 1      # latest start was hour 1
