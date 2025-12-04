# tank1_fmu.py
from pythonfmu import Fmi2Slave, Fmi2Causality, Fmi2Variability, Real


class Tank1(Fmi2Slave):
    """
    Tank1 (height = 500 m)
    Dynamics:
        dL/dt = q_in - q_out
    where q_in, q_out are "level rates" in m/s.

    Inputs:
      q_in  [m/s]  (from v1)
      q_out [m/s]  (to tank2 via v2)

    Output:
      level [m]
    """

    author = "Ali"
    description = "Tank1: dlevel/dt = q_in - q_out with saturation (0..height)"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.height = 500.0

        # inputs (m/s)
        self.q_in = 0.0
        self.q_out = 0.0

        # state/output (m)
        self.level = 0.0

        # internal integration resolution
        self.dt_internal = 0.1

        # register inputs
        self.register_variable(
            Real("q_in", causality=Fmi2Causality.input, variability=Fmi2Variability.continuous, start=0.0)
        )
        self.register_variable(
            Real("q_out", causality=Fmi2Causality.input, variability=Fmi2Variability.continuous, start=0.0)
        )

        # register output (IMPORTANT: no start=... to avoid initial="calculated"+start issue)
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

            # saturate
            if self.level < 0.0:
                self.level = 0.0
            elif self.level > self.height:
                self.level = self.height

        return True
