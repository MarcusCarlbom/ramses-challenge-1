#!/bin/bash
# Checks your whole setup and tells you what is missing. It changes nothing.
#
# Usage, with the full path of your own RUN_ALL.sh (or RUN_ALL_LINUX.sh), for example
#   ./check_setup.sh ~/Downloads/SEAMS24_artifact_package/bash_scripts/execute/RUN_ALL.sh
#
# At the end it prints one of these.
#   Missing: ...                     something in the setup is not done yet (each item says what to do)
#   Ready for a start                everything is set up and RAMSES is fresh, you can start a run
#   Start RAMSES                     everything is set up, but RAMSES is not running
#   Mid session                      RAMSES has been used, run reset_ramses.sh for a clean start
Green() { echo -e "\033[0;32m$1\033[0m"; }
Red() { echo -e "\033[0;31m$1\033[0m"; }
Yellow() { echo -e "\033[0;33m$1\033[0m"; }

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXAMPLE="$0 ~/Downloads/SEAMS24_artifact_package/bash_scripts/execute/RUN_ALL.sh"
RUN_ALL=$1
missing=()      # short names for the summary
ok() { echo "  $(Green OK)       $1"; }
bad() { echo "  $(Red MISSING)  $1"; echo "             $2"; missing+=("$3"); }

echo "Setup"

# ------------------------------------------------------------ RUN_ALL.sh and its three edits
if [ -z "$RUN_ALL" ]; then
  bad "RUN_ALL.sh not given" "Run this script with the full path of your RUN_ALL.sh, for example  $EXAMPLE" "path to RUN_ALL.sh"
elif [ -d "$RUN_ALL" ]; then
  bad "\"$RUN_ALL\" is a folder" "Give the file itself, for example  $0 $RUN_ALL/RUN_ALL.sh" "path to RUN_ALL.sh"
  RUN_ALL=""
elif [ ! -f "$RUN_ALL" ]; then
  bad "no file at \"$RUN_ALL\"" "Check where you unzipped RAMSES (README, step 2), for example  $EXAMPLE" "path to RUN_ALL.sh"
  RUN_ALL=""
elif [ "$(head -1 "$RUN_ALL")" != "#!/bin/bash" ] || ! grep -q '^PrintSuccess() {' "$RUN_ALL"; then
  bad "\"$RUN_ALL\" is not RAMSES's RUN_ALL.sh" "Use SEAMS24_artifact_package/bash_scripts/execute/RUN_ALL.sh (or RUN_ALL_LINUX.sh)" "path to RUN_ALL.sh"
  RUN_ALL=""
else
  ok "RUN_ALL.sh found"
fi

