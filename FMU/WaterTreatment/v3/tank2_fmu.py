# tank2_fmu.py
from pythonfmu import Fmi2Slave, Fmi2Causality, Fmi2Variability, Real


class Tank2(Fmi2Slave):
    """
    Tank2 (height = 5000 m)
    Dynamics:
        dL/dt = q_in - q_out

    Inputs:
      q_in  [m/s]  (from tank1 via v2)
      q_out [m/s]  (drain via v3)

    Output:
      level [m]
    """

    author = "Ali"
    description = "Tank2: dlevel/dt = q_in - q_out with saturation (0..height)"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.height = 5000.0

        self.q_in = 0.0
        self.q_out = 0.0

        self.level = 0.0

        self.dt_internal = 0.1

        self.register_variable(
            Real("q_in", causality=Fmi2Causality.input, variability=Fmi2Variability.continuous, start=0.0)
        )
        self.register_variable(
            Real("q_out", causality=Fmi2Causality.input, variability=Fmi2Variability.continuous, start=0.0)
        )

        # IMPORTANT: no start=...
        self.register_variable(
            Real("level", causality=Fmi2Causality.output, variability=Fmi2Variability.continuous,
                 description="Water level [m]")
        )

    def do_step(self, current_time, step_size):
        n = max(1, int(round(step_size / self.dt_internal)))
        h = step_size / n

        for _ in range(n):
            dldt = self.q_in - self.q_out
            self.level += h * dldt

            if self.level < 0.0:
                self.level = 0.0
            elif self.level > self.height:
                self.level = self.height

        return True
