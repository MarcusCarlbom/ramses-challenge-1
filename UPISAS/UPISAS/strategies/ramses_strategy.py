"""
The baseline strategy. It is RAMSES's own adaptation logic, rewritten as a UPISAS strategy.

The Java code it comes from is in managing-system/ of the RAMSES download.
  analyze()  comes from analyse/.../AnalyseService.java
  plan()     comes from plan/.../PlanService.java, including the OR-Tools calculation of load balancer weights
  execute()  comes from execute/.../ExecuteService.java, but it calls the interface instead of the actuators
  the classes at the top come from the parts of knowledge/... that the three above use
Comments name the Java method each part comes from, so you can compare the two side by side.

The settings are RAMSES's defaults (from the analyse and monitor application.properties). The goals, benchmarks
and list of services are the files the ramses-knowledge image loads, copied into ramses_specs/.
"""
import json
import logging
import os
import time

import requests
from ortools.linear_solver import pywraplp

from UPISAS import validate_schema
from UPISAS.exceptions import EndpointNotReachable
from UPISAS.strategy import Strategy

AVAILABILITY = "availability"
ART = "average_response_time"
QOS = (AVAILABILITY, ART)

BOOTING, ACTIVE, UNREACHABLE, FAILED, SHUTDOWN = "BOOTING", "ACTIVE", "UNREACHABLE", "FAILED", "SHUTDOWN"

SPEC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ramses_specs")


def load_spec(name):
    with open(os.path.join(SPEC_DIR, name)) as spec_file:
        return json.load(spec_file)["services"]


def summarize_http_metrics(snapshot):
    """Returns, for each endpoint, (total count, successful count, successful duration in ms).
    Like RAMSES, a request counts as successful when its status is below 500
    (HttpEndpointMetrics.getTotalCountOfSuccessful and getTotalDurationOfSuccessful)."""
    summary = {}
    for endpoint, metrics in (snapshot.get("httpMetrics") or {}).items():
        total = successful = duration = 0
        for outcome in (metrics.get("outcomeMetrics") or {}).values():
            total += outcome["count"]
            if outcome["status"] < 500:
                successful += outcome["count"]
                duration += outcome["totalDuration"]
        summary[endpoint] = (total, successful, duration)
    return summary


class QoSHistory:
    """knowledge/.../adaptation/values/QoSHistory.java"""

    def __init__(self, current_value):
        self.values = []  # newest first, each [value, invalidates_this_and_previous_values]
        self.current_value = current_value

    def add_value(self, value):
        self.values.insert(0, [value, False])

    def latest_value(self):
        return self.values[0][0] if self.values else None

    def latest_analysis_window(self, size):
        """The latest `size` valid values, or None if there are fewer."""
        if len(self.values) < size:
            return None
        window = []
        for value, invalid in self.values[:size]:
            if invalid:
                break
            window.append(value)
        return window if len(window) == size else None

    def latest_filled_analysis_window(self, size):
        """Like latest_analysis_window, but missing values are filled with the current value."""
        window = []
        for value, invalid in self.values[:size]:
            if invalid:
                break
            window.append(value)
        return window + [self.current_value] * (size - len(window))

    def invalidate(self):
        if self.values:
            self.values[0][1] = True


class Instance:
    """knowledge/.../architecture/Instance.java"""

    def __init__(self, instance_id, benchmarks):
        self.instance_id = instance_id
        self.status = BOOTING
        self.qos = {q: QoSHistory(benchmarks[q]) for q in QOS}
        self.latest_snapshot = None
        self.latest_snapshot_time = time.time()
        # Snapshots received after the instance's latest QoS value, newest first
        # (KnowledgeService.getLatestNMetricsOfCurrentInstance).
        self.metrics = []

    def latest_value(self, q):
        value = self.qos[q].latest_value()
        return self.qos[q].current_value if value is None else value

    def add_snapshot(self, status, http):
        snapshot = {"status": status, "http": http}
        # InstanceMetricsSnapshot.equals: an identical snapshot is not stored again, an UNREACHABLE one always is.
        if self.latest_snapshot == snapshot and status != UNREACHABLE:
            return
        self.latest_snapshot = snapshot
        self.latest_snapshot_time = time.time()
        self.status = status
        self.metrics.insert(0, snapshot)

    @property
    def address_and_port(self):
        address, port = self.instance_id.split("@")[1].split(":")
        return address, int(port)


