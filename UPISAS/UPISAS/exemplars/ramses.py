import logging
import platform

import docker

from UPISAS.exemplar import Exemplar

logging.getLogger().setLevel(logging.INFO)

# The three scenarios (see ../../../scenarios/README.md). Each one uses a scenario image that comes with RAMSES.
#   image        number of the RAMSES scenario image (giamburrasca/scenario<image>)
#   minutes      length of the short version
#   short        start times of the problems in the short version, in seconds after the scenario starts
#   original     start times in RAMSES's own 10-minute version
# The start times are settings of the scenario. Environment variables replace the values inside the image, so the
# images don't have to be rebuilt.
SCENARIOS = {
    1: {"image": 1, "minutes": 4,
        "short": {"FAILURE_INJECTION_1_START": 60, "FAILURE_INJECTION_2_START": 180},
        "original": {}},
    2: {"image": 3, "minutes": 4,
        "short": {"FAKE_SLOW_ORDERING_1_START": 60, "FAKE_SLOW_ORDERING_2_START": 150},
        "original": {}},
    3: {"image": 4, "minutes": 6,
        "short": {"FAKE_SLOW_ORDERING_1_START": 60, "FAKE_SLOW_ORDERING_2_START": 150,
                  "FAKE_EXCEPTION_START_1": 200, "FAKE_EXCEPTION_START_2": 300},
        "original": {}},
}
ORIGINAL_MINUTES = 10


class RAMSES(Exemplar):
    """
    RAMSES and SEFA, which you start yourself with RUN_ALL.sh.

    The container this exemplar starts and stops is the scenario. It sends the user requests and causes the problems.
    With ADAPT=0 RAMSES only watches. It keeps monitoring (the dashboard still works), and the UPISAS strategy is the
    only thing that adapts. UPISAS talks to RAMSES through the interface in ../interface/api.py.
    """
    def __init__(self, scenario=3, minutes=None, auto_start=False, base_endpoint="http://localhost:50000",
                 ramses_adapts=False, original_timing=False):
        """minutes is the length of the run. By default it is the length of the chosen scenario."""
        settings = SCENARIOS[scenario]
        self.minutes = minutes or (ORIGINAL_MINUTES if original_timing else settings["minutes"])
        start_times = settings["original"] if original_timing else settings["short"]
        arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "amd64"
        image = f"giamburrasca/scenario{settings['image']}:{arch}"
        container_name = f"simulation-scenario-{scenario}"
        client = docker.from_env()
        # A container left over from an earlier run would make create() fail, because the name is taken.
        try:
            client.containers.get(container_name).remove(force=True)
            logging.info(f"removed old container '{container_name}'")
        except docker.errors.NotFound:
            pass
        # UPISAS's Exemplar only downloads an image if it is on the first page of a Docker Hub search for its owner.
        # The scenario images are not on that page, so they are downloaded here.
        try:
            client.images.get(image)
        except docker.errors.ImageNotFound:
            logging.info(f"pulling {image}")
            client.images.pull(image)

        docker_config = {
            "name": container_name,
            "image": image,
            "network": "ramses-sas-net",
            # ADAPT=1 (ramses_adapts) lets RAMSES itself adapt instead, to measure RAMSES.
            # These images ignore TRIAL_DURATION_MINUTES, so run_ramses.py stops the container at the end of the run.
            "environment": {"ADAPT": "1" if ramses_adapts else "0",
                            "TRIAL_DURATION_MINUTES": str(int(self.minutes) + 1),
                            **{k: str(v) for k, v in start_times.items()}},
        }
        super().__init__(base_endpoint, docker_config, auto_start)

    def start_run(self):
        self.start_container()
