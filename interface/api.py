"""
HTTP interface between UPISAS and RAMSES/SEFA.

Based on https://github.com/Yimingzha0/RAMES-Interface. It was changed so that UPISAS works with it without any
changes to UPISAS. /execute accepts PUT, every JSON answer has exactly the top-level keys of its schema, and every
operation is passed on to the right SEFA actuator.

Endpoints
    GET  /monitor                    state of all services (raw probe data, see specifications/monitor_schema.json)
    GET  /monitor_schema
    GET  /adaptation_options         the operations /execute accepts
    GET  /adaptation_options_schema
    GET  /execute_schema
    PUT  /execute                    {"operation": "...", "parameters": {...}}  (POST also accepted)
"""
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests
from flask import Flask, Response, request

app = Flask(__name__)

# The fixed host ports from the RUN_ALL.sh edits (README, step 3).
PROBE_URL = os.environ.get("PROBE_URL", "http://localhost:55010")
INSTANCES_MANAGER_URL = os.environ.get("INSTANCES_MANAGER_URL", "http://localhost:55011")
CONFIG_MANAGER_URL = os.environ.get("CONFIG_MANAGER_URL", "http://localhost:55012")
INTERFACE_PORT = int(os.environ.get("INTERFACE_PORT", "50000"))
TIMEOUT_S = 30
CONFIG_CACHE_SECONDS = 60

# (service, implementation) -> (time fetched, configuration)
config_cache = {}

SPEC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "specifications")

# operation -> (actuator base URL, path, required parameters)
OPERATIONS = {
    "addInstances": (INSTANCES_MANAGER_URL, "/rest/addInstances", ["serviceImplementationName", "numberOfInstances"]),
    "removeInstance": (INSTANCES_MANAGER_URL, "/rest/removeInstance", ["serviceImplementationName", "address", "port"]),
    "changeLBWeights": (CONFIG_MANAGER_URL, "/rest/changeLBWeights", ["serviceId", "newWeights", "instancesToRemoveWeightOf"]),
    "changeProperty": (CONFIG_MANAGER_URL, "/rest/changeProperty", ["propertiesToChange"]),
}


def json_response(data, status=200):
    return Response(response=json.dumps(data), status=status, mimetype="application/json")


def load_spec(name):
    with open(os.path.join(SPEC_DIR, name), "r") as spec_file:
        return json.load(spec_file)


def probe_get(path, params=None):
    response = requests.get(PROBE_URL + path, params=params, timeout=TIMEOUT_S)
    response.raise_for_status()
    return response.json()


@app.route("/monitor", methods=["GET"])
def monitor():
    try:
        architecture = probe_get("/rest/systemArchitecture")
    except requests.exceptions.RequestException as e:
        return json_response({"error": f"probe not reachable at {PROBE_URL}: {e}"}, 503)

    def fetch(path, params=None):
        try:
            return probe_get(path, params)
        except requests.exceptions.RequestException as e:
            print(f"{path} failed: {e}")
            return None

    def configuration(name, implementation_id):
        # One configuration call takes about 2 s, because the config server reads GitHub every time. The probe also
        # handles these calls one at a time. So configurations are kept for a while, and /execute throws them away
        # whenever it changes the config repo.
        key = (name, implementation_id)
        cached = config_cache.get(key)
        if cached and time.monotonic() - cached[0] < CONFIG_CACHE_SECONDS:
            return cached[1]
        result = fetch(f"/rest/service/{name}/configuration", {"implementationId": implementation_id})
        if result is not None:
            config_cache[key] = (time.monotonic(), result)
        return result

    with ThreadPoolExecutor(max_workers=2 * max(1, len(architecture))) as pool:
        futures = {
            name: (pool.submit(fetch, f"/rest/service/{name}/snapshot"),
                   pool.submit(configuration, name, details["currentImplementationId"]))
            for name, details in architecture.items()}
    services = {}
    for service_name, details in architecture.items():
        snapshot, instance_config = futures[service_name]
        services[service_name] = {
            "serviceId": details["serviceId"],
            "currentImplementationId": details["currentImplementationId"],
            "instances": details["instances"],
            "snapshot": snapshot.result(),
            "instanceConfig": instance_config.result(),
        }

    return json_response({"timestamp": datetime.now(timezone.utc).isoformat(), "services": services})


@app.route("/monitor_schema", methods=["GET"])
def monitor_schema():
    return json_response(load_spec("monitor_schema.json"))


@app.route("/adaptation_options", methods=["GET"])
def adaptation_options():
    return json_response(load_spec("adaptation_options.json"))


@app.route("/adaptation_options_schema", methods=["GET"])
def adaptation_options_schema():
    return json_response(load_spec("adaptation_options_schema.json"))


@app.route("/execute_schema", methods=["GET"])
def execute_schema():
    return json_response(load_spec("execute_schema.json"))


# UPISAS's Strategy.execute() sends a PUT. POST still works, for people who used the original interface with curl.
@app.route("/execute", methods=["PUT", "POST"])
def execute():
    data = request.get_json(silent=True) or {}
    operation = data.get("operation")
    parameters = data.get("parameters") or {}

    if operation not in OPERATIONS:
        return json_response({"error": f"unknown operation {operation!r}, expected one of {list(OPERATIONS)}"}, 400)
    base_url, path, required = OPERATIONS[operation]
    missing = [p for p in required if p not in parameters]
    if missing:
        return json_response({"error": f"{operation} is missing parameters {missing}"}, 400)

    # The actuators take exactly these parameters as their request body.
    body = {p: parameters[p] for p in required}
    try:
        response = requests.post(base_url + path, json=body, timeout=TIMEOUT_S)
    except requests.exceptions.RequestException as e:
        return json_response({"error": f"actuator not reachable at {base_url}: {e}"}, 503)
    if base_url == CONFIG_MANAGER_URL:
        config_cache.clear()
    if not response.ok:
        return json_response({"error": f"actuator returned {response.status_code}", "body": response.text}, 502)

    # changeLBWeights and changeProperty return an empty body.
    result = response.json() if response.content else {}
    print(f"[execute] {operation} {body} -> {result}")
    return json_response({"operation": operation, "result": result})


if __name__ == "__main__":
    print(f"RAMSES interface available at http://127.0.0.1:{INTERFACE_PORT}/")
    print(f"probe: {PROBE_URL}  instances-manager: {INSTANCES_MANAGER_URL}  config-manager: {CONFIG_MANAGER_URL}")
    app.run(host="127.0.0.1", port=INTERFACE_PORT)
