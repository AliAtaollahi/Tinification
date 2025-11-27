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
      - thermostat-controlled heater with hysteresis
      - built–in heater schedule: heater_enabled(t) = 1 only in [100,200] and [400,500]

    Actual room temp T follows:

        dT/dt = (T_env(t) - T) / tau + heater * P

      T_env(t)  = base(t) + stochastic_noise(t), mapped into [10, 25]
      tau       = room time constant
      P         = heating power (deg/time)
      heater    = 1 if T < 20, 0 if T > 21, else keep previous state
                  (only if heater_enabled(t) = 1 according to schedule)
    """

    author = "Ali"
    description = "Smooth room temp with stochastic environment and scheduled heater"

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
        self.noise_level = 0.6           # strength of noise (0 = deterministic)
        self.smooth_factor = 0.95        # 0.95 => quite smooth, 0.99 => very smooth
        self.noise = 2.0                 # initial noise state

        # pseudo-random generator: NO fixed seed -> different each run
        self.rng = Random()              # use Random(12345) for reproducible runs

        # ----- room + heater dynamics -----
        self.tau = 20.0                  # slower room response  (bigger = smoother)
        self.heating_power = 0.3         # deg per time unit when heater ON

        # thermostat thresholds (hysteresis)
        self.T_on = 20.0                 # heater turns ON when T < 20
        self.T_off = 21.0                # heater turns OFF when T > 21

        # heater state (output)
        self.heater = 0                  # 0 = off, 1 = on

        # heater_enabled is now driven by an internal time schedule
        # Here we hard-code: ON in [100,200] and [400,500], OFF otherwise.
        self.heater_enabled = 0          # current state of schedule (0/1)
        self.heater_schedule = [
            (0.0, 1000.0),
            # (400.0, 500.0),
        ]

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
                description="Heater state (0 = OFF, 1 = ON)",
            )
        )

        # heater_enabled as discrete output following the internal schedule
        self.register_variable(
            Integer(
                "heater_enabled",
                causality=Fmi2Causality.output,
                variability=Fmi2Variability.discrete,
                start=self.heater_enabled,
                description="Heater enable schedule (0 = disabled, 1 = enabled)",
            )
        )

    # ---------- Helper: heater schedule ----------

    def _update_heater_enabled(self, t: float):
        """Update heater_enabled according to fixed time intervals."""
        enabled = 0
        for start, end in self.heater_schedule:
            if start <= t <= end:
                enabled = 1
                break
        self.heater_enabled = enabled

    # ---------- Helper: environment temperature ----------

    def _compute_env_temperature(self, t):
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

            # 0) update heater_enabled from schedule
            self._update_heater_enabled(t)

            # 1) environment temperature at this substep
            self.T_env = self._compute_env_temperature(t)

            # 2) thermostat with hysteresis on room temp
            if self.heater_enabled:  # heater logic active in scheduled intervals
                if self.temp < self.T_on:
                    self.heater = 1
                elif self.temp > self.T_off:
                    self.heater = 0
                # else: keep previous heater state
            else:
                # heater disabled: always off
                self.heater = 0

            # 3) room temperature ODE
            dTdt = (self.T_env - self.temp) / self.tau + self.heater * self.heating_power
            self.temp += h * dTdt

            # 4) enforce physical bounds
            if self.temp < self.T_min:
                self.temp = self.T_min
            elif self.temp > self.T_max:
                self.temp = self.T_max

        return True