URL=""; TOKEN=""
if [ -f "$RUN_ALL" ]; then
  URL=$(grep -m1 '^export GITHUB_REPOSITORY_URL=' "$RUN_ALL" | cut -d= -f2- | tr -d "\"' ")
  TOKEN=$(grep -m1 '^export GITHUB_OAUTH=' "$RUN_ALL" | cut -d= -f2- | tr -d "\"' ")
  if [[ "$URL" != https://github.com/* ]] || [[ "$URL" == *YOUR_USERNAME* ]]; then
    bad "edit 1, config repo URL (line 7)" "Set it to your fork, like  export GITHUB_REPOSITORY_URL=https://github.com/your-name/config-server.git" "edit 1 (repo URL)"
    URL=""
  else
    ok "edit 1, config repo URL ($URL)"
  fi
  if [ -z "$TOKEN" ] || [ "$TOKEN" = "..." ] || [[ "$TOKEN" == *YOUR_TOKEN* ]]; then
    bad "edit 1, GitHub token (line 8)" "Put your token from README step 2 there, like  export GITHUB_OAUTH=github_pat_..." "edit 1 (token)"
    TOKEN=""
  else
    ok "edit 1, GitHub token"
  fi
  if grep -q -- '-p 55010:58020' "$RUN_ALL" && grep -q -- '-p 55011:58015' "$RUN_ALL"; then
    ok "edit 2, fixed ports for sefa-probe and sefa-instances-manager"
  else
    bad "edit 2, fixed ports for sefa-probe and sefa-instances-manager" "Do edit 2 from README step 3" "edit 2 (ports)"
  fi
  if grep -q -- '-p 55011:58015 .*-e ARCH=\$ARCH' "$RUN_ALL"; then
    ok "edit 2, -e ARCH=\$ARCH for sefa-instances-manager"
  else
    bad "edit 2, -e ARCH=\$ARCH for sefa-instances-manager" "Add -e ARCH=\$ARCH to its docker run line (README step 3, edit 2)" "edit 2 (ARCH)"
  fi
  if grep -q -- '-p 55012:58016' "$RUN_ALL"; then
    ok "edit 3, fixed port for sefa-config-manager"
  else
    bad "edit 3, fixed port for sefa-config-manager" "Do edit 3 from README step 3" "edit 3 (port)"
  fi
fi

# ------------------------------------------------------------ config repo
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
weights=0
if [ -n "$URL" ] && [ -n "$TOKEN" ]; then
  AUTH_URL="https://x-access-token:${TOKEN}@${URL#https://}"
  if git clone -q "$AUTH_URL" "$WORK/repo" 2> /dev/null && [ -f "$WORK/repo/application.properties" ]; then
    ok "config repo can be read"
    weights=$(grep -c '^loadbalancing\..*\.weight=' "$WORK/repo/application.properties")
    # A dry-run push asks GitHub for write access without changing anything.
    if (cd "$WORK/repo" && git push --dry-run -q origin HEAD > /dev/null 2>&1); then
      ok "token can write to the config repo"
    else
      bad "token cannot write to the config repo" "Give the token Contents → Read and write for this repo (README, step 2)" "token write access"
    fi
  else
    bad "config repo cannot be read ($URL)" "Check that ${URL%.git} opens in your browser, that the URL is exact, and that the token is complete and not expired" "config repo access"
  fi
fi

# ------------------------------------------------------------ Docker, socat, Python
if docker info > /dev/null 2>&1; then
  ok "Docker is running"
  # LC_ALL=C gives a decimal point whatever the computer's language is.
  mem_gb=$(docker info --format '{{.MemTotal}}' | LC_ALL=C awk '{printf "%.1f", $1/1024/1024/1024}')
  cpus=$(docker info --format '{{.NCPU}}')
  if LC_ALL=C awk -v m="$mem_gb" 'BEGIN {exit !(m >= 9.5)}'; then
    ok "Docker memory ${mem_gb} GB"
  else
    bad "Docker memory is ${mem_gb} GB" "Set it to 10 GB in Docker Desktop → Settings → Resources (README, step 1)" "Docker memory 10 GB"
  fi
  if [ "$cpus" -ge 8 ]; then
    ok "Docker CPUs $cpus"
  else
    bad "Docker has $cpus CPUs" "Set it to 8 in Docker Desktop → Settings → Resources (README, step 1)" "Docker 8 CPUs"
  fi
else
  bad "Docker is not running" "Start Docker Desktop and wait until it says it is running" "Docker running"
fi

if curl -s -m 3 http://localhost:2375/_ping > /dev/null; then
  ok "socat is running"
else
  bad "socat is not running" "Start it in its own terminal (README, step 4)  socat -d TCP-LISTEN:2375,bind=127.0.0.1,reuseaddr,fork UNIX:/var/run/docker.sock" "socat"
fi

PY="$REPO_DIR/.venv/bin/python"
[ -x "$PY" ] || PY=python3
if "$PY" -c "import flask, docker, jsonschema, rich, ortools" 2> /dev/null; then
  ok "Python packages installed"
else
  bad "Python packages not installed" "In this repo run  python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt" "Python packages"
fi

# ------------------------------------------------------------ summary of the setup
echo
if [ ${#missing[@]} -gt 0 ]; then
  Red "Missing: $(IFS=,; echo "${missing[*]}" | sed 's/,/, /g')"
  echo "Fix these first. Each MISSING line above says what to do."
  exit 1
fi

# ------------------------------------------------------------ state of RAMSES
echo "RAMSES"
EXPECTED="mysql sefa-eureka sefa-configserver sefa-restaurant-service sefa-ordering-service
sefa-payment-proxy-1-service sefa-delivery-proxy-1-service sefa-web-service sefa-api-gateway sefa-probe
sefa-instances-manager sefa-config-manager ramses-knowledge ramses-analyse ramses-execute ramses-monitor
ramses-dashboard ramses-plan"
running=0; not_running=""
for c in $EXPECTED; do
  if [ "$(docker inspect -f '{{.State.Status}}' $c 2>/dev/null)" = running ]; then
    running=$((running + 1))
  else
    not_running="$not_running $c"
  fi
done
# Instances added during a run are named like sefa-ordering-service-43499.
extra=$(docker ps -a --filter network=ramses-sas-net --format '{{.Names}}' | grep -E -- '^sefa-.*-[0-9]+$' | tr '\n' ' ')
scenarios=$(docker ps -a --filter name=simulation-scenario --format '{{.Names}}' | tr '\n' ' ')
interface=$(curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:50000/monitor_schema)

reasons=()
echo "  $running of 18 RAMSES containers running"
[ -n "$not_running" ] && [ "$running" -gt 0 ] && reasons+=("not running:$not_running")
[ -n "$extra" ] && reasons+=("extra instances from an earlier run: $extra")
[ -n "$scenarios" ] && reasons+=("scenario container left: $scenarios")
[ "$weights" -gt 0 ] && reasons+=("$weights load balancer weight lines in the config repo")
if [ "$interface" = 200 ]; then echo "  interface is running"; else echo "  interface is not running (README, step 5)"; fi

echo
if [ ${#reasons[@]} -gt 0 ]; then
  Yellow "Mid session, run reset_ramses.sh for a clean start."
  for r in "${reasons[@]}"; do echo "  - $r"; done
  echo "  ./reset_ramses.sh $RUN_ALL"
elif [ "$running" -eq 0 ]; then
  Yellow "Setup is complete. Start RAMSES with  ./reset_ramses.sh $RUN_ALL"
else
  Green "Ready for a start."
  [ "$interface" = 200 ] || echo "  Start the interface first (README, step 5)."
fi
