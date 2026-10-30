"""
Summarizes and compares run CSVs written by run_ramses.py.

    python compare.py results/*.csv ../baseline/*.csv
    python compare.py results/*.csv --service RESTAURANT-SERVICE
    python compare.py results/*.csv --plot comparison.png      # needs matplotlib

Runs with the same strategy and scenario in their file name (<strategy>_scenario<N>_<time>.csv) are grouped.
For a group of several runs it shows the mean and the standard deviation. The goals are RAMSES's own, from
UPISAS/strategies/ramses_specs/qos_specification.json.
"""
import argparse
import csv
import json
import os
import re
import statistics
from collections import defaultdict

SPEC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "UPISAS", "strategies", "ramses_specs",
                    "qos_specification.json")

METRICS = [
    ("art_ms", "mean RT (ms)"),
    ("availability", "availability"),
    ("rt_ok", "RT goal met (%)"),
    ("av_ok", "avail. goal met (%)"),
    ("mean_instances", "mean instances"),
    ("max_instances", "max instances"),
    ("adaptations", "adaptations"),
]


def goals(service):
    with open(SPEC) as spec_file:
        for s in json.load(spec_file)["services"]:
            if s["service_id"] == service:
                qos = {q["name"]: q for q in s["qos"]}
                return qos["average_response_time"]["max_threshold"], qos["availability"]["min_threshold"]
    raise SystemExit(f"unknown service {service}")


def summarize(path, service):
    max_rt, min_av = goals(service)
    rows = [r for r in csv.DictReader(open(path)) if r["service"] == service]
    if not rows:
        return None
    requests = sum(int(r["requests"]) for r in rows)
    successful = sum(int(r["successful"]) for r in rows)
    duration = sum(float(r["art_ms"]) * int(r["successful"]) for r in rows if r["art_ms"])
    with_rt = [float(r["art_ms"]) for r in rows if r["art_ms"]]
    with_av = [float(r["availability"]) for r in rows if r["availability"]]
    instances = [int(r["instances"]) for r in rows]
    adaptation_rows = {r["time_s"] for r in csv.DictReader(open(path)) if r["adaptations"] == "1"}
    return {
        "art_ms": duration / successful if successful else float("nan"),
        "availability": successful / requests if requests else float("nan"),
        "rt_ok": 100 * sum(v <= max_rt for v in with_rt) / len(with_rt) if with_rt else float("nan"),
        "av_ok": 100 * sum(v >= min_av for v in with_av) / len(with_av) if with_av else float("nan"),
        "mean_instances": statistics.mean(instances),
        "max_instances": max(instances),
        "adaptations": len(adaptation_rows),
        "rows": rows,
    }


def group_name(path):
    match = re.match(r"(.+_scenario\d)_", os.path.basename(path))
    return match.group(1) if match else os.path.splitext(os.path.basename(path))[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv", nargs="+")
    parser.add_argument("--service", default="ORDERING-SERVICE")
    parser.add_argument("--plot", help="write a response time / instances plot to this file")
    args = parser.parse_args()

    groups = defaultdict(list)
    for path in args.csv:
        summary = summarize(path, args.service)
        if summary:
            groups[group_name(path)].append(summary)

    max_rt, min_av = goals(args.service)
    print(f"{args.service}: goals RT <= {max_rt} ms, availability >= {min_av}\n")
    header = f"{'run':<42}{'n':>3}" + "".join(f"{label:>22}" for _, label in METRICS)
    print(header)
    print("-" * len(header))
    for name in sorted(groups):
        runs = groups[name]
        cells = []
        for key, _ in METRICS:
            values = [r[key] for r in runs]
            if len(values) > 1:
                cells.append(f"{statistics.mean(values):.3g} ± {statistics.stdev(values):.2g}")
            else:
                cells.append(f"{values[0]:.3g}")
        print(f"{name:<42}{len(runs):>3}" + "".join(f"{c:>22}" for c in cells))
    print("\nRT/availability: over all requests of the run. 'goal met': share of measurement intervals (one per"
          " MAPE-K iteration). adaptations: iterations in which the strategy executed something.")

    if args.plot:
        import matplotlib.pyplot as plt
        fig, (ax_rt, ax_inst) = plt.subplots(2, 1, sharex=True, figsize=(10, 6))
        for name in sorted(groups):
            rows = groups[name][0]["rows"]
            t = [float(r["time_s"]) for r in rows]
            ax_rt.plot(t, [float(r["art_ms"]) if r["art_ms"] else float("nan") for r in rows], label=name)
            ax_inst.step(t, [int(r["instances"]) for r in rows], where="post", label=name)
        ax_rt.axhline(max_rt, color="red", linestyle="--", linewidth=1, label="goal")
        ax_rt.set_ylabel(f"{args.service}\nresponse time (ms)")
        ax_inst.set_ylabel("instances")
        ax_inst.set_xlabel("time (s)")
        ax_rt.legend(fontsize="small")
        fig.tight_layout()
        fig.savefig(args.plot)
        print(f"plot written to {args.plot} (first run of each group)")


if __name__ == "__main__":
    main()
