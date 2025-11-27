from pythonfmu import Fmi2Slave, Fmi2Causality, Fmi2Variability, Integer


class Stair(Fmi2Slave):
    """
    Simple Stair FMU:

    - Output: counter (Integer)
    - Behaviour:
        counter(0) = 1
        each do_step: counter := counter + 1
    """

    author = "Ali"
    description = "Integer stair: 1, 2, 3, ..."

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # internal state
        self.counter = 1

        # expose 'counter' as a discrete Integer output with start value 1
        self.register_variable(
            Integer(
                "counter",
                causality=Fmi2Causality.output,
                variability=Fmi2Variability.discrete,
                start=self.counter,
            )
        )

    def do_step(self, current_time, step_size):
        # increase counter every step
        self.counter += 1
        return True
