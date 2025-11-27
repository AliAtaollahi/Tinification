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
      - graph-based controller that decides heater ON/OFF

    Graph (LTS) controlling the heater:

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
      - Start in state 6.
      - Edges with 'room.tempchange[...]' are guarded by the current temp:
            > 21  or  < 20  (else stay in the same state).
      - Edges with 'time += 10' are enabled only after staying in the state
        for 10 seconds of simulation time.
      - 'hc_unit.activateh' sets heater = 1.
      - 'hc_unit.switchoff' sets heater = 0.
      - 'controller.getsense' are instantaneous steps with no extra effect.

    Room temperature dynamics:

        dT/dt = (T_env(t) - T) / tau + heater * P

      T_env(t) = base(t) + stochastic_noise(t), mapped into [10, 25]
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

        # thermostat thresholds (used by graph guards)
        self.T_on = 20.0                 # "low" threshold
        self.T_off = 21.0                # "high" threshold

        # heater state (output)
        self.heater = 0                  # 0 = off, 1 = on

        # graph state (LTS) and its entry time
        self.graph_state = 6             # initial state from "des (6, ...)"
        self.graph_state_enter_time = 0.0

        # internal integration step inside each do_step (for ODE)
        self.dt_internal = 0.1           # seconds per internal Euler substep

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
        Run the LTS controller in a 'while true' loop.

        Rules:
          - Take unconditional edges immediately.
          - For time edges: wait until (t - entry_time) >= 10.
          - For condition edges (room.tempchange[>21] / [<20]):
              only take them when the condition holds;
              if no condition edge is enabled, we stay in that state.
        """
        while True:
            s = self.graph_state

            # --- time edges: (3, "time +=10", 0) and (8, "time +=10", 7) ---
            if s == 3:
                # time += 10  --> 0
                elapsed = t - self.graph_state_enter_time
                if elapsed >= 1.0:
                    self._enter_state(0, t)
                    continue  # keep processing new state
                else:
                    break  # wait in state 3
            if s == 8:
                # time += 10  --> 7
                elapsed = t - self.graph_state_enter_time
                if elapsed >= 1.0:
                    self._enter_state(7, t)
                    continue
                else:
                    break  # wait in state 8

            # --- condition edges on room temperature ---
            if s in (0, 6, 7):
                progressed = False

                # room.tempchange[> 21]
                if self.temp > 21.0:
                    if s == 0:
                        self._enter_state(2, t)   # (0, ">21", 2)
                    else:
                        self._enter_state(9, t)   # (6 or 7, ">21", 9)
                    progressed = True

                # room.tempchange[< 20]
                elif self.temp < 20.0:
                    if s == 0:
                        self._enter_state(1, t)   # (0, "<20", 1)
                    else:
                        self._enter_state(5, t)   # (6 or 7, "<20", 5)
                    progressed = True

                # temp in [20,21] -> no condition enabled, wait
                if progressed:
                    continue
                else:
                    break

            # --- unconditional / instant edges ---
            if s == 5:
                # (5, "hc_unit.activateh", 4)
                self.heater = 1
                self._enter_state(4, t)
                continue

            if s == 4:
                # (4, "controller.getsense", 3)
                self._enter_state(3, t)
                continue

            if s == 2:
                # (2, "hc_unit.switchoff", 8)
                self.heater = 0
                self._enter_state(8, t)
                continue

            if s == 9:
                # (9, "controller.getsense", 8)
                self._enter_state(8, t)
                continue

            if s == 1:
                # (1, "controller.getsense", 3)
                self._enter_state(3, t)
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
