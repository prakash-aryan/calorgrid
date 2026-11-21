"""Command-line interface for CalorGrid server, simulation and dataset verification."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter

from digitalheat.config import CONSERVATION_TOL_KWH, DATA_DIR, PACKAGE_ROOT
from digitalheat.data_loader import DatasetIntegrityError, load_weather_zurich, verify_manifest
from digitalheat.scheduler import run_annual_simulation


def _cmd_serve(host: str, port: int, reload: bool) -> int:
    import uvicorn

    uvicorn.run("digitalheat.api:app", host=host, port=port, reload=reload)
    return 0


def _cmd_simulate(strategy: str, servers: int, site: str, as_json: bool) -> int:
    res = run_annual_simulation(
        strategy=strategy,  # type: ignore[arg-type]
        n_servers=servers,
        site=site,  # type: ignore[arg-type]
        include_hourly=False,
    )
    if as_json:
        print(json.dumps(res.model_dump(exclude={"hourly"}), indent=2))
        return 0

    k = res.kpis
    print(
        f"CalorGrid Annual Simulation ({res.strategy}, site={res.site}, "
        f"servers={k.servers_per_house})"
    )
    print(
        f"  Heat demand covered: {100.0 * k.heat_demand_covered_share:.2f}% "
        f"({k.heat_used_kwh:.1f} / {k.heat_demand_total_kwh:.1f} kWh)"
    )
    print(
        f"  Heat dumped:         {k.heat_dumped_kwh:.2f} kWh ({100.0 * k.heat_dumped_share:.2f}%)"
    )
    print(
        f"  Server utilisation:  {100.0 * k.server_utilisation:.2f}% "
        f"({k.server_hours_sold:.0f} server-h)"
    )
    print(
        f"  Missed deadlines:    {k.missed_deadlines_pct:.2f}% "
        f"({k.total_jobs_missed}/{k.total_jobs_arrived} jobs)"
    )
    print(f"  Electricity cost:    {k.electricity_cost_chf:.2f} CHF")
    print(f"  Computing income:    {k.computing_income_chf:.2f} CHF")
    print(
        "  Break-even price:    "
        f"{k.break_even_computing_price_chf_per_server_hour:.4f} CHF/server-h"
    )
    print(
        f"  Conservation resid.: {res.conservation_residual_kwh:.3e} kWh "
        f"(max hourly {res.max_hourly_residual_kwh:.3e} kWh)"
    )
    print(f"  Negative payoff:     {res.negative_payoff_statement}")
    return 0


def _cmd_verify() -> int:
    try:
        verified = verify_manifest(data_dir=DATA_DIR)
    except DatasetIntegrityError as exc:
        print(f"ERROR: Dataset verification failed: {exc}", file=sys.stderr)
        return 1

    print(f"Datasets verified ({len(verified)} files): {', '.join(verified.keys())}")

    for site in ("basel", "davos"):
        ds = load_weather_zurich(site=site, data_dir=DATA_DIR, verify=False)  # type: ignore[arg-type]
        counts = Counter(h.local_date for h in ds.hours)
        spring_h = counts.get("2025-03-30", 0)
        autumn_h = counts.get("2025-10-26", 0)
        if len(ds.hours) != 8760 or ds.days_count != 365 or spring_h != 23 or autumn_h != 25:
            print(f"ERROR: DST or length check failed for {site}", file=sys.stderr)
            return 1
        print(
            f"  {site}: {len(ds.hours)} hours, {ds.days_count} days, "
            f"heating_days={len(ds.heating_days)}, "
            f"DST=(2025-03-30:{spring_h}h, 2025-10-26:{autumn_h}h)"
        )

    sim = run_annual_simulation(strategy="smart", n_servers=4, site="basel", include_hourly=False)
    if sim.conservation_residual_kwh > CONSERVATION_TOL_KWH:
        print(
            f"ERROR: Conservation residual {sim.conservation_residual_kwh} "
            f"exceeds {CONSERVATION_TOL_KWH}",
            file=sys.stderr,
        )
        return 1
    print(
        f"  Conservation check: smart basel N=4 residual={sim.conservation_residual_kwh:.3e} "
        f"kWh <= {CONSERVATION_TOL_KWH:.1e}"
    )

    assets_dir = PACKAGE_ROOT / "assets"
    manifest_path = assets_dir / "manifest.json"
    if manifest_path.is_file():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            print(f"ERROR: Invalid assets manifest {manifest_path}: {exc}", file=sys.stderr)
            return 1
        if not isinstance(data, dict):
            print(
                f"ERROR: Invalid assets manifest {manifest_path}: expected JSON object",
                file=sys.stderr,
            )
            return 1
        assets_map = data.get("assets")
        if not isinstance(assets_map, dict):
            print(
                f"ERROR: Invalid assets manifest {manifest_path}: 'assets' must be an object",
                file=sys.stderr,
            )
            return 1
        present = sum(
            1
            for meta in assets_map.values()
            if isinstance(meta, dict)
            and isinstance(meta.get("file"), str)
            and meta["file"]
            and (assets_dir / meta["file"]).is_file()
        )
        print(f"  Assets manifest: {present}/{len(assets_map)} GLB files present on disk")
    else:
        print("  Assets manifest: not present yet")

    return 0


def _bounded_int(min_val: int, max_val: int, arg_name: str):
    def _parse(value: str) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                f"{arg_name} must be an integer, got {value!r}"
            ) from exc
        if parsed < min_val or parsed > max_val:
            raise argparse.ArgumentTypeError(
                f"{arg_name} must be between {min_val} and {max_val}, got {parsed}"
            )
        return parsed

    return _parse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="digitalheat", description="CalorGrid CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    serve_p = sub.add_parser("serve", help="Run the CalorGrid FastAPI server")
    serve_p.add_argument("--host", default="127.0.0.1")
    serve_p.add_argument("--port", type=_bounded_int(1, 65535, "--port"), default=8000)
    serve_p.add_argument("--reload", action="store_true")

    sim_p = sub.add_parser("simulate", help="Run the 8,760h annual CalorGrid simulation")
    sim_p.add_argument(
        "--strategy",
        default="smart",
        choices=("smart", "smart_heat_led", "smart_sla", "baseline"),
    )
    sim_p.add_argument("--servers", type=_bounded_int(1, 16, "--servers"), default=4)
    sim_p.add_argument("--site", default="basel", choices=("basel", "davos"))
    sim_p.add_argument("--json", action="store_true", dest="as_json")

    sub.add_parser("verify", help="Verify pinned SHA-256 datasets, DST alignment and assets")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "serve":
        return _cmd_serve(host=args.host, port=args.port, reload=args.reload)
    if args.command == "simulate":
        return _cmd_simulate(
            strategy=args.strategy,
            servers=args.servers,
            site=args.site,
            as_json=args.as_json,
        )
    if args.command == "verify":
        return _cmd_verify()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
