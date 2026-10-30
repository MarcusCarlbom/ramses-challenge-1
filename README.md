# RAMSES + UPISAS starter

This is how the baseline strategy is run. You can follow the same steps to get RAMSES working yourself, or use
them if you are stuck getting RAMSES to run.

The baseline (`UPISAS/UPISAS/strategies/ramses_strategy.py`) is RAMSES's own adaptation logic rewritten as a
UPISAS strategy. RAMSES keeps running and monitoring, but the strategy makes all the decisions. Your strategy is
compared with it on the three scenarios in [`scenarios/`](scenarios/README.md). The reference results are in
[`baseline/`](baseline/README.md).

## 1. Install

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (tested with 4.97). In Settings → Resources
  set 8 CPUs, 10 GB memory and 1 GB swap. The baseline is run with these settings, and runs are expected to use
  the same, so your computer needs at least 8 cores and 16 GB of RAM. In Settings → Advanced turn on "Allow the
  default Docker socket to be used".
- socat and Python 3.10 or newer. On macOS `brew install socat python`, on Linux/WSL
  `sudo apt-get install socat python3 python3-venv qemu-user-static` (qemu lets Intel/AMD machines run the one
  RAMSES image that only exists for Apple Silicon).
- The Python packages, from this repo's folder. Run `source .venv/bin/activate` again in every new terminal
  where you use this repo's Python, so it uses the environment in `.venv`.
  ```bash
  python3 -m venv .venv
  source .venv/bin/activate
  pip install -r requirements.txt
  ```

Java is not needed. Tested on macOS 26.5.

## 2. Get RAMSES and a config repo

1. Download `SEAMS24_artifact_package.zip` from [Zenodo](https://doi.org/10.5281/zenodo.10400820) and unzip it.
   The commands below assume it is in your Downloads folder. If not, change `~/Downloads` to where it is.
2. Fork https://github.com/MarcusCarlbom/config-server and keep the fork public. (The config repo linked in the
   RAMSES README no longer exists, and this one also has a fix the RAMSES images need.)
3. Create a fine-grained token on GitHub (Settings → Developer settings → Fine-grained personal access tokens)
   with access to only your fork and Contents set to Read and write. Don't commit or share it.

## 3. Edit `RUN_ALL.sh`

These three edits are the only changes to RAMSES. They go in `SEAMS24_artifact_package/bash_scripts/execute/`.

- For macOS and Windows, edit `RUN_ALL.sh`.
- For Linux, edit `RUN_ALL_LINUX.sh`. Its line numbers are sometimes different, and edit 2 looks different there.

### Edit 1. Your config repo and token

**File** lines 7–8, in both `RUN_ALL.sh` and `RUN_ALL_LINUX.sh`

**Why** RAMSES reads SEFA's settings from your fork and uses the token to write to it.

Original
```bash
export GITHUB_REPOSITORY_URL=...
export GITHUB_OAUTH=...
```
New, with your GitHub username instead of `YOUR_USERNAME` and your whole token instead of `github_pat_YOUR_TOKEN`
```bash
export GITHUB_REPOSITORY_URL=https://github.com/YOUR_USERNAME/config-server.git
export GITHUB_OAUTH=github_pat_YOUR_TOKEN
```

### Edit 2. Fixed ports for the probe and the instances manager

**File** lines 90–98 in `RUN_ALL.sh` (macOS and Windows), or lines 93 and 100 in `RUN_ALL_LINUX.sh` (Linux)

**Why** The interface looks for these services on ports 55010 and 55011, and without the edit they get random
ports. `-e ARCH=$ARCH` makes new instances use the image for your machine instead of always the Apple Silicon one.

For macOS and Windows, in `RUN_ALL.sh`, replace lines 90–98.

Original
```bash
declare -a pract=("sefa-probe" "sefa-instances-manager")
for i in "${pract[@]}"
do
   PrintSuccess "Pulling $i"
   docker pull giamburrasca/$i:$ARCH
   docker run -P --name $i -d --network ramses-sas-net giamburrasca/$i:$ARCH
   echo
   sleep 1
done
```
New
```bash
PrintSuccess "Pulling sefa-probe"
docker pull giamburrasca/sefa-probe:$ARCH
docker run -p 55010:58020 --name sefa-probe -d --network ramses-sas-net giamburrasca/sefa-probe:$ARCH
sleep 1

PrintSuccess "Pulling sefa-instances-manager"
docker pull giamburrasca/sefa-instances-manager:$ARCH
docker run -p 55011:58015 --name sefa-instances-manager -e ARCH=$ARCH -d --network ramses-sas-net giamburrasca/sefa-instances-manager:$ARCH
sleep 1
```

For Linux, in `RUN_ALL_LINUX.sh`, change lines 93 and 100. There each service already has its own line.

