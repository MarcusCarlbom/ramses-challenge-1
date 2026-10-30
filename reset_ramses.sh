#!/bin/bash
# Starts a fresh RAMSES (README, step 4). Use it for the first start and before every new run.
#   1. It removes the RAMSES and SEFA containers and their network.
#   2. It deletes leftover load balancer weights from your config repo, with the URL and token in your RUN_ALL.sh.
#   3. It runs RUN_ALL.sh. It answers the architecture question and presses Enter at the scenario question.
#   Then it waits until all 18 containers run, and starts ramses-knowledge again if it failed to start.
# Keep socat running in another terminal. The interface can keep running too.
#
# Usage, with the full path of your own RUN_ALL.sh (or RUN_ALL_LINUX.sh), for example
#   ./reset_ramses.sh ~/Downloads/SEAMS24_artifact_package/bash_scripts/execute/RUN_ALL.sh
# Add arm64 or amd64 at the end to choose the architecture yourself. By default it is detected.
#
# This script only checks that each reset step works. To check your whole setup, run check_setup.sh.
Green() { echo -e "\033[0;32m$1\033[0m"; }
Red() { echo -e "\033[0;31m$1\033[0m"; }
Stop() {
  Red "Reset failed: $1"
  shift
  for line in "$@"; do echo "  $line"; done
  exit 1
}

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_ALL=$1
ARCH=${2:-$( [[ "$(uname -m)" =~ ^(arm64|aarch64)$ ]] && echo arm64 || echo amd64 )}

if [ ! -f "$RUN_ALL" ]; then
  Stop "no RUN_ALL.sh found at \"$RUN_ALL\"." \
       "Give the full path of your RUN_ALL.sh, for example" \
       "  $0 ~/Downloads/SEAMS24_artifact_package/bash_scripts/execute/RUN_ALL.sh" \
       "To find out what is wrong with your setup, run check_setup.sh with that same path, for example" \
       "  $REPO_DIR/check_setup.sh ~/Downloads/SEAMS24_artifact_package/bash_scripts/execute/RUN_ALL.sh"
fi
RUN_ALL=$(cd "$(dirname "$RUN_ALL")" && pwd)/$(basename "$RUN_ALL")
URL=$(grep -m1 '^export GITHUB_REPOSITORY_URL=' "$RUN_ALL" | cut -d= -f2- | tr -d "\"' ")
TOKEN=$(grep -m1 '^export GITHUB_OAUTH=' "$RUN_ALL" | cut -d= -f2- | tr -d "\"' ")

EXPECTED="mysql sefa-eureka sefa-configserver sefa-restaurant-service sefa-ordering-service
sefa-payment-proxy-1-service sefa-delivery-proxy-1-service sefa-web-service sefa-api-gateway sefa-probe
sefa-instances-manager sefa-config-manager ramses-knowledge ramses-analyse ramses-execute ramses-monitor
ramses-dashboard ramses-plan"
status() { docker inspect -f '{{.State.Status}}' "$1" 2>/dev/null; }

# 1. Containers. Everything on the RAMSES network, plus scenario containers and instances added during the run.
echo "1/4 Removing RAMSES containers"
docker info > /dev/null 2>&1 || Stop "Docker is not running." "Start Docker Desktop and run this script again."
containers="$(docker ps -aq --filter network=ramses-sas-net) $(docker ps -aq --filter name=simulation-scenario)"
[ -n "${containers// /}" ] && docker rm -f $containers > /dev/null
docker network rm ramses-sas-net > /dev/null 2>&1

# 2. Config repo. The token is only used for a temporary copy of the repo, which is deleted at the end.
echo "2/4 Removing load balancer weights from the config repo"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
git clone -q "https://x-access-token:${TOKEN}@${URL#https://}" "$WORK/repo" 2> /dev/null \
  || Stop "could not read the config repo in RUN_ALL.sh (lines 7 and 8)." "Run check_setup.sh to see what is wrong."
cd "$WORK/repo"
sed -i.bak '/^loadbalancing\..*\.weight=/d' application.properties && rm -f application.properties.bak
if git diff --quiet; then
  echo "    already clean"
else
  git -c user.name=reset_ramses -c user.email=reset@localhost commit -qam "Remove load balancer weights before a new run"
  git push -q origin HEAD 2> /dev/null \
    || Stop "could not write to the config repo." "Run check_setup.sh to see what is wrong, then run this script again."
  echo "    removed and pushed"
fi
cd - > /dev/null

# 3. RAMSES.
LOG=/tmp/ramses_run_all.log
echo "3/4 Starting RAMSES ($ARCH). This takes a few minutes. Its output goes to $LOG"
(cd "$(dirname "$RUN_ALL")" && printf '%s\n\n' "$ARCH" | bash "$RUN_ALL" > "$LOG" 2>&1)

# 4. Wait for ramses-knowledge, and start it again if it failed (README, Common issues).
echo "4/4 Waiting until all containers run"
for attempt in 1 2 3; do
  for i in $(seq 1 24); do
    docker logs ramses-knowledge 2>&1 | grep -q "Knowledge initialized" && break
    [ "$(status ramses-knowledge)" = running ] || break
    sleep 5
  done
  if docker logs ramses-knowledge 2>&1 | grep -q "Knowledge initialized" && [ "$(status ramses-knowledge)" = running ]; then
    break
  fi
  echo "    ramses-knowledge did not start, starting it again ($attempt of 3)"
  docker start ramses-knowledge > /dev/null
done
for c in ramses-analyse ramses-execute ramses-monitor ramses-dashboard ramses-plan; do
  [ "$(status $c)" = running ] || docker start $c > /dev/null
done
sleep 10

missing=""
for c in $EXPECTED; do
  [ "$(status $c)" = running ] || missing="$missing $c"
done
[ -z "$missing" ] || Stop "these containers are not running:$missing" \
  "Look at the output of RUN_ALL.sh in $LOG, and at  docker logs <container name>" \
  "The README's Common issues table lists the usual causes. check_setup.sh also helps."

Green "RAMSES is running (18 containers)."
echo "If the interface is not running yet, start it (README, step 5)."
