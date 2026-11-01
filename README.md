# Code Challenge #1 - RAMSES

## The challenge

RAMSES is a microservice system that simulates an e-food service. Your challenge is to make your own version of it through UPISAS that mimics RAMSES, or is even better.

Your version runs on [one scenario](https://github.com/MarcusCarlbom/ramses-challenge-1/tree/main/scenario) that lasts 4 minutes. At minute 2, an instance dies. Your goal is to beat the baseline, which does nothing when that happens.

Part 1 helps you get RAMSES running. Part 2 is the actual challenge.

## What you need

| Tools | Check it works |
|---|---|
| Docker Desktop | `docker compose version` |
| Python 3.9 or newer | `python3 --version` |
| socat | `socat -V` |

Install socat with `brew install socat` on macOS. On Linux or WSL, run `sudo apt-get install socat qemu-user-static`.

In Docker Desktop, go to Settings → Resources and set 8 CPUs, 10 GB memory and 1 GB swap. Under Settings → Advanced, turn on "Allow the default Docker socket to be used".

## Part 1 - Set up RAMSES

**1. Get RAMSES.** Download `SEAMS24_artifact_package.zip` from [Zenodo](https://doi.org/10.5281/zenodo.10400820) and unzip it.

**2. Get a config repo.** Fork [MarcusCarlbom/config-server](https://github.com/MarcusCarlbom/config-server) and keep the fork public.

**3. Create a token.** On GitHub, go to Settings → Developer settings → Fine-grained personal access tokens. Give the token access to only your fork, with Contents set to Read and write. Don't commit or share it.

**4. Edit the start script.** Open `SEAMS24_artifact_package/bash_scripts/execute/`. Edit `RUN_ALL.sh` on macOS, or `RUN_ALL_LINUX.sh` on Linux and on Windows with WSL.

**Both files, lines 7–8**

```bash
export GITHUB_REPOSITORY_URL=...
export GITHUB_OAUTH=...
```
becomes
```bash
export GITHUB_REPOSITORY_URL=https://github.com/YOUR_USERNAME/config-server.git
export GITHUB_OAUTH=github_pat_YOUR_TOKEN
```

**`RUN_ALL.sh`, lines 90–98**

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
becomes
```bash
docker run -p 55010:58020 --name sefa-probe -d --network ramses-sas-net giamburrasca/sefa-probe:$ARCH
sleep 1
docker run -p 55011:58015 --name sefa-instances-manager -e ARCH=$ARCH -d --network ramses-sas-net giamburrasca/sefa-instances-manager:$ARCH
sleep 1
```

**`RUN_ALL_LINUX.sh`, line 93**

```bash
docker run -P --name sefa-probe -d --network ramses-sas-net giamburrasca/sefa-probe:$ARCH
```
becomes
```bash
docker run -p 55010:58020 --name sefa-probe -d --network ramses-sas-net giamburrasca/sefa-probe:$ARCH
```

**`RUN_ALL_LINUX.sh`, line 100**

```bash
docker run -P --name sefa-instances-manager -d --network ramses-sas-net --add-host=host.docker.internal:host-gateway giamburrasca/sefa-instances-manager:$ARCH
```
becomes
```bash
docker run -p 55011:58015 --name sefa-instances-manager -e ARCH=$ARCH -d --network ramses-sas-net --add-host=host.docker.internal:host-gateway giamburrasca/sefa-instances-manager:$ARCH
```

**`RUN_ALL.sh` line 102, `RUN_ALL_LINUX.sh` line 106**

```bash
docker run -P --name sefa-config-manager -e GITHUB_OAUTH=$GITHUB_OAUTH -e GITHUB_REPOSITORY_URL=$GITHUB_REPOSITORY_URL -d --network ramses-sas-net giamburrasca/sefa-config-manager:$ARCH
```
becomes
```bash
docker run -p 55012:58016 --name sefa-config-manager -e GITHUB_OAUTH=$GITHUB_OAUTH -e GITHUB_REPOSITORY_URL=$GITHUB_REPOSITORY_URL -d --network ramses-sas-net giamburrasca/sefa-config-manager:$ARCH
```

**5. Start socat.** Run this in its own terminal and leave it running.

```bash
socat -d TCP-LISTEN:2375,bind=127.0.0.1,reuseaddr,fork UNIX:/var/run/docker.sock
```

**6. Start RAMSES.** In a second terminal, from the folder where you unzipped RAMSES, run this. Use `RUN_ALL_LINUX.sh` on Linux and WSL.

```bash
cd SEAMS24_artifact_package/bash_scripts/execute
bash RUN_ALL.sh
```

Type `arm64` (Apple Silicon) or `amd64` (Intel/AMD) when asked. When it asks which scenario to run, press Enter without choosing one. The first start downloads about 5 GB.

**7. Check that it runs.** After about a minute, this should print 18. If it prints less, start any stopped `ramses-` container again with `docker start <name>`.

```bash
docker ps --format '{{.Names}}' | wc -l
```

## Part 2 - Make your UPISAS

```bash
git clone https://github.com/S2-group/UPISAS
cd UPISAS
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt flask
```

In your UPISAS clone, you need three files of your own and one you can copy.

**`interface.py`** is a small web server on port 50000 that UPISAS talks to, with two endpoints.

- `GET /monitor` returns `GET localhost:55010/rest/systemArchitecture` and `GET localhost:55010/rest/service/<name>/snapshot` for each service.
- `PUT /execute` sends the adaptation to `POST localhost:55011/rest/addInstances`.

**`UPISAS/exemplars/ramses.py`** points UPISAS at `http://localhost:50000` and starts the scenario with these settings. Download the image first with `docker pull`, and use your own architecture in its name.

```python
docker_config = {
    "name": "simulation-scenario",
    "image": "giamburrasca/scenario1:arm64",
    "network": "ramses-sas-net",
    "environment": {
        "ADAPT": "0",
        "FAILURE_INJECTION_1_START": "120",
    },
}
```

**`UPISAS/strategies/my_strategy.py`** In `analyze()`, use the monitored data to detect that `RESTAURANT-SERVICE` has lost its instance. In `plan()`, decide what to do in the given scenario to beat the baseline strategy's availability.

**`run_challenge.py`** runs your strategy. Copy [`run_challenge.py`](https://github.com/MarcusCarlbom/ramses-challenge-1/blob/main/scenario/run_challenge.py), then run these in separate terminals.

```bash
python interface.py
python run_challenge.py
```

## What to submit

Commit your three files to your own repository and submit the commit link.

- `interface.py`
- `UPISAS/exemplars/ramses.py`
- `UPISAS/strategies/my_strategy.py`

## Evaluation

**Baseline.** The baseline is a UPISAS run with `ADAPT=0`, like your own code, and it does nothing when an instance dies.

**Setup.** We will perform one run of the baseline and one run of your solution, each starting from a fresh RAMSES.

**Adaptation objective.** Your solution must beat the baseline on availability. Availability is the share of the 4 minutes in which `ORDERING-SERVICE` is successfully handling requests.