Line 93 original
```bash
docker run -P --name sefa-probe -d --network ramses-sas-net giamburrasca/sefa-probe:$ARCH
```
Line 93 new
```bash
docker run -p 55010:58020 --name sefa-probe -d --network ramses-sas-net giamburrasca/sefa-probe:$ARCH
```
Line 100 original
```bash
docker run -P --name sefa-instances-manager -d --network ramses-sas-net --add-host=host.docker.internal:host-gateway giamburrasca/sefa-instances-manager:$ARCH
```
Line 100 new
```bash
docker run -p 55011:58015 --name sefa-instances-manager -e ARCH=$ARCH -d --network ramses-sas-net --add-host=host.docker.internal:host-gateway giamburrasca/sefa-instances-manager:$ARCH
```

### Edit 3. Fixed port for the config manager

**File** line 102 in `RUN_ALL.sh` (macOS and Windows), or line 106 in `RUN_ALL_LINUX.sh` (Linux). The line is the
same in both files.

**Why** Same as edit 2. The interface looks for the config manager on port 55012.

Original
```bash
docker run -P --name sefa-config-manager -e GITHUB_OAUTH=$GITHUB_OAUTH -e GITHUB_REPOSITORY_URL=$GITHUB_REPOSITORY_URL -d --network ramses-sas-net giamburrasca/sefa-config-manager:$ARCH
```
New
```bash
docker run -p 55012:58016 --name sefa-config-manager -e GITHUB_OAUTH=$GITHUB_OAUTH -e GITHUB_REPOSITORY_URL=$GITHUB_REPOSITORY_URL -d --network ramses-sas-net giamburrasca/sefa-config-manager:$ARCH
```

## 4. Start RAMSES

Start socat in its own terminal and leave it running. RAMSES uses it to start and stop containers.

```bash
socat -d TCP-LISTEN:2375,bind=127.0.0.1,reuseaddr,fork UNIX:/var/run/docker.sock
```

In a second terminal, from this repo's folder, check your setup and then start RAMSES.

```bash
./check_setup.sh ~/Downloads/SEAMS24_artifact_package/bash_scripts/execute/RUN_ALL.sh
./reset_ramses.sh ~/Downloads/SEAMS24_artifact_package/bash_scripts/execute/RUN_ALL.sh
```

`check_setup.sh` changes nothing. It lists anything that is missing and how to fix it. `reset_ramses.sh` removes
old RAMSES containers, cleans leftover weights from your config repo, runs `RUN_ALL.sh` and waits until all 18
containers are up. The first time it downloads about 5 GB, which takes a while. Use the same command before every
new run, so each run starts from a fresh RAMSES.

## 5. Start the interface

UPISAS can't talk to RAMSES directly. It talks to a small web server in this repo, the interface, which passes
everything on to RAMSES. Start it in a third terminal and leave it running. It keeps working when RAMSES is reset.

```bash
source .venv/bin/activate      # use this repo's Python environment (step 1)
cd interface
python api.py
```

## 6. Run the baseline

Then run the baseline on each scenario in a fourth terminal.

```bash
source .venv/bin/activate      # use this repo's Python environment (step 1)
cd UPISAS
python run_ramses.py --scenario 1      # and the same with --scenario 2 and --scenario 3
python compare.py results/*.csv ../baseline/*.csv
```

`run_ramses.py` starts the scenario, runs the strategy every 5 seconds until the scenario ends, and saves a CSV
in `UPISAS/results/`. `compare.py` shows response time, availability, how often RAMSES's goals were met, the
number of instances and the number of adaptations for each run.

## Your own strategy

Copy the baseline (or start from scratch), rename the class, and run it the same way.

```bash
cp UPISAS/UPISAS/strategies/ramses_strategy.py UPISAS/UPISAS/strategies/my_strategy.py
python run_ramses.py --scenario 2 --strategy UPISAS.strategies.my_strategy:MyStrategy
```

A strategy extends `UPISAS.strategy.Strategy` and implements `analyze()` and `plan()`. It can change SEFA in four
ways.

- `addInstances` starts new instances of a service
- `removeInstance` stops one instance
- `changeLBWeights` changes how requests are split between the instances of a service
- `changeProperty` changes a setting in the config repo, for example a circuit breaker value

The parameters of each one are at http://127.0.0.1:50000/adaptation_options while the interface is running, and
the baseline shows how to call them. Compare your strategy with the baseline on all three scenarios, with a fresh
RAMSES and at least 3 runs per scenario (see the [rules](scenarios/README.md#rules-for-a-fair-comparison)).

## Common issues

| What you see | What to do |
|---|---|
| Anything in the setup | Run `./check_setup.sh` first. It says what is missing. |
| `reset_ramses.sh` says some containers are not running | Look at `/tmp/ramses_run_all.log` and `docker logs <name>`, then run `reset_ramses.sh` again. |
| Downloads hang or everything is very slow | Quit Docker Desktop completely (not just Restart) and open it again. |
| Services crash near the end of a run | Give Docker 10 GB memory. |
| `run_ramses.py` says "Interface not reachable" | Start the interface (step 5). |
