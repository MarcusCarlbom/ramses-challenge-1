# Evaluation scenarios

Your UPISAS strategy is compared with the baseline strategy on three scenarios. The baseline is
`UPISAS/UPISAS/strategies/ramses_strategy.py`, which is RAMSES's own decision logic as a UPISAS strategy.

The scenarios come with RAMSES. You don't have to build anything, because `run_ramses.py` downloads them as
Docker images when you run them.

## What a scenario does

A scenario sends a steady stream of user requests to SEFA, like many users ordering food at the same time. At
fixed moments it causes a problem, for example it makes a service slow or makes it crash. The strategy has to
notice the problem and fix it.

The table shows when each problem starts, in seconds after the scenario starts.

| # | What happens to SEFA | Length | What a strategy has to handle |
|---|---|---|---|
| 1 | The `restaurant-service` instance crashes at 60 s, and the scenario tries again at 180 s | 4 min | Notice that a service has no working instance, and start a new one |
| 2 | `ordering-service` gets 1000 ms slower per request from 60 to 120 s, and 600 ms slower from 150 to 210 s | 4 min | Keep the response time of `ordering-service` under 1000 ms |
| 3 | The same slowdowns as scenario 2. Then the original `ordering-service` instance starts failing requests. From 200 s 28 % of its requests fail, from 300 s 70 % | 6 min | Keep both the response time and the availability (at least 0.87) |

In scenario 1 the second crash at 180 s has no effect after a successful recovery. The scenario always tries to
crash the original instance, and that one is already gone. This is how RAMSES built the scenario.

In scenario 3 only the original `ordering-service` instance fails requests. Instances that are added later work
normally. So the share of requests sent to the original instance decides how much availability drops.

### Short and original timing

RAMSES's own versions of these scenarios run for 10 minutes each, and most of that time nothing happens. By
default `run_ramses.py` uses the short versions in the table above. They have the same problems in the same
order, but the problems start earlier. The images are not rebuilt. The start times are settings of the scenario,
and `run_ramses.py` passes new values when it starts the container (see `SCENARIOS` in
`UPISAS/UPISAS/exemplars/ramses.py`).

Add `--original-timing` to use RAMSES's original 10-minute versions instead.

| # | Original timing |
|---|---|
| 1 | crashes at 180 s and 340 s |
| 2 | slowdowns from 90 to 150 s and from 180 to 240 s |
| 3 | slowdowns as in scenario 2, then 28 % failing requests from 260 s and 70 % from 400 s |

Problems are not moved earlier than 60 s. RAMSES (and the baseline) can make their first decision about 75 s
after the start, and they need about the same time again after every adaptation.

The exact settings inside each image are in `scenario1/`, `scenario2/` and `scenario3/` in this folder. They are
only there for reference. Changing them does not change the image.

## The goals

These are the goals RAMSES itself uses. A copy is in `UPISAS/UPISAS/strategies/ramses_specs/`.

| Service | Availability | Average response time |
|---|---|---|
| ORDERING-SERVICE | at least 0.87 | at most 1000 ms |
| RESTAURANT-SERVICE | at least 0.90 | at most 90 ms |
| PAYMENT-PROXY-SERVICE | at least 0.92 | at most 500 ms |
| DELIVERY-PROXY-SERVICE | at least 0.92 | at most 500 ms |

Availability is the share of requests that succeed. 0.87 means 87 % of requests succeed.

The comparison looks mostly at `ORDERING-SERVICE`. Scenarios 2 and 3 cause problems there, and it is the service
users call when they place an order. For scenario 1, also look at `RESTAURANT-SERVICE` with
`compare.py --service RESTAURANT-SERVICE`.

(The RAMSES download has two sets of goal files. The image uses `knowledge/architecture_sla/sefa/`. The other
set, in `knowledge/src/main/resources/`, has different values that are not used.)

## Running a scenario

Run these in the `UPISAS/` folder, with RAMSES and the interface running (main README, steps 4 and 5).

```bash
python run_ramses.py --scenario 2                                            # the baseline strategy
python run_ramses.py --scenario 2 --strategy UPISAS.strategies.my_strategy:MyStrategy   # your strategy
python run_ramses.py --scenario 2 --original-timing                         # RAMSES's 10-minute version
```

`run_ramses.py` starts the scenario with `ADAPT=0`. That tells RAMSES to only watch, so your strategy is the only
thing that adapts. When the scenario ends, the script stops it and saves a CSV file in `UPISAS/results/`.

Don't start a scenario with the question at the end of `RUN_ALL.sh`. That starts RAMSES's own scenarios with
RAMSES adapting at the same time as your strategy, and they are numbered differently.

## Rules for a fair comparison

1. Use a fresh RAMSES for every run. Instances added during a run keep running and new weights stay in your
   config repo, so run `reset_ramses.sh` before each run (main README, step 4).
2. Run with Docker set to 8 CPUs and 10 GB memory. The baseline results were made with these settings, and runs
   are expected to use the same. `check_setup.sh` checks them.
3. Compare short runs with short runs (the default) and original-timing runs with original-timing runs.
4. Run every scenario at least 3 times. The requests are random, so results vary a bit, and you should report
   the average and the standard deviation.
