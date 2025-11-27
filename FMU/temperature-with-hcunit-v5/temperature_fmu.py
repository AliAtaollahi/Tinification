import os
import re
from math import sin, pi
from random import Random

from pythonfmu import (
    Fmi2Slave,
    Fmi2Causality,
    Fmi2Variability,
    Real,
    Integer,
)


class Temperature(Fmi2Slave):
    """
    Room temperature FMU with:
      - smooth base signal (day/night)
      - smooth stochastic variation (AR(1) noise)
      - first-order room dynamics (ODE) integrated with substeps
      - LTS (from RebecaCore.aut) that decides heater ON/OFF

    .aut format (example):

        des (6,13,10)
        (7,"room.tempchange[> 21]",9)
        (7,"room.tempchange[< 20]",5)
        (0,"room.tempchange[> 21]",2)
        (0,"room.tempchange[< 20]",1)
        (6,"room.tempchange[> 21]",9)
        (6,"room.tempchange[< 20]",5)
        (5,"hc_unit.activateh[].[]",4)
        (4,"controller.getsense[].[]",3)
        (8,"time +=10",7)
        (3,"time +=10",0)
        (2,"hc_unit.switchoff[].[]",8)
        (9,"controller.getsense[].[]",8)
        (1,"controller.getsense[].[]",3)

    Semantics:
      - Start in the initial state from `des(...)`.
      - Labels:
          room.tempchange[> 21] / [< 20]  -> temperature guards
          time += 10                      -> time delay from state entry
          hc_unit.activateh               -> heater := 1
          hc_unit.switchoff               -> heater := 0
          controller.getsense             -> immediate transition (no extra effect)
      - Graph is executed in a "while true" loop after every internal step.
    """

    author = "Ali"
    description = "Stochastic room temperature with LTS-based heater controller (loaded from .aut)"

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
        self.period1 = 24.0              # slow daily cycle
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

        # heater state (output)
        self.heater = 0                  # 0 = off, 1 = on

        # ----- graph (LTS) loaded from RebecaCore.aut -----
        self.time = 0.0                  # simulation time
        self.graph = {}                  # state -> list of transitions
        self.graph_state = 0             # will be set by _load_aut
        self.graph_state_enter_time = 0.0

        self._load_aut()                 # parse RebecaCore.aut and initialize graph_state

        # ----- initial environment temperature T_env(0) -----
        base0 = self.base_a1 * sin(self.omega1 * 0.0 + self.phi1)  # ~1.0
        s0 = base0 + self.noise_level * self.noise
        s0 = max(-1.0, min(1.0, s0))     # clamp
        self.T_env = self.T_mean + self.deltaT * s0

        # start room temp equal to environment
        self.temp = self.T_env

        # internal integration step inside each do_step (for ODE)
        self.dt_internal = 0.1           # seconds per internal Euler substep

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
                description="Current state of the LTS controller",
            )
        )

    # ------------------------------------------------------------------
    # .aut loader and label parsing
    # ------------------------------------------------------------------

    def _load_aut(self):
        """Load and parse RebecaCore.aut from the same directory as this script."""
        here = os.path.dirname(__file__)
        aut_path = os.path.join(here, "RebecaCore.aut")

        try:
            with open(aut_path, "r") as f:
                lines = [ln.strip() for ln in f if ln.strip()]
        except Exception as e:
            # Fallback: empty graph, heater remains off
            self.graph = {}
            self.graph_state = 0
            self.graph_state_enter_time = self.time
            return

        # parse des line
        des_line = None
        for ln in lines:
            if ln.startswith("des"):
                des_line = ln
                break

        if des_line is None:
            raise Exception("No 'des' line found in RebecaCore.aut")

        m = re.search(r"des\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", des_line)
        if not m:
            raise Exception("Could not parse des line in RebecaCore.aut: " + des_line)

        init_state = int(m.group(1))
        # n_trans = int(m.group(2))  # not used
        # n_states = int(m.group(3)) # not used

        self.graph_state = init_state
        self.graph_state_enter_time = self.time
        self.graph = {}

        # parse transitions
        for ln in lines:
            if ln.startswith("des"):
                continue
            m = re.search(r'\(\s*(\d+)\s*,\s*"(.*?)"\s*,\s*(\d+)\s*\)', ln)
            if not m:
                continue
            src = int(m.group(1))
            label = m.group(2)
            dst = int(m.group(3))

            tr = self._parse_label(label, src, dst)
            if tr is None:
                continue
            self.graph.setdefault(src, []).append(tr)

    def _parse_label(self, label: str, src: int, dst: int):
        """Turn a label string into a structured transition dict."""
        # room.tempchange[> 21] / [< 20]
        if label.startswith("room.tempchange["):
            m = re.search(r"room\.tempchange\[(.*?)\]", label)
            if not m:
                return None
            cond = m.group(1).strip()
            if cond.startswith(">"):
                op = ">"
                thr_str = cond[1:].strip()
            elif cond.startswith("<"):
                op = "<"
                thr_str = cond[1:].strip()
            else:
                return None
            try:
                thr = float(thr_str)
            except ValueError:
                return None
            return {
                "src": src,
                "dst": dst,
                "type": "temp_cond",
                "op": op,
                "threshold": thr,
                "label": label,
            }

        # time += 10
        if label.startswith("time"):
            m = re.search(r"time\s*\+=\s*([0-9]+(?:\.[0-9]*)?)", label)
            if not m:
                return None
            delay = float(m.group(1))
            return {
                "src": src,
                "dst": dst,
                "type": "time",
                "delay": delay,
                "label": label,
            }

        # hc_unit.activateh[].[]
        if label.startswith("hc_unit.activateh"):
            return {
                "src": src,
                "dst": dst,
                "type": "action",
                "name": "hc_unit.activateh",
                "label": label,
            }

        # hc_unit.switchoff[].[]
        if label.startswith("hc_unit.switchoff"):
            return {
                "src": src,
                "dst": dst,
                "type": "action",
                "name": "hc_unit.switchoff",
                "label": label,
            }

        # controller.getsense[].[]
        if label.startswith("controller.getsense"):
            return {
                "src": src,
                "dst": dst,
                "type": "internal",
                "name": "controller.getsense",
                "label": label,
            }

        # unknown label -> ignore
        return None

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
        Run the LTS controller in a 'while true' loop.

        Strategy:
          - If there are time edges from the current state, check them first.
            * If delay satisfied -> take the edge (state change) and continue loop.
            * If not satisfied    -> stop (wait in this state).
          - Else, if there are temp_cond edges, evaluate them:
            * If one condition holds -> take that edge and continue.
            * If none holds          -> stop (wait).
          - Else, if there are action/internal edges, take the first one immediately
            (heater on/off for actions) and continue.
          - Else, no outgoing edges -> stop.
        """
        while True:
            s = self.graph_state
            outgoing = self.graph.get(s, [])
            if not outgoing:
                break

            progressed = False

            # 1) time edges
            time_edges = [tr for tr in outgoing if tr["type"] == "time"]
            if time_edges:
                # assume at most one time edge per state (as in the example)
                tr = time_edges[0]
                elapsed = t - self.graph_state_enter_time
                if elapsed >= tr["delay"]:
                    self._enter_state(tr["dst"], t)
                    progressed = True
                # if delay not met, we wait in this state
                if not progressed:
                    break
                else:
                    continue

            # 2) temperature condition edges
            temp_edges = [tr for tr in outgoing if tr["type"] == "temp_cond"]
            if temp_edges:
                taken = False
                for tr in temp_edges:
                    if tr["op"] == ">" and self.temp > tr["threshold"]:
                        self._enter_state(tr["dst"], t)
                        taken = True
                        break
                    if tr["op"] == "<" and self.temp < tr["threshold"]:
                        self._enter_state(tr["dst"], t)
                        taken = True
                        break
                if not taken:
                    # no condition enabled -> wait here
                    break
                else:
                    continue

            # 3) instantaneous action/internal edges
            inst_edges = [tr for tr in outgoing if tr["type"] in ("action", "internal")]
            if inst_edges:
                tr = inst_edges[0]

                if tr["type"] == "action":
                    if tr["name"] == "hc_unit.activateh":
                        self.heater = 1
                    elif tr["name"] == "hc_unit.switchoff":
                        self.heater = 0

                # controller.getsense or other internal labels just change state
                self._enter_state(tr["dst"], t)
                continue

            # nothing more to do
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
