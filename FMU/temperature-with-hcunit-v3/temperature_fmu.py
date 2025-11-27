# temperature_fmu.py
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
      - smooth base signal + smooth stochastic variation (AR(1) noise) for T_env(t)
      - 1st-order room ODE integrated with internal substeps (Euler)
      - thermostat heater with hysteresis (ON below T_on, OFF above T_off)
      - built-in schedule heater_enabled(t)=1 only inside heater_schedule intervals

    Change THESE to get what you asked:

      A) Always heater enabled:
         self.heater_schedule = [(0.0, 1000.0)]     # (or longer than stop-time)

      B) Heater never enabled (no heater variables in CSV output):
         self.heater_schedule = []                  # empty list
    """

    author = "Ali"
    description = "Room temperature with stochastic environment + scheduled thermostat heater"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # ----- bounds for temperature -----
        self.T_min = 10.0
        self.T_max = 25.0
        self.T_mean = 0.5 * (self.T_min + self.T_max)   # 17.5
        self.deltaT = 0.5 * (self.T_max - self.T_min)   # 7.5

        # ----- base signal (environment temperature without heater) -----
        self.period1 = 24.0
        self.omega1 = 2.0 * pi / self.period1
        self.base_a1 = 1.0
        self.phi1 = pi / 2  # start near max

        # ----- smoothed random component for T_env(t) -----
        self.noise_level = 0.6
        self.smooth_factor = 0.95
        self.noise = 2.0
        self.rng = Random()          # Random(12345) for reproducible runs

        # ----- room + heater dynamics -----
        self.tau = 20.0
        self.heating_power = 0.3

        # ---- thermostat set-points (UPDATED) ----
        # You asked: when heater is enabled, temperature should live roughly in [21, 24]
        self.T_on = 21.0             # heater turns ON when temp < 21
        self.T_off = 24.0            # heater turns OFF when temp > 24

        # heater state
        self.heater = 0

        # ---- heater schedule (EDIT THIS) ----
        # Always enabled:
        # self.heater_schedule = [(0.0, 1000.0)]
        #
        # Never enabled (and no heater/heater_enabled columns in output):
        # self.heater_schedule = []
        self.heater_schedule = [
            (0.0, 100.0),
            (300.0, 400.0),
        ] 

        # schedule output flag
        self.heater_enabled = 0

        # If schedule is empty, we will NOT expose heater-related outputs
        self.expose_heater_outputs = (len(self.heater_schedule) > 0)

        # internal integration step
        self.dt_internal = 0.1

        # internal time
        self.time = 0.0

        # initial environment temperature T_env(0)
        base0 = self.base_a1 * sin(self.omega1 * 0.0 + self.phi1)
        s0 = base0 + self.noise_level * self.noise
        s0 = max(-1.0, min(1.0, s0))
        self.T_env = self.T_mean + self.deltaT * s0

        # initial room temp
        self.temp = self.T_env

        # ----- expose FMU variables -----
        # temp FIRST so your plot.py always picks it
        self.register_variable(
            Real(
                "temp",
                causality=Fmi2Causality.output,
                variability=Fmi2Variability.continuous,
                start=self.temp,
                description="Room temperature [°C]",
            )
        )

        self.register_variable(
            Real(
                "T_env",
                causality=Fmi2Causality.output,
                variability=Fmi2Variability.continuous,
                start=self.T_env,
                description="Environment temperature [°C] (base + noise)",
            )
        )

        if self.expose_heater_outputs:
            self.register_variable(
                Integer(
                    "heater",
                    causality=Fmi2Causality.output,
                    variability=Fmi2Variability.discrete,
                    start=self.heater,
                    description="Heater state (0 = OFF, 1 = ON)",
                )
            )

            self.register_variable(
                Integer(
                    "heater_enabled",
                    causality=Fmi2Causality.output,
                    variability=Fmi2Variability.discrete,
                    start=self.heater_enabled,
                    description="Heater enabled by schedule (0/1)",
                )
            )

    # ---------- Helper: heater schedule ----------

    def _update_heater_enabled(self, t: float):
        if not self.heater_schedule:
            self.heater_enabled = 0
            return

        enabled = 0
        for start, end in self.heater_schedule:
            if start <= t <= end:
                enabled = 1
                break
        self.heater_enabled = enabled

    # ---------- Helper: environment temperature ----------

    def _compute_env_temperature(self, t: float) -> float:
        base = self.base_a1 * sin(self.omega1 * t + self.phi1)

        xi = self.rng.gauss(0.0, 1.0)
        self.noise = self.smooth_factor * self.noise + (1.0 - self.smooth_factor) * xi

        s = base + self.noise_level * self.noise
        if s < -1.0:
            s = -1.0
        elif s > 1.0:
            s = 1.0

        return self.T_mean + self.deltaT * s

    # ---------- FMI do_step ----------

    def do_step(self, current_time, step_size):
        n_sub = max(1, int(round(step_size / self.dt_internal)))
        h = step_size / n_sub

        t = current_time

        for _ in range(n_sub):
            t += h
            self.time = t

            # schedule -> heater_enabled
            self._update_heater_enabled(t)

            # environment
            self.T_env = self._compute_env_temperature(t)

            # heater logic
            if self.heater_enabled:
                if self.temp < self.T_on:
                    self.heater = 1
                elif self.temp > self.T_off:
                    self.heater = 0
            else:
                self.heater = 0

            # ODE integration
            dTdt = (self.T_env - self.temp) / self.tau + self.heater * self.heating_power
            self.temp += h * dTdt

            # bounds
            if self.temp < self.T_min:
                self.temp = self.T_min
            elif self.temp > self.T_max:
                self.temp = self.T_max

        return True
