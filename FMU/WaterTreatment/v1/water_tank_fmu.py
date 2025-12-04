# water_tank_fmu.py
from math import sqrt
from pythonfmu import Fmi2Slave, Fmi2Causality, Fmi2Variability, Real, Integer


class WaterTank(Fmi2Slave):
    """
    Water tank (height = 500 m) with ODE dynamics:

      level_dot = q_in - q_out

    where
      q_in  = v1 * p1
      q_out = v2 * p2 * sqrt(level / H)     (drain is slightly nonlinear & level-dependent)

    v1, v2 are *actual valve openings* in [0,1] and follow a first-order ODE:
      dv/dt = (v_cmd - v) / tau_valve

    Valve command schedule (300 s total):
      0-100:  v1 open,  v2 closed
      100-200: both open
      200-300: v1 closed, v2 open
    """

    author = "Ali"
    description = "Water tank with ODE fill/drain + scheduled valves"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # ---- tank ----
        self.H = 500.0          # physical tank height [m]
        self.level = 0.0        # water level [m]

        # ---- valve commands (0/1) ----
        self.v1_cmd = 0
        self.v2_cmd = 0

        # ---- actual valve openings (continuous 0..1) ----
        self.v1 = 0.0
        self.v2 = 0.0
        self.tau_valve = 2.0    # seconds, valve smoothing time constant

        # ---- flow "speeds" (slightly different) ----
        # p1 is fill rate when v1=1, p2 is drain scale when v2=1
        self.p1 = 3.8           # [m/s] fill
        self.p2 = 3.6           # [m/s] drain (slightly smaller)

        # ---- internal integration ----
        self.dt_internal = 0.05  # seconds per internal Euler step

        # ---- outputs / debug ----
        self.q_in = 0.0
        self.q_out = 0.0

        # ---------- register FMU variables ----------
        self.register_variable(
            Real("level", causality=Fmi2Causality.output,
                 variability=Fmi2Variability.continuous, start=self.level,
                 description="Water level [m]")
        )
        self.register_variable(
            Integer("v1_cmd", causality=Fmi2Causality.output,
                    variability=Fmi2Variability.discrete, start=self.v1_cmd,
                    description="Valve 1 command (0/1)")
        )
        self.register_variable(
            Integer("v2_cmd", causality=Fmi2Causality.output,
                    variability=Fmi2Variability.discrete, start=self.v2_cmd,
                    description="Valve 2 command (0/1)")
        )
        self.register_variable(
            Real("v1", causality=Fmi2Causality.output,
                 variability=Fmi2Variability.continuous, start=self.v1,
                 description="Valve 1 opening (0..1)")
        )
        self.register_variable(
            Real("v2", causality=Fmi2Causality.output,
                 variability=Fmi2Variability.continuous, start=self.v2,
                 description="Valve 2 opening (0..1)")
        )
        self.register_variable(
            Real("q_in", causality=Fmi2Causality.output,
                 variability=Fmi2Variability.continuous, start=self.q_in,
                 description="Inflow rate [m/s]")
        )
        self.register_variable(
            Real("q_out", causality=Fmi2Causality.output,
                 variability=Fmi2Variability.continuous, start=self.q_out,
                 description="Outflow rate [m/s]")
        )

    # ---------------- schedule ----------------
    def _update_schedule(self, t: float):
        if t < 100.0:
            self.v1_cmd, self.v2_cmd = 1, 0
        elif t < 200.0:
            self.v1_cmd, self.v2_cmd = 1, 1
        elif t <= 300.0:
            self.v1_cmd, self.v2_cmd = 0, 1
        else:
            self.v1_cmd, self.v2_cmd = 0, 0

    # ---------------- FMI do_step ----------------
    def do_step(self, current_time, step_size):
        n_sub = max(1, int(round(step_size / self.dt_internal)))
        h = step_size / n_sub

        t = current_time

        for _ in range(n_sub):
            t += h

            # 1) schedule -> commands
            self._update_schedule(t)

            # 2) valve ODEs (smooth opening/closing)
            self.v1 += h * ((float(self.v1_cmd) - self.v1) / self.tau_valve)
            self.v2 += h * ((float(self.v2_cmd) - self.v2) / self.tau_valve)

            # clamp valve openings
            if self.v1 < 0.0: self.v1 = 0.0
            if self.v1 > 1.0: self.v1 = 1.0
            if self.v2 < 0.0: self.v2 = 0.0
            if self.v2 > 1.0: self.v2 = 1.0

            # 3) flows (ODE input)
            self.q_in = self.v1 * self.p1

            # drain depends on level (0 at empty, ~p2 at full with v2=1)
            level_frac = 0.0 if self.level <= 0.0 else min(1.0, self.level / self.H)
            self.q_out = self.v2 * self.p2 * sqrt(level_frac)

            # 4) tank ODE
            dlevel_dt = self.q_in - self.q_out
            self.level += h * dlevel_dt

            # physical bounds
            if self.level < 0.0:
                self.level = 0.0
            elif self.level > self.H:
                self.level = self.H

        return True
