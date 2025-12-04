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
      - small smoothed random variation
      - first-order room dynamics (ODE) integrated with substeps
      - thermostat-controlled heater with hysteresis

    Actual room temp T follows:

        dT/dt = (T_env(t) - T) / tau + heater * P

      T_env(t)  = smooth base + small noise, in [10, 25]
      tau       = room time constant
      P         = heating power (deg/time)
      heater    = 1 if T < 20, 0 if T > 21, else keep previous state
    """

    author = "Ali"
    description = "Smooth room temp with heater ODE and 20/21 °C thermostat"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # ----- bounds for temperature -----
        self.T_min = 10.0
        self.T_max = 25.0
        self.T_mean = 0.5 * (self.T_min + self.T_max)   # 17.5
        self.deltaT = 0.5 * (self.T_max - self.T_min)   # 7.5

        # ----- base signal (environment temperature without heater) -----
        # single smooth daily sinusoid
        self.period1 = 24.0              # slow daily cycle
        self.omega1 = 2.0 * pi / self.period1
        self.base_a1 = 1.0
        self.phi1 = pi / 2               # start near max

        # ----- smoothed random component for T_env(t) -----
        self.noise_level = 0.1           # 0 = deterministic, 1 = all noise
        self.smooth_factor = 0.99        # very smooth noise
        self.noise = 0.0
        self.rng = Random(12345)         # fixed seed; use Random() for non-repeatable

        # ----- room + heater dynamics -----
        self.tau = 20.0                  # slower room response  (bigger = smoother)
        self.heating_power = 0.3         # deg per time unit when heater ON (smaller = smoother)

        # thermostat thresholds (hysteresis)
        self.T_on = 20.0                 # heater turns ON when T < 20
        self.T_off = 21.0                # heater turns OFF when T > 21

        self.heater = 0                  # 0 = off, 1 = on

        # internal integration step inside each do_step (for ODE)
        self.dt_internal = 0.1           # seconds per internal Euler substep

        # ----- internal time -----
        self.time = 0.0

        # initial environment temperature T_env(0)
        base0 = self.base_a1 * sin(self.omega1 * 0.0 + self.phi1)  # ~1.0
        s0 = (1.0 - self.noise_level) * base0 + self.noise_level * self.noise
        self.T_env = self.T_mean + self.deltaT * s0

        # start room temp equal to environment
        self.temp = self.T_env

        # ----- expose FMU variables -----
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
                description="Environment/base temperature [°C] (without heater)",
            )
        )

        self.register_variable(
            Integer(
                "heater",
                causality=Fmi2Causality.output,
                variability=Fmi2Variability.discrete,
                start=self.heater,
                description="Heater state (0 = OFF, 1 = ON)",
            )
        )

    # ---------- Helper: environment temperature ----------

    def _compute_env_temperature(self, t):
        """Compute smooth environment temperature T_env(t) with base + smoothed noise."""
        # base smooth signal
        base = self.base_a1 * sin(self.omega1 * t + self.phi1)

        # update smoothed noise (AR(1))
        u = self.rng.uniform(-1.0, 1.0)
        self.noise = self.smooth_factor * self.noise + (1.0 - self.smooth_factor) * u

        # combine base and noise
        s = (1.0 - self.noise_level) * base + self.noise_level * self.noise

        # map to [T_min, T_max]
        T_env = self.T_mean + self.deltaT * s

        # clip just in case
        if T_env < self.T_min:
            T_env = self.T_min
        elif T_env > self.T_max:
            T_env = self.T_max

        return T_env

    # ---------- FMI do_step ----------

    def do_step(self, current_time, step_size):
        """
        Co-Simulation step:
          - integrate room ODE with smaller internal steps
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

            # 2) thermostat with hysteresis on room temp
            if self.temp < self.T_on:
                self.heater = 1
            elif self.temp > self.T_off:
                self.heater = 0
            # else: keep previous heater state

            # 3) room temperature ODE
            dTdt = (self.T_env - self.temp) / self.tau + self.heater * self.heating_power
            self.temp += h * dTdt

            # 4) enforce physical bounds
            if self.temp < self.T_min:
                self.temp = self.T_min
            elif self.temp > self.T_max:
                self.temp = self.T_max

        return True
