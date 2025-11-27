from math import sin, pi
from random import Random
import re

from pythonfmu import (
    Fmi2Slave,
    Fmi2Causality,
    Fmi2Variability,
    Real,
    Integer,
)

# ----------------------------------------------------------------------
# Load AUT from full path
# ----------------------------------------------------------------------

AUT_FILE = r"/home/marziyeh/tinification/Tinification/FMU/temperature-with-hcunit-v6/RebecaCore.aut"

with open(AUT_FILE, "r", encoding="utf-8") as f:
    AUT_TEXT = f.read()



def parse_aut(aut_text: str):
    """
    Parse the AUT text into a controller specification dictionary.

    Structure:
        {
          "initial_state": int,
          "num_transitions": int,
          "num_states": int,
          "temperature_guards": {state: [{"op": ">"|"<", "threshold": float, "next": int}, ...], ...},
          "time_edges": {state: {"delay": float, "next": int}, ...},
          "heater_actions": {state: {"action": "...", "label": str, "next": int}, ...},
          "instant_transitions": {state: {"label": str, "next": int}, ...},
          "raw_transitions": [(src, simplified_label, dst), ...],
        }
    """
    lines = [l.strip() for l in aut_text.splitlines() if l.strip()]

    # header: des (init, num_transitions, num_states)
    m = re.match(r"des\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", lines[0])
    if not m:
        raise ValueError("Invalid des line in AUT")

    initial_state = int(m.group(1))
    num_transitions = int(m.group(2))
    num_states = int(m.group(3))

    temperature_guards = {}
    time_edges = {}
    heater_actions = {}
    instant_transitions = {}

    raw_triples = []
    simplified_raw = []

    # transitions
    for line in lines[1:]:
        m2 = re.match(r"\(\s*(\d+)\s*,\s*\"([^\"]+)\"\s*,\s*(\d+)\s*\)", line)
        if not m2:
            raise ValueError(f"Invalid transition line: {line}")

        src = int(m2.group(1))
        label = m2.group(2)
        dst = int(m2.group(3))

        raw_triples.append((src, label, dst))

        # room.tempchange guards
        if label.startswith("room.tempchange"):
            m_guard = re.match(r"room\.tempchange\[\s*([<>])\s*([\d\.]+)\s*\]", label)
            if not m_guard:
                raise ValueError(f"Cannot parse guard: {label}")
            op = m_guard.group(1)
            threshold = float(m_guard.group(2))
            temperature_guards.setdefault(src, []).append(
                {"op": op, "threshold": threshold, "next": dst}
            )

        # time edges: "time +=10"
        elif label.startswith("time +="):
            m_time = re.match(r"time\s*\+\=\s*([\d\.]+)", label)
            if not m_time:
                raise ValueError(f"Cannot parse time edge: {label}")
            delay = float(m_time.group(1))
            time_edges[src] = {"delay": delay, "next": dst}

        # heater actions
        elif label.startswith("hc_unit.activateh"):
            heater_actions[src] = {
                "action": "activate_heater",
                "label": label,
                "next": dst,
            }
        elif label.startswith("hc_unit.switchoff"):
            heater_actions[src] = {
                "action": "switch_off_heater",
                "label": label,
                "next": dst,
            }

        # instant transitions (controller.getsense)
        elif label.startswith("controller.getsense"):
            instant_transitions[src] = {
                "label": label,
                "next": dst,
            }

        # anything else: currently none

    # simplified raw_transitions: keep src/dst + only temp/time info
    for src, label, dst in raw_triples:
        simple_label = ""

        # temp guards -> "temp[> 21]" / "temp[< 20]"
        if label.startswith("room.tempchange"):
            m_guard = re.match(r"room\.tempchange\[\s*([<>])\s*([\d\.]+)\s*\]", label)
            if not m_guard:
                raise ValueError(f"Cannot parse guard: {label}")
            op = m_guard.group(1)
            threshold = float(m_guard.group(2))
            simple_label = f"temp[{op} {threshold}]"

        # time edges -> "elapsed time>=10"
        elif label.startswith("time +="):
            m_time = re.match(r"time\s*\+\=\s*([\d\.]+)", label)
            if not m_time:
                raise ValueError(f"Cannot parse time edge: {label}")
            delay = float(m_time.group(1))
            simple_label = f"elapsed time>={delay}"

        # everything else (heater actions, getsense) -> no condition text
        else:
            simple_label = ""

        simplified_raw.append((src, simple_label, dst))

    controller_spec = {
        "initial_state": initial_state,
        "num_transitions": num_transitions,
        "num_states": num_states,
        "temperature_guards": temperature_guards,
        "time_edges": time_edges,
        "heater_actions": heater_actions,
        "instant_transitions": instant_transitions,
        "raw_transitions": simplified_raw,
    }
 
    return controller_spec


