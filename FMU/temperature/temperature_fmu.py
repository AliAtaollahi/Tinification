from math import sin, pi
from random import Random

from pythonfmu import Fmi2Slave, Fmi2Causality, Fmi2Variability, Real


class Temperature(Fmi2Slave):
    """
    Room temperature FMU with smooth base + smoothed random fluctuations.

    Output:
        temp  [°C]  (Real, continuous)

    Behaviour:

        1) Base signal (smooth, deterministic):

           base(t) = 0.7 * sin(ω1 * t + φ1)
                   + 0.3 * sin(ω2 * t + φ2)

           ω1 = 2π / 24   (slow daily cycle)
           ω2 = 2π / 8    (medium fluctuation)
           φ1 = φ2 = π/2

           => base(t) ∈ [-1, 1]

        2) Smoothed random noise (pseudo-random, looks non-deterministic):

           noise_t = smooth_factor * noise_{t-Δt}
                     + (1 - smooth_factor) * u_t

           where u_t ~ Uniform(-1, 1), smooth_factor ∈ (0,1), e.g. 0.9

           => noise_t ∈ [-1, 1], but changes slowly (smooth).

        3) Combine them:

           s(t) = (1 - noise_level) * base(t) + noise_level * noise_t

           with noise_level ∈ [0,1], e.g. 0.3

           => s(t) ∈ [-1, 1]

        4) Map to temperature interval [T_min, T_max]:

           T_min = 10 °C
           T_max = 25 °C
           T_mean  = (T_min + T_max) / 2 = 17.5 °C
           ΔT      = (T_max - T_min) / 2 = 7.5 °C

           temp(t) = T_mean + ΔT * s(t)

           => 10 °C ≤ temp(t) ≤ 25 °C

        At t = 0 we choose base(0) = 1, noise_0 = 0
        so temp(0) = T_mean + ΔT * 1 = 25 °C (start at maximum).
    """

    author = "Ali"
    description = "Room temperature in [10°C, 25°C] with smooth and random-like fluctuations"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # bounds
        self.T_min = 10.0
        self.T_max = 25.0

        # derived constants
        self.T_mean = 0.5 * (self.T_min + self.T_max)   # 17.5
        self.deltaT = 0.5 * (self.T_max - self.T_min)   # 7.5

        # base frequencies (time unit ~ hours)
        self.period1 = 24.0
        self.period2 = 8.0

        self.omega1 = 2.0 * pi / self.period1
        self.omega2 = 2.0 * pi / self.period2

        # amplitudes sum to 1.0
        self.base_a1 = 0.7
        self.base_a2 = 0.3

        # phases: start at max for base signal
        self.phi1 = pi / 2
        self.phi2 = pi / 2

        # noise parameters
        self.noise_level = 0.3     # weight of noise vs base
        self.smooth_factor = 0.9   # AR(1) smoothing; closer to 1 => smoother
        self.noise = 0.0           # initial noise value

        # pseudo-random generator (deterministic seed for repeatability)
        self.rng = Random(12345)

        # internal time
        self.time = 0.0

        # initial temperature at t = 0 (base = 1, noise = 0)
        base0 = self.base_a1 * sin(self.phi1) + self.base_a2 * sin(self.phi2)  # = 1.0
        s0 = (1.0 - self.noise_level) * base0 + self.noise_level * self.noise
        self.temp = self.T_mean + self.deltaT * s0  # = 25 °C

        # register temperature output as continuous Real
        self.register_variable(
            Real(
                "temp",
                causality=Fmi2Causality.output,
                variability=Fmi2Variability.continuous,
                start=self.temp,
                description="Room temperature [°C]",
            )
        )

    def do_step(self, current_time, step_size):
        """
        Update temperature based on the new time.
        For Co-Simulation, current_time is the beginning of the step.
        We compute T at t = current_time + step_size.
        """
        self.time = current_time + step_size
        t = self.time

        # 1) smooth base signal
        base = (
            self.base_a1 * sin(self.omega1 * t + self.phi1)
            + self.base_a2 * sin(self.omega2 * t + self.phi2)
        )

        # 2) smoothed random noise
        u = self.rng.uniform(-1.0, 1.0)
        self.noise = self.smooth_factor * self.noise + (1.0 - self.smooth_factor) * u

        # 3) combine base and noise (still in [-1,1])
        s = (1.0 - self.noise_level) * base + self.noise_level * self.noise

        # 4) map to [T_min, T_max]
        self.temp = self.T_mean + self.deltaT * s

        # safety clip for numerical round-off
        if self.temp < self.T_min:
            self.temp = self.T_min
        elif self.temp > self.T_max:
            self.temp = self.T_max

        return True
