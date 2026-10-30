# Baseline results

Results of the baseline strategy (`RamsesStrategy_scenario<N>_<time>.csv`) to compare your strategy with. They use
the short scenarios (the default in `run_ramses.py`) and a fresh RAMSES before every run.

To compare your own runs with these, run this in the `UPISAS/` folder.

```bash
python compare.py results/*.csv ../baseline/*.csv
```

## Setup

| | |
|---|---|
| Computer | MacBook Pro, Apple M3 Pro, macOS 26.5 |
| Docker Desktop | 4.97 (engine 29.9), 8 CPUs, 10 GB memory, 1 GB swap |
| Runs | one per scenario |

Runs are expected to use the same Docker settings, 8 CPUs and 10 GB memory. Numbers can still differ a bit on
another computer. If yours is very different, run the baseline yourself too (`python run_ramses.py --scenario 1`,
and the same for 2 and 3).

## ORDERING-SERVICE

The goals are a response time of at most 1000 ms and an availability of at least 0.87. "Goal met" is the share
of the 5-second measurements in which the goal was met.

| Scenario | Mean response time | Availability | Response time goal met | Availability goal met | Mean instances | Max instances |
|---|---|---|---|---|---|---|
| 1 | 784 ms | 0.988 | 97 % | 97 % | 1.0 | 1 |
| 2 | 974 ms | 1.000 | 52 % | 100 % | 1.42 | 2 |
| 3 | 423 ms | 0.895 | 82 % | 71 % | 1.83 | 3 |

In scenario 1 the problem is in `RESTAURANT-SERVICE`. The crashed instance was replaced, and the new one was
handling requests again after 141 s.

## What the baseline does

- Scenario 1. It notices the crashed `restaurant-service` instance, removes it, starts a new one and sends all
  requests to it.
- Scenario 2. About 45 s after the slowdown starts it adds an `ordering-service` instance and splits requests
  50/50. Later it moves more of them to the faster instance (weights 0.8 and 0.2).
- Scenario 3. It adds an instance for the slowdown and more instances when availability drops. The extra
  instances help only a little here, so there is room for a better strategy.
