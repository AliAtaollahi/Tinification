import re
from pprint import pprint

aut_text = """des (6,13,10)
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
"""

lines = [l.strip() for l in aut_text.splitlines() if l.strip()]

# --- header: des (init, num_transitions, num_states) ---
m = re.match(r"des\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", lines[0])
if not m:
    raise ValueError("Invalid des line")

initial_state = int(m.group(1))
num_transitions = int(m.group(2))
num_states = int(m.group(3))

temperature_guards = {}
time_edges = {}
heater_actions = {}
instant_transitions = {}
raw_transitions = []

# --- transitions ---
for line in lines[1:]:
    m2 = re.match(r"\(\s*(\d+)\s*,\s*\"([^\"]+)\"\s*,\s*(\d+)\s*\)", line)
    if not m2:
        raise ValueError(f"Invalid transition line: {line}")

    src = int(m2.group(1))
    label = m2.group(2)
    dst = int(m2.group(3))

    raw_transitions.append((src, label, dst))

    # room.tempchange guards
    if label.startswith("room.tempchange"):
        m_guard = re.match(r"room\.tempchange\[\s*([<>])\s*([\d\.]+)\s*\]", label)
        if not m_guard:
            raise ValueError(f"Cannot parse guard: {label}")
        op = m_guard.group(1)
        threshold = float(m_guard.group(2))
        temperature_guards.setdefault(src, []).append(
            {"op": op, "threshold": threshold, "next": dst}
        )

    # time edges
    elif label.startswith("time +="):
        m_time = re.match(r"time\s*\+\=\s*([\d\.]+)", label)
        if not m_time:
            raise ValueError(f"Cannot parse time edge: {label}")
        delay = float(m_time.group(1))
        time_edges[src] = {"delay": delay, "next": dst}

    # heater actions
    elif label.startswith("hc_unit.activateh"):
        heater_actions[src] = {
            "action": "activate_heater",
            "label": label,
            "next": dst,
        }
    elif label.startswith("hc_unit.switchoff"):
        heater_actions[src] = {
            "action": "switch_off_heater",
            "label": label,
            "next": dst,
        }

    # instant transitions (getsense)
    elif label.startswith("controller.getsense"):
        instant_transitions[src] = {
            "label": label,
            "next": dst,
        }

# --- simplify raw_transitions: keep src, dest, and only temp/time info ---
simplified_raw = []
for src, label, dst in raw_transitions:
    simple_label = ""

    # temp guards -> "temp[> 21]" / "temp[< 20]"
    if label.startswith("room.tempchange"):
        m_guard = re.match(r"room\.tempchange\[\s*([<>])\s*([\d\.]+)\s*\]", label)
        if not m_guard:
            raise ValueError(f"Cannot parse guard: {label}")
        op = m_guard.group(1)
        threshold = float(m_guard.group(2))
        simple_label = f"temp[{op} {threshold}]"

    # time edges -> "elapsed time>=10"
    elif label.startswith("time +="):
        m_time = re.match(r"time\s*\+\=\s*([\d\.]+)", label)
        if not m_time:
            raise ValueError(f"Cannot parse time edge: {label}")
        delay = float(m_time.group(1))
        simple_label = f"elapsed time>={delay}"

    # everything else (heater actions, getsense) -> no condition
    else:
        simple_label = ""  # only src/dst matter here

    simplified_raw.append((src, simple_label, dst))

controller_spec = {
    "initial_state": initial_state,
    "num_transitions": num_transitions,
    "num_states": num_states,
    "temperature_guards": temperature_guards,
    "time_edges": time_edges,
    "heater_actions": heater_actions,
    "instant_transitions": instant_transitions,
    "raw_transitions": simplified_raw,
}

pprint(controller_spec["raw_transitions"])