class Implementation:
    """knowledge/.../architecture/ServiceImplementation.java"""

    def __init__(self, spec, benchmarks):
        self.implementation_id = spec["implementation_id"]
        self.trust = spec["implementation_trust"]
        self.preference = spec["preference"]
        self.instance_load_shutdown_threshold = spec["instance_load_shutdown_threshold"]
        self.penalty = 0
        self.benchmarks = benchmarks
        self.qos = {q: QoSHistory(benchmarks[q]) for q in QOS}
        self.instances = {}


class Service:
    """knowledge/.../architecture/Service.java"""

    def __init__(self, architecture, qos_specs, benchmarks):
        self.service_id = architecture["service_id"]
        self.dependencies = [d["name"] for d in architecture["dependencies"]]
        self.implementations = {
            impl["implementation_id"]: Implementation(impl, benchmarks[impl["implementation_id"]])
            for impl in architecture["implementations"]}
        self.current_implementation_id = architecture["implementations"][0]["implementation_id"]
        # For each goal, its threshold and its weight.
        self.specs = {AVAILABILITY: (qos_specs[AVAILABILITY]["min_threshold"], qos_specs[AVAILABILITY]["weight"]),
                      ART: (qos_specs[ART]["max_threshold"], qos_specs[ART]["weight"])}
        # The load balancer weight of each instance. SEFA's load balancer type is WEIGHTED_RANDOM.
        self.lb_weights = {}

    @property
    def implementation(self):
        return self.implementations[self.current_implementation_id]

    @property
    def instances(self):
        return self.implementation.instances

    def is_satisfied(self, q, value):
        """Availability.isSatisfied / AverageResponseTime.isSatisfied"""
        threshold = self.specs[q][0]
        return value >= threshold if q == AVAILABILITY else value <= threshold

    def is_satisfied_at_rate(self, q, values, rate):
        """QoSSpecification.isSatisfied(List, percentage)"""
        if not values:
            return False
        return sum(1 for v in values if self.is_satisfied(q, v)) / len(values) >= rate

    def latest_analysis_window(self, q, size):
        return self.implementation.qos[q].latest_analysis_window(size)

    def current_value(self, q):
        return self.implementation.qos[q].current_value

    def is_in_transition_state(self):
        return any(i.status in (BOOTING, SHUTDOWN) for i in self.instances.values())

    def should_consider_changing_implementation(self):
        impl = self.implementation
        return len(self.implementations) > 1 and impl.trust - impl.penalty <= 0

    def create_instance(self, address_and_port):
        instance_id = f"{self.current_implementation_id}@{address_and_port}"
        instance = Instance(instance_id, self.implementation.benchmarks)
        self.instances[instance_id] = instance
        return instance

    def invalidate_qos_history(self):
        """KnowledgeService.invalidateQosHistory"""
        for instance in self.instances.values():
            for q in QOS:
                instance.qos[q].invalidate()
        for q in QOS:
            self.implementation.qos[q].invalidate()


def option(kind, service, goal=None, forced=False, description="", **fields):
    """An adaptation option (knowledge/.../adaptation/options/*Option.java) as a dict."""
    return {"type": kind, "serviceId": service.service_id, "implementationId": service.current_implementation_id,
            "goal": goal, "forced": forced, "description": description, **fields}


