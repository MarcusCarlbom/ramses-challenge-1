"""
Runs one scenario with a UPISAS strategy and saves what happened in a CSV file.

    python run_ramses.py --scenario 2                                  # the baseline (RAMSES's logic in UPISAS)
    python run_ramses.py --scenario 2 --strategy UPISAS.strategies.my_strategy:MyStrategy

RAMSES (RUN_ALL.sh) and the interface (python ../interface/api.py) must be running. This script measures the
results itself, from the same /monitor data for every strategy. That way compare.py can compare any two strategies.
"""
import argparse
import csv
import importlib
import logging
import os
import sys
import time
from datetime import datetime

from UPISAS.exemplars.ramses import RAMSES

SERVICES = ["ORDERING-SERVICE", "RESTAURANT-SERVICE", "PAYMENT-PROXY-SERVICE", "DELIVERY-PROXY-SERVICE"]
COLUMNS = ["time_s", "service", "instances", "requests", "successful", "art_ms", "availability", "adaptations"]


def load_strategy(spec):
    module_name, class_name = spec.split(":")
    return getattr(importlib.import_module(module_name), class_name)


def totals(service):
    """Returns the request counters of a service, for each instance, as (requests, successful, successful ms).
    The counters only go up. A request counts as successful when its status is below 500, as in RAMSES."""
    result = {}
    for snapshot in service.get("snapshot") or []:
        requests = successful = duration = 0
        for metrics in (snapshot.get("httpMetrics") or {}).values():
            for outcome in (metrics.get("outcomeMetrics") or {}).values():
                requests += outcome["count"]
                if outcome["status"] < 500:
                    successful += outcome["count"]
                    duration += outcome["totalDuration"]
        result[snapshot["instanceId"]] = (requests, successful, duration)
    return result


def measure(previous, current):
    """Requests handled by each service between two /monitor calls, summed over instances present in both."""
    rows = {}
    for name in SERVICES:
        before = totals(previous.get(name) or {})
        after = totals(current.get(name) or {})
        requests = successful = duration = 0
        for instance_id, (r, s, d) in after.items():
            r0, s0, d0 = before.get(instance_id, (0, 0, 0))
            if r < r0:  # counters reset (instance restarted): skip this interval for it
                continue
            requests += r - r0
            successful += s - s0
            duration += d - d0
        rows[name] = {
            "instances": len((current.get(name) or {}).get("instances") or []),
            "requests": requests,
            "successful": successful,
            "art_ms": round(duration / successful, 1) if successful else "",
            "availability": round(successful / requests, 4) if requests else "",
        }
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", type=int, choices=[1, 2, 3], default=3,
                        help="which scenario to run (see scenarios/README.md)")
    parser.add_argument("--minutes", type=float, default=None,
                        help="run length (default: the scenario's length, see scenarios/README.md)")
    parser.add_argument("--original-timing", action="store_true",
                        help="use the artifact's original fault times and 10-minute runs instead of the short ones")
    parser.add_argument("--strategy", default="UPISAS.strategies.ramses_strategy:RamsesStrategy",
                        help="module:Class of the strategy to run")
    parser.add_argument("--interval", type=float, default=None,
                        help="seconds between MAPE-K iterations (default: the strategy's LOOP_PERIOD_SECONDS, else 5)")
    parser.add_argument("--out", default=None, help="CSV path (default: results/<strategy>_scenario<N>_<time>.csv)")
    parser.add_argument("--ramses-adapts", action="store_true",
                        help="let RAMSES's own MAPE-K loop adapt (ADAPT=1); UPISAS only measures")
    args = parser.parse_args()

    strategy_class = load_strategy(args.strategy)
    interval = args.interval or getattr(strategy_class, "LOOP_PERIOD_SECONDS", 5)
    name = "RAMSES" if args.ramses_adapts else strategy_class.__name__
    out = args.out or os.path.join(
        "results", f"{name}_scenario{args.scenario}_{datetime.now():%Y%m%d_%H%M%S}.csv")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)

    exemplar = RAMSES(scenario=args.scenario, minutes=args.minutes, ramses_adapts=args.ramses_adapts,
                      original_timing=args.original_timing)
    args.minutes = exemplar.minutes
    strategy = strategy_class(exemplar)
    try:
        strategy.get_monitor_schema()
        strategy.get_execute_schema()
        strategy.get_adaptation_options_schema()
        strategy.get_adaptation_options()
    except Exception as e:
        exemplar.stop_container()
        sys.exit(f"Interface not reachable at {exemplar.base_endpoint} ({e!r}). Start it with: python ../interface/api.py")

    who = "RAMSES itself (ADAPT=1)" if args.ramses_adapts else f"strategy {args.strategy}"
    print(f"Scenario {args.scenario}, {args.minutes} min, {who}, every {interval}s -> {out}", flush=True)
    exemplar.start_run()
    start = time.monotonic()
    previous = None
    with open(out, "w", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=COLUMNS)
        writer.writeheader()
        try:
            while time.monotonic() - start < args.minutes * 60:
                iteration_start = time.monotonic()
                adaptations = 0
                try:
                    strategy.monitor()
                    if not args.ramses_adapts and strategy.analyze() and strategy.plan():
                        strategy.execute()
                        adaptations = 1
                except Exception as e:
                    logging.exception(f"iteration failed: {e!r}")
                history = strategy.knowledge.monitored_data.get("services") or []
                if history:
                    if previous is not None:
                        elapsed = round(time.monotonic() - start, 1)
                        for name, row in measure(previous, history[-1]).items():
                            writer.writerow({"time_s": elapsed, "service": name, "adaptations": adaptations, **row})
                        csv_file.flush()
                    previous = history[-1]
                if exemplar.get_container_status() not in ("running", "created"):
                    logging.warning("scenario container stopped, ending the run")
                    break
                time.sleep(max(0.0, interval - (time.monotonic() - iteration_start)))
        except KeyboardInterrupt:
            print("Interrupted")
        finally:
            exemplar.stop_container()
    print(f"Done. Results: {out}\nSummary: python compare.py {out}")


if __name__ == "__main__":
    main()