class Temperature(Fmi2Slave):
    """
    Room temperature FMU with:
      - smooth base signal (day/night)
      - smooth stochastic variation (AR(1) noise)
      - first-order room dynamics (ODE) integrated with substeps
      - graph-based controller that decides heater ON/OFF

    The LTS controller is defined by AUT_TEXT above and parsed
    into self.controller_spec.
    """

    author = "Ali"
    description = "Stochastic room temperature with LTS-based heater controller"

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # ----- bounds for temperature -----
        self.T_min = 10.0
        self.T_max = 25.0
        self.T_mean = 0.5 * (self.T_min + self.T_max)   # 17.5
        self.deltaT = 0.5 * (self.T_max - self.T_min)   # 7.5

        # ----- base signal (environment temperature without heater) -----
        # single smooth daily sinusoid in [-1, 1]
        self.period1 = 24.0              # slow daily cycle (model time units)
        self.omega1 = 2.0 * pi / self.period1
        self.base_a1 = 1.0
        self.phi1 = pi / 2               # start near max

        # ----- smoothed random component for T_env(t) -----
        # AR(1) process: noise_k = alpha * noise_{k-1} + (1-alpha) * N(0,1)
        self.noise_level = 0.3           # strength of noise (0 = deterministic)
        self.smooth_factor = 0.95        # 0.95 => quite smooth, 0.99 => very smooth
        self.noise = 1.0                 # initial noise state

        # pseudo-random generator: NO fixed seed -> different each run
        self.rng = Random()              # use Random(12345) for reproducible runs

        # ----- room + heater dynamics -----
        self.tau = 20.0                  # room time constant
        self.heating_power = 0.3         # deg per time unit when heater ON

        # thermostat thresholds (for possible external use / tuning)
        self.T_on = 21.0                 # "low" threshold (not used directly here)
        self.T_off = 24.0                # "high" threshold (not used directly here)

        # heater state (output)
        self.heater = 0                  # 0 = off, 1 = on

        # parse the LTS controller specification from AUT_TEXT
        self.controller_spec = parse_aut(AUT_TEXT)

        # graph state (LTS) and its entry time
        self.graph_state = self.controller_spec["initial_state"]
        self.graph_state_enter_time = 0.0

        # internal integration step inside each do_step (for ODE)
        self.dt_internal = 0.1           # model time units per internal Euler substep

        # ----- internal time -----
        self.time = 0.0

        # initial environment temperature T_env(0)
        base0 = self.base_a1 * sin(self.omega1 * 0.0 + self.phi1)  # ~1.0
        s0 = base0 + self.noise_level * self.noise
        # clamp s0 to [-1, 1]
        s0 = max(-1.0, min(1.0, s0))
        self.T_env = self.T_mean + self.deltaT * s0

        # start room temp equal to environment
        self.temp = self.T_env

        # ----- expose FMU variables -----

        # main room temperature
        self.register_variable(
            Real(
                "temp",
                causality=Fmi2Causality.output,
                variability=Fmi2Variability.continuous,
                start=self.temp,
                description="Room temperature [°C]",
            )
        )

        # environment temp (for plotting / debugging)
        self.register_variable(
            Real(
                "T_env",
                causality=Fmi2Causality.output,
                variability=Fmi2Variability.continuous,
                start=self.T_env,
                description="Environment/base temperature [°C] (with noise, without heater)",
            )
        )

        # heater state as discrete integer 0/1
        self.register_variable(
            Integer(
                "heater",
                causality=Fmi2Causality.output,
                variability=Fmi2Variability.discrete,
                start=self.heater,
                description="Heater state from LTS controller (0 = OFF, 1 = ON)",
            )
        )

        # current graph state (for debugging / plotting)
        self.register_variable(
            Integer(
                "graph_state",
                causality=Fmi2Causality.output,
                variability=Fmi2Variability.discrete,
                start=self.graph_state,
                description="Current state of the LTS controller (0..9)",
            )
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _enter_state(self, new_state: int, t: float):
        """Enter a new graph state and remember entry time."""
        self.graph_state = new_state
        self.graph_state_enter_time = t

    def _compute_env_temperature(self, t: float) -> float:
        """Compute environment temperature T_env(t) with base + smooth stochastic noise."""
        # 1) base smooth signal (daily sinusoid), in [-1, 1]
        base = self.base_a1 * sin(self.omega1 * t + self.phi1)

        # 2) update smoothed noise: AR(1) driven by Gaussian
        xi = self.rng.gauss(0.0, 1.0)  # standard normal
        self.noise = self.smooth_factor * self.noise + (1.0 - self.smooth_factor) * xi

        # 3) combine base and noise
        s = base + self.noise_level * self.noise

        # 4) clamp s to [-1,  +1] to keep temp in [T_min, T_max]
        if s < -1.0:
            s = -1.0
        elif s > 1.0:
            s = 1.0

        # 5) map to [T_min, T_max]
        T_env = self.T_mean + self.deltaT * s

        return T_env

    def _run_graph_controller(self, t: float):
        """
        Run the LTS controller using the dictionary-based specification in self.controller_spec.

        Rules:
          - Take unconditional edges (heater actions / getsense) immediately.
          - For time edges: wait until (t - entry_time) >= delay.
          - For condition edges (room.tempchange[>21] / [<20]):
              only take them when the condition holds;
              if no condition edge is enabled for the state, we stay in that state.
        """
        spec = self.controller_spec
        time_edges = spec["time_edges"]
        temperature_guards = spec["temperature_guards"]
        heater_actions = spec["heater_actions"]
        instant_transitions = spec["instant_transitions"]

        while True:
            s = self.graph_state

            # --- time edges (e.g. (3, "time +=10", 0), (8, "time +=10", 7)) ---
            if s in time_edges:
                edge = time_edges[s]
                delay = edge["delay"]
                elapsed = t - self.graph_state_enter_time
                if elapsed >= delay:
                    self._enter_state(edge["next"], t)
                    continue  # keep processing new state
                else:
                    # wait in this state until enough time has passed
                    break

            # --- condition edges on room temperature ---
            if s in temperature_guards:
                guards = temperature_guards[s]
                progressed = False

                for g in guards:
                    op = g["op"]
                    thr = g["threshold"]
                    if op == ">" and self.temp > thr:
                        self._enter_state(g["next"], t)
                        progressed = True
                        break
                    elif op == "<" and self.temp < thr:
                        self._enter_state(g["next"], t)
                        progressed = True
                        break

                # if a guard fired, we loop and process new state; otherwise we wait
                if progressed:
                    continue
                else:
                    break

            # --- heater actions (unconditional edges that set heater) ---
            if s in heater_actions:
                action = heater_actions[s]
                kind = action["action"]
                if kind == "activate_heater":
                    self.heater = 1
                elif kind == "switch_off_heater":
                    self.heater = 0

                self._enter_state(action["next"], t)
                continue

            # --- instant transitions (controller.getsense, etc.) ---
            if s in instant_transitions:
                edge = instant_transitions[s]
                self._enter_state(edge["next"], t)
                continue

            # No outgoing rule handled here -> stop
            break

    # ------------------------------------------------------------------
    # FMI do_step
    # ------------------------------------------------------------------

    def do_step(self, current_time, step_size):
        """
        Co-Simulation step:
          - integrate room ODE with smaller internal steps
          - after each substep, run the LTS controller to update heater
        """
        # number of internal substeps
        n_sub = max(1, int(round(step_size / self.dt_internal)))
        h = step_size / n_sub

        t = current_time

        for _ in range(n_sub):
            t += h
            self.time = t

            # 1) environment temperature at this substep
            self.T_env = self._compute_env_temperature(t)

            # 2) room temperature ODE integration (explicit Euler)
            dTdt = (self.T_env - self.temp) / self.tau + self.heater * self.heating_power
            self.temp += h * dTdt

            # 3) enforce physical bounds
            if self.temp < self.T_min:
                self.temp = self.T_min
            elif self.temp > self.T_max:
                self.temp = self.T_max

            # 4) run the graph-based controller (second "thread")
            self._run_graph_controller(t)

        return True