class RamsesStrategy(Strategy):
    # analyse/src/main/resources/application.properties
    ANALYSIS_WINDOW_SIZE = 5
    METRICS_WINDOW_SIZE = 4
    FAILURE_RATE_THRESHOLD = 0.1
    UNREACHABLE_RATE_THRESHOLD = 0.35
    QOS_SATISFACTION_RATE = 0.6
    MAX_BOOT_TIME_SECONDS = 120
    # monitor/src/main/resources/application.properties: one MAPE-K iteration every 5 s
    LOOP_PERIOD_SECONDS = 5

    def __init__(self, exemplar):
        super().__init__(exemplar)
        qos_specs = {s["service_id"]: {q["name"]: q for q in s["qos"]} for s in load_spec("qos_specification.json")}
        benchmarks = {impl["implementation_id"]: {b["name"]: b["benchmark"] for b in impl["adaptation_benchmarks"]}
                      for s in load_spec("system_benchmarks.json") for impl in s["implementations"]}
        self.services = {a["service_id"]: Service(a, qos_specs[a["service_id"]], benchmarks)
                         for a in load_spec("system_architecture.json")}
        self.initialized = False
        self.previously_active_instances = set()
        self.log = logging.getLogger("RamsesStrategy")

    # ------------------------------------------------------------------ Knowledge (process the latest /monitor data)

    def update_knowledge(self, monitored):
        """KnowledgeInit (first call) and KnowledgeService.addMetricsFromBuffer (every call)."""
        probe_services = monitored["services"]
        if not self.initialized:
            for service in self.services.values():
                data = probe_services.get(service.service_id)
                if not data:
                    continue
                service.current_implementation_id = data["currentImplementationId"]
                for instance_id in data["instances"]:
                    service.create_instance(instance_id.split("@")[1])
                # Like KnowledgeInit, use equal weights when the config has none.
                weights = (data.get("instanceConfig") or {}).get("loadBalancerWeights")
                service.lb_weights = dict(weights) if weights else {
                    i: 1.0 / len(service.instances) for i in service.instances}
            self.initialized = True

        currently_active = set()
        still_monitored_shutdown = set()
        for service in self.services.values():
            data = probe_services.get(service.service_id) or {}
            for snapshot in data.get("snapshot") or []:
                instance_id = snapshot["instanceId"]
                if instance_id.split("@")[0] != service.current_implementation_id:
                    continue  # not the current implementation
                instance = service.instances.get(instance_id)
                if instance is None:
                    # RAMSES stops with an error here, because it only expects instances it started itself.
                    # The port adds the unknown instance instead.
                    self.log.warning(f"{service.service_id}: unknown instance {instance_id}, adding it")
                    instance = service.create_instance(instance_id.split("@")[1])
                if instance.status == SHUTDOWN:
                    still_monitored_shutdown.add(instance_id)
                    continue
                instance.add_snapshot(snapshot["status"], summarize_http_metrics(snapshot))
                if snapshot["status"] in (ACTIVE, UNREACHABLE):
                    currently_active.add(instance_id)

        # An instance that was active before and is not reported anymore has failed.
        for service in self.services.values():
            for instance_id, instance in service.instances.items():
                if instance_id in self.previously_active_instances and instance_id not in currently_active \
                        and instance.status != SHUTDOWN:
                    instance.add_snapshot(FAILED, {})
        self.previously_active_instances = currently_active

        # Shutdown instances that are not reported anymore are removed.
        for service in self.services.values():
            for instance_id in [i for i, inst in service.instances.items()
                                if inst.status == SHUTDOWN and i not in still_monitored_shutdown]:
                del service.instances[instance_id]

    # ------------------------------------------------------------------ Analyse

    def analyze(self):
        """AnalyseService.startAnalysis. Returns True if any adaptation option was proposed."""
        self.update_knowledge({"services": self.knowledge.monitored_data["services"][-1]})
        self.services_to_skip = set()
        self.forced_options = {s: [] for s in self.services}
        self.proposed_options = {}
        self._analyse_services()
        self._adapt()
        for service_id, forced in self.forced_options.items():
            if forced:
                self.proposed_options.setdefault(service_id, []).extend(forced)
        # KnowledgeService.proposeAdaptationOptions: every proposal costs the implementation one trust point.
        for service_id, options in self.proposed_options.items():
            if options:
                self.services[service_id].implementation.penalty += 1
        for options in self.proposed_options.values():
            for opt in options:
                self.log.info(f"{'FORCED' if opt['forced'] else 'PROPOSED'}: {opt['serviceId']} {opt['type']} "
                              f"({opt['goal'] or '-'}) {opt['description']}")
        self.knowledge.analysis_data["proposed_options"] = self.proposed_options
        return any(self.proposed_options.values())

    def _analyse_services(self):
        """AnalyseService.analyse"""
        now = time.time()
        for service in self.services.values():
            sid = service.service_id
            exists_instance_with_new_values = False
            at_least_one_booting = False
            instances_stats = []  # (instance, art, availability, from_new_data)
            for instance in list(service.instances.values()):
                if instance.status == SHUTDOWN:
                    self.services_to_skip.add(sid)
                    continue
                if instance.status == BOOTING:
                    if now - instance.latest_snapshot_time > self.MAX_BOOT_TIME_SECONDS:
                        self.forced_options[sid].append(option(
                            "ShutdownInstance", service, forced=True, description="Instance boot timed out",
                            instanceToShutdownId=instance.instance_id))
                    else:
                        at_least_one_booting = True
                    self.services_to_skip.add(sid)
                    continue
                if instance.status == FAILED:
                    self.forced_options[sid].append(option(
                        "ShutdownInstance", service, forced=True, description="Instance failed",
                        instanceToShutdownId=instance.instance_id))
                    self.services_to_skip.add(sid)
                    continue

                metrics = instance.metrics[:self.METRICS_WINDOW_SIZE]
                if len(metrics) != self.METRICS_WINDOW_SIZE:
                    instances_stats.append((instance, instance.latest_value(ART),
                                            instance.latest_value(AVAILABILITY), False))
                    continue

                failure_rate = sum(m["status"] == FAILED for m in metrics) / len(metrics)
                unreachable_rate = sum(m["status"] == UNREACHABLE for m in metrics) / len(metrics)
                if unreachable_rate >= self.UNREACHABLE_RATE_THRESHOLD or failure_rate >= self.FAILURE_RATE_THRESHOLD \
                        or failure_rate + unreachable_rate >= 1:
                    self.forced_options[sid].append(option(
                        "ShutdownInstance", service, forced=True, description="Instance failed or unreachable",
                        instanceToShutdownId=instance.instance_id))
                    self.services_to_skip.add(sid)
                    continue

                active = [m for m in metrics if m["status"] == ACTIVE and m["http"]]
                if not active:
                    instances_stats.append((instance, instance.latest_value(ART),
                                            instance.latest_value(AVAILABILITY), False))
                    continue
                oldest, latest = active[-1], active[0]
                instances_stats.append((instance, self._instance_art(instance, oldest, latest),
                                        self._instance_availability(instance, oldest, latest), True))
                exists_instance_with_new_values = True

            if not instances_stats and not at_least_one_booting:
                self.forced_options[sid].append(option(
                    "AddInstance", service, forced=True, description="No instances available"))
                self.services_to_skip.add(sid)
                continue
            if not exists_instance_with_new_values:
                continue
            self._update_qos_history(service, instances_stats, sid in self.services_to_skip)

    @staticmethod
    def _instance_art(instance, oldest, latest):
        """AnalyseService.computeInstanceAvgResponseTime"""
        duration = count = 0
        for endpoint, (_, successful, total_duration) in latest["http"].items():
            old = oldest["http"].get(endpoint, (0, 0, 0))
            duration += total_duration - old[2]
            count += successful - old[1]
        return duration / count if count else instance.qos[ART].current_value

    @staticmethod
    def _instance_availability(instance, oldest, latest):
        """AnalyseService.computeInstanceAvailability"""
        successful = total = 0
        for endpoint, (all_requests, ok, _) in latest["http"].items():
            old = oldest["http"].get(endpoint, (0, 0, 0))
            total += all_requests - old[0]
            successful += ok - old[1]
        return successful / total if total else instance.qos[AVAILABILITY].current_value

    def _update_qos_history(self, service, instances_stats, skip_service):
        """AnalyseService.updateQoSHistory"""
        service_values = {ART: 0.0, AVAILABILITY: 0.0}
        for instance, art, availability, from_new_data in instances_stats:
            if from_new_data:
                instance.qos[ART].add_value(art)
                instance.qos[AVAILABILITY].add_value(availability)
                # The next window starts with this window's newest snapshot. So RAMSES computes a new value every
                # METRICS_WINDOW_SIZE - 1 iterations, which is one value per 15 s (seen in the ramses-analyse log).
                instance.metrics = instance.metrics[:1]
            weight = service.lb_weights.get(instance.instance_id, 0.0)
            service_values[ART] += art * weight
            service_values[AVAILABILITY] += availability * weight
        if skip_service:
            return
        for q in QOS:
            service.implementation.qos[q].add_value(service_values[q])
        windows = {q: service.latest_analysis_window(q, self.ANALYSIS_WINDOW_SIZE) for q in QOS}
        if windows[ART] is None or windows[AVAILABILITY] is None:
            return
        for q in QOS:
            service.implementation.qos[q].current_value = sum(windows[q]) / len(windows[q])
            for instance in service.instances.values():
                window = instance.qos[q].latest_filled_analysis_window(self.ANALYSIS_WINDOW_SIZE)
                instance.qos[q].current_value = sum(window) / len(window)

    def _adapt(self):
        """AnalyseService.adapt"""
        requiring_or_completing = {}
        for service in self.services.values():
            self._compute_adaptation_options(service, requiring_or_completing)

    def _compute_adaptation_options(self, service, requiring_or_completing):
        """AnalyseService.computeAdaptationOptions (recursive over dependencies)"""
        sid = service.service_id
        if sid in requiring_or_completing:
            return requiring_or_completing[sid]
        requiring_or_completing[sid] = bool(self.forced_options[sid])
        if sid in self.services_to_skip:
            requiring_or_completing[sid] = True
            return True
        windows = {q: service.latest_analysis_window(q, self.ANALYSIS_WINDOW_SIZE) for q in QOS}
        if windows[ART] is None or windows[AVAILABILITY] is None:
            return requiring_or_completing[sid]

        proposed = []
        for q in (AVAILABILITY, ART):  # handleAvailabilityAnalysis, handleAverageResponseTimeAnalysis
            if service.is_satisfied_at_rate(q, windows[q], self.QOS_SATISFACTION_RATE):
                continue
            instances = list(service.instances.values())
            unsatisfying = [i for i in instances if not service.is_satisfied(q, i.qos[q].current_value)]
            if len(instances) > 1 and len(unsatisfying) < len(instances):
                proposed.append(option("ChangeLoadBalancerWeights", service, goal=q,
                                       description=f"At least one instance satisfies the {q} specification"))
            proposed.append(option("AddInstance", service, goal=q,
                                   description=f"The service {q} specification is not satisfied"))
        if service.should_consider_changing_implementation():
            for q in (AVAILABILITY, ART):
                proposed.append(option(
                    "ChangeImplementation", service, goal=q, description="Changing implementation",
                    numberOfInstances=len(service.instances),
                    possibleImplementations=[i for i in service.implementations
                                             if i != service.current_implementation_id]))

        requiring_or_completing[sid] = bool(proposed) or requiring_or_completing[sid]
        for dependency in service.dependencies:
            if self._compute_adaptation_options(self.services[dependency], requiring_or_completing):
                return requiring_or_completing[sid]  # first solve the dependency's problems
        if requiring_or_completing[sid] and proposed:
            self.proposed_options[sid] = proposed
        return requiring_or_completing[sid]

    # ------------------------------------------------------------------ Plan

    def plan(self):
        """PlanService.startPlan. Returns True if any option was chosen. The chosen options go in knowledge.plan_data."""
        chosen = {}
        for sid, options in self.proposed_options.items():
            service = self.services[sid]
            weights = dict(service.lb_weights)
            instance_ids = list(service.instances)
            forced = [o for o in options if o["forced"]]
            chosen_list = []
            if not forced:
                for opt in options:
                    if opt["type"] == "ChangeLoadBalancerWeights":
                        new_weights = self._handle_change_lb_weights(service)
                        if new_weights is not None:
                            to_shutdown = [i for i, w in new_weights.items() if w == 0.0]
                            opt["newWeights"] = {i: w for i, w in new_weights.items() if w != 0.0}
                            opt["instancesToShutdownIds"] = to_shutdown
                    elif opt["type"] == "AddInstance":
                        self._handle_add_instance(opt, service, weights, instance_ids)
                    elif opt["type"] == "ShutdownInstance":
                        self._handle_shutdown_instance(opt, weights, instance_ids, forced=False)
                    elif opt["type"] == "ChangeImplementation":
                        self._handle_change_implementation(opt, service)
                candidates = [o for o in options
                              if o["type"] != "ChangeLoadBalancerWeights" or o.get("newWeights") is not None]
                best = self._extract_best_option(service, candidates)
                if best is not None:
                    chosen_list.append(best)
            else:
                # Forced options. First all shutdowns, then at most one AddInstance. Execute keeps this order.
                for opt in [o for o in forced if o["type"] == "ShutdownInstance"]:
                    chosen_list.append(self._handle_shutdown_instance(opt, weights, instance_ids, forced=True))
                adds = [o for o in forced if o["type"] == "AddInstance"]
                if len(adds) > 1:
                    raise RuntimeError("More than one add instance option is forced")
                if adds:
                    chosen_list.append(self._handle_add_instance(adds[0], service, weights, instance_ids))
            if chosen_list:
                chosen[sid] = chosen_list

        processed = set()
        for sid, service in self.services.items():
            if service.is_in_transition_state() or sid in chosen:
                self._invalidate_service_and_dependants(sid, processed)

        for options in chosen.values():
            for opt in options:
                self.log.info(f"CHOSEN: {opt['serviceId']} {opt['type']} ({opt['goal'] or '-'}) {opt['description']}")
        self.knowledge.plan_data = {"chosen_options": chosen}
        return bool(chosen)

    def _invalidate_service_and_dependants(self, sid, processed):
        """PlanService.invalidateQoSHistoryOfServiceAndDependants"""
        if sid in processed:
            return
        processed.add(sid)
        self.services[sid].invalidate_qos_history()
        for other in self.services.values():
            if sid in other.dependencies:
                self._invalidate_service_and_dependants(other.service_id, processed)

    @staticmethod
    def _redistribute_weight(weights, to_remove):
        """PlanService.redistributeWeight"""
        removed = sum(w for i, w in weights.items() if i in to_remove)
        remaining = len(weights) - len(to_remove)
        share = removed / remaining if remaining else 0.0
        return {i: w + share for i, w in weights.items() if i not in to_remove}

    def _recursively_remove_instances_under_threshold(self, weights, threshold):
        """PlanService.recursivelyRemoveInstancesUnderThreshold"""
        to_remove = [i for i, w in weights.items() if w < threshold]
        if not to_remove:
            return weights
        new_threshold = threshold * len(weights) / (len(weights) - len(to_remove))
        return self._recursively_remove_instances_under_threshold(self._redistribute_weight(weights, to_remove),
                                                                  new_threshold)

    def _handle_shutdown_instance(self, opt, weights, instance_ids, forced):
        """PlanService.handleShutdownInstance. A forced shutdown also updates the plan's view of the service."""
        new_weights = self._redistribute_weight(weights, [opt["instanceToShutdownId"]])
        opt["newWeights"] = new_weights
        if forced:
            weights.clear()
            weights.update(new_weights)
            if opt["instanceToShutdownId"] in instance_ids:
                instance_ids.remove(opt["instanceToShutdownId"])
        return opt

    def _handle_add_instance(self, opt, service, weights, instance_ids):
        """PlanService.handleAddInstance"""
        shutdown_threshold = service.implementation.instance_load_shutdown_threshold / (len(instance_ids) + 1)
        old = len(weights)
        redistributed = {i: w * old / (old + 1) for i, w in weights.items()}  # reduceWeightsForNewInstance
        redistributed["NEWINSTANCE"] = 1 / (len(redistributed) + 1)
        redistributed = self._recursively_remove_instances_under_threshold(redistributed, shutdown_threshold)
        opt["newInstanceWeight"] = redistributed.pop("NEWINSTANCE", None)
        opt["oldInstancesNewWeights"] = redistributed
        opt["instancesToShutdownIds"] = [i for i in weights if i not in redistributed]
        return opt

    def _handle_change_lb_weights(self, service):
        """PlanService.handleChangeLoadBalancerWeights: min sum(w_i/z_i) - sum(a_i*z_i) with OR-Tools (SCIP)."""
        instances = list(service.instances.values())
        if not instances:
            return None
        previous = dict(service.lb_weights) or {i.instance_id: 1.0 / len(instances) for i in instances}
        shutdown_threshold = service.implementation.instance_load_shutdown_threshold / len(instances)
        k_s = service.current_value(AVAILABILITY) / service.current_value(ART)

        def k(instance):
            return instance.qos[AVAILABILITY].current_value / instance.qos[ART].current_value

        solver = pywraplp.Solver.CreateSolver("SCIP")
        weight, activation = {}, {}
        objective = solver.Objective()
        sum_of_weights = solver.Constraint(1.0, 1.0)
        for inst in instances:
            iid = inst.instance_id
            weight[iid] = solver.NumVar(0, 1, f"{iid}_weight")
            activation[iid] = solver.IntVar(0, 1, f"{iid}_activation")
            sum_of_weights.SetCoefficient(weight[iid], 1)
            lower = solver.Constraint(0, solver.infinity())  # w_i >= a_i * shutdownThreshold
            lower.SetCoefficient(weight[iid], 1)
            lower.SetCoefficient(activation[iid], -shutdown_threshold)
            upper = solver.Constraint(-solver.infinity(), 0)  # w_i <= a_i
            upper.SetCoefficient(weight[iid], 1)
            upper.SetCoefficient(activation[iid], -1)
            previous.setdefault(iid, 1.0 / len(instances))
            k_i = k(inst)
            if k_i != 0.0:
                z_i = k_i / k_s
                objective.SetCoefficient(weight[iid], 1 / z_i)
                objective.SetCoefficient(activation[iid], -z_i)
            else:
                zero = solver.Constraint(0, 0)
                zero.SetCoefficient(activation[iid], 1)

        for inst_i in instances:
            k_i = k(inst_i)
            if k_i == 0:
                continue
            z_i = k_i / k_s
            growth = solver.Constraint(-solver.infinity(), z_i)  # w_i + z_i * sum_j(P_j * a_j) <= z_i
            growth.SetCoefficient(weight[inst_i.instance_id], 1)
            for inst_j in instances:
                if inst_j is inst_i:
                    continue
                growth.SetCoefficient(activation[inst_j.instance_id], z_i * previous[inst_j.instance_id])
                k_j = k(inst_j)
                if k_i >= k_j:  # w_i - k_i/k_j * w_j + a_j <= 1
                    balancing = solver.Constraint(-solver.infinity(), 1)
                    balancing.SetCoefficient(weight[inst_i.instance_id], 1)
                    balancing.SetCoefficient(weight[inst_j.instance_id], -(k_i / k_j))
                    balancing.SetCoefficient(activation[inst_j.instance_id], 1)

        objective.SetMinimization()
        status = solver.Solve()
        if status not in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE, pywraplp.Solver.UNBOUNDED):
            self.log.error(f"{service.service_id}: no solution for new load balancer weights")
            return None
        return {iid: var.solution_value() for iid, var in weight.items()}

    @staticmethod
    def _handle_change_implementation(opt, service):
        """PlanService.handleChangeImplementation"""
        best_id, best = None, 0
        for impl_id in opt["possibleImplementations"]:
            impl = service.implementations[impl_id]
            benchmark = impl.benchmarks[opt["goal"]]
            if opt["goal"] == AVAILABILITY:
                benchmark *= impl.preference
                if best_id is None or benchmark > best:
                    best_id, best = impl_id, benchmark
            else:
                benchmark /= impl.preference
                if best_id is None or benchmark < best:
                    best_id, best = impl_id, benchmark
        opt["newImplementationId"] = best_id
        return opt

    @staticmethod
    def _extract_best_option(service, candidates):
        """PlanService.extractBestOption (WEIGHTED_RANDOM branch, the load balancer type SEFA uses)"""
        if not candidates:
            return None
        benefits, best_for_goal = {}, {}
        instances = list(service.instances.values())
        for opt in candidates:
            q = opt["goal"]
            if q is None:
                continue
            estimation = 0.0
            if opt["type"] == "ChangeLoadBalancerWeights":
                for inst in instances:
                    if inst.instance_id not in opt["instancesToShutdownIds"]:
                        estimation += opt["newWeights"].get(inst.instance_id, 0.0) * inst.qos[q].current_value
            elif opt["type"] == "AddInstance":
                for inst in instances:
                    if inst.instance_id not in opt["instancesToShutdownIds"]:
                        estimation += opt["oldInstancesNewWeights"].get(inst.instance_id, 0.0) * inst.qos[q].current_value
                estimation += opt["newInstanceWeight"] * service.implementation.benchmarks[q]
            elif opt["type"] == "ShutdownInstance":
                for inst in instances:
                    if inst.instance_id != opt["instanceToShutdownId"]:
                        estimation += opt["newWeights"].get(inst.instance_id, 0.0) * inst.qos[q].current_value
            elif opt["type"] == "ChangeImplementation":
                estimation = service.implementations[opt["newImplementationId"]].benchmarks[q]
            if q == AVAILABILITY:
                benefit = estimation / service.current_value(AVAILABILITY)
            else:  # Java's double division gives Infinity for an estimation of 0
                benefit = service.current_value(ART) / estimation if estimation else float("inf")
            if benefit > 1 and (q not in benefits or benefit > benefits[q]):
                benefits[q] = benefit
                best_for_goal[q] = opt
        best_goal = None
        for q in benefits:
            if best_goal is None or benefits[q] * service.specs[q][1] > benefits[best_goal] * service.specs[best_goal][1]:
                best_goal = q
        if best_goal is None:
            return None
        best_for_goal[best_goal]["benefit"] = benefits[best_goal]
        return best_for_goal[best_goal]

    # ------------------------------------------------------------------ Execute

    def execute(self, adaptation=None, endpoint_suffix="execute", with_validation=True):
        """ExecuteService. One RAMSES option can take several actuator calls (e.g. add an instance, then set
        weights that include it), so every call is sent to the interface's /execute separately, in RAMSES's order."""
        chosen = (adaptation or self.knowledge.plan_data)["chosen_options"]
        for sid, options in chosen.items():
            service = self.services[sid]
            for opt in options:
                try:
                    getattr(self, f"_execute_{opt['type']}")(service, opt, endpoint_suffix, with_validation)
                except (requests.RequestException, RuntimeError, KeyError) as e:
                    self.log.error(f"{sid}: executing {opt['type']} failed: {e}")
        return True

    def _send(self, operation, parameters, endpoint_suffix, with_validation):
        payload = {"operation": operation, "parameters": parameters}
        if with_validation:
            if not self.knowledge.execute_schema:
                self.get_execute_schema()
            validate_schema(payload, self.knowledge.execute_schema)
        response = requests.put('/'.join([self.exemplar.base_endpoint, endpoint_suffix]), json=payload)
        print(f"[Execute]\t{operation} {parameters} -> {response.status_code}")
        if response.status_code == 404:
            raise EndpointNotReachable
        if not response.ok:
            raise RuntimeError(response.text)
        return response.json()["result"]

    def _shutdown(self, service, instance_id, endpoint_suffix, with_validation):
        """ExecuteService.actuatorShutdownInstance + KnowledgeService.markInstanceAsShutdown"""
        address, port = instance_id.split("@")[1].split(":")
        try:
            self._send("removeInstance", {"serviceImplementationName": instance_id.split("@")[0],
                                          "address": address, "port": int(port)}, endpoint_suffix, with_validation)
        except RuntimeError as e:
            # For example, the container already crashed. RAMSES's execute would stop here. The port carries on.
            self.log.warning(f"removing {instance_id} failed: {e}")
        instance = service.instances.get(instance_id)
        if instance:
            instance.status = SHUTDOWN

    def _set_weights(self, service, new_weights, to_remove, endpoint_suffix, with_validation):
        self._send("changeLBWeights", {"serviceId": service.service_id, "newWeights": new_weights,
                                       "instancesToRemoveWeightOf": to_remove}, endpoint_suffix, with_validation)
        service.lb_weights = dict(new_weights or {})

    def _execute_AddInstance(self, service, opt, endpoint_suffix, with_validation):
        """ExecuteService.handleAddInstanceOption"""
        result = self._send("addInstances", {"serviceImplementationName": opt["implementationId"],
                                             "numberOfInstances": 1}, endpoint_suffix, with_validation)
        if not result.get("dockerizedInstances"):
            raise RuntimeError("No instances were added")
        new = result["dockerizedInstances"][0]
        new_instance_id = service.create_instance(f"{new['address']}:{new['port']}").instance_id
        if opt.get("newInstanceWeight") is not None:
            for instance_id in opt["instancesToShutdownIds"]:
                self._shutdown(service, instance_id, endpoint_suffix, with_validation)
            final_weights = dict(opt["oldInstancesNewWeights"])
            final_weights[new_instance_id] = opt["newInstanceWeight"]
            self._set_weights(service, final_weights, opt["instancesToShutdownIds"], endpoint_suffix, with_validation)

    def _execute_ShutdownInstance(self, service, opt, endpoint_suffix, with_validation):
        """ExecuteService.handleShutdownInstanceOption"""
        self._shutdown(service, opt["instanceToShutdownId"], endpoint_suffix, with_validation)
        if opt.get("newWeights") is not None:
            self._set_weights(service, opt["newWeights"], [opt["instanceToShutdownId"]], endpoint_suffix,
                              with_validation)

    def _execute_ChangeLoadBalancerWeights(self, service, opt, endpoint_suffix, with_validation):
        """ExecuteService.handleChangeLBWeightsOption"""
        for instance_id in opt["instancesToShutdownIds"]:
            self._shutdown(service, instance_id, endpoint_suffix, with_validation)
        self._set_weights(service, opt["newWeights"], opt["instancesToShutdownIds"], endpoint_suffix, with_validation)

    def _execute_ChangeImplementation(self, service, opt, endpoint_suffix, with_validation):
        """ExecuteService.handleChangeImplementationOption + KnowledgeService.changeServiceImplementation"""
        result = self._send("addInstances", {"serviceImplementationName": opt["newImplementationId"],
                                             "numberOfInstances": opt["numberOfInstances"]},
                            endpoint_suffix, with_validation)
        if not result.get("dockerizedInstances"):
            raise RuntimeError("No instances were added")
        old_ids = list(service.instances)
        for instance_id in old_ids:
            self._shutdown(service, instance_id, endpoint_suffix, with_validation)
        self._send("changeLBWeights", {"serviceId": service.service_id, "newWeights": None,
                                       "instancesToRemoveWeightOf": old_ids}, endpoint_suffix, with_validation)
        service.implementation.penalty = 0
        service.implementation.instances.clear()
        service.current_implementation_id = opt["newImplementationId"]
        for new in result["dockerizedInstances"]:
            service.create_instance(f"{new['address']}:{new['port']}")
        service.lb_weights = {i: 1.0 / len(service.instances) for i in service.instances}
