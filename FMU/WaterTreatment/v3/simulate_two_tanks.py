# simulate_two_tanks.py
import argparse
import csv
import os
import re
import shutil
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from fmpy import extract, read_model_description
from fmpy.fmi2 import FMU2Slave


def instantiate_cs(fmu_path: str, instance_name: str):
    unzipdir = extract(fmu_path)
    md = read_model_description(fmu_path)

    cs = md.coSimulation
    if cs is None:
        raise RuntimeError(f"{fmu_path} has no CoSimulation section")

    fmu = FMU2Slave(
        guid=md.guid,
        unzipDirectory=unzipdir,
        modelIdentifier=cs.modelIdentifier,
        instanceName=instance_name,
    )

    fmu.instantiate()
    fmu.setupExperiment(startTime=0.0)
    fmu.enterInitializationMode()
    fmu.exitInitializationMode()

    vr = {v.name: v.valueReference for v in md.modelVariables}
    return fmu, vr, unzipdir


def valve_schedule(t: float) -> Tuple[int, int, int]:
    if t < 100.0:
        return 1, 0, 0
    if t < 200.0:
        return 1, 1, 0
    return 0, 1, 1


# -----------------------------------------------------------------------------
# RebecaCore (AUT/LTS) controller for two tanks
# -----------------------------------------------------------------------------

_AUT_HEADER_RE = re.compile(r"des\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)\s*$")
_AUT_EDGE_RE = re.compile(r'^\(\s*(\d+)\s*,\s*"(.*)"\s*,\s*(\d+)\s*\)\s*$')

_TIME_RE = re.compile(r"^time\s*\+=\s*([0-9]+(?:\.[0-9]+)?)\s*$")
_VALVE_RE = re.compile(r"^(v[123])\.(open_valve|close_valve)\s*$")
_SENSE_RE = re.compile(r"^sensor\.senselevel\[(.*)\]\s*$")

_CMP_RE = re.compile(r"^(<=|>=|<|>)\s*([0-9]+(?:\.[0-9]+)?)\s*$")
_INT_RE = re.compile(r"^[0-9]+$")

_INF = 1e30


def _normalize_label(label: str) -> str:
    """Strip the Aldebaran-style trailing '.[]' / '[]' noise."""
    lab = label.strip()
    while lab.endswith(".[]"):
        lab = lab[:-3]
    while lab.endswith("[]"):
        lab = lab[:-2]
    return lab.strip()


def _bucket(value: float, thresholds: List[float]) -> int:
    b = 1
    for thr in thresholds:
        if value >= thr:
            b += 1
        else:
            break
    return b


def _atom_to_pred(atom: str, *, thresholds: List[float]) -> Callable[[float], bool]:
    atom = atom.strip()
    m = _CMP_RE.match(atom)
    if m:
        op, num = m.group(1), float(m.group(2))
        if op == "<":
            return lambda x, n=num: x < n
        if op == ">":
            return lambda x, n=num: x > n
        if op == "<=":
            return lambda x, n=num: x <= n
        if op == ">=":
            return lambda x, n=num: x >= n

    # Optional: support bare integers like ",2" meaning bucket(x)==2
    if _INT_RE.match(atom):
        k = int(atom)
        return lambda x, kk=k, th=thresholds: _bucket(x, th) == kk

    return lambda _x: False


def _split_atoms(part: str) -> List[str]:
    return [a.strip() for a in re.split(r"\s*&&\s*", part.strip()) if a.strip()]


def _make_sense_guard(
    inner: str,
    *,
    t1_thresholds: List[float],
    t2_thresholds: List[float],
) -> Callable[[float, float], bool]:
    """
    Core semantics (your latest core):
      sensor.senselevel[ <tank1_expr> , <tank2_expr> ]

    - Comma separates tanks: part0 -> tank1, part1 -> tank2
    - Inside each part, && is conjunction on the same tank

    Backward-compat:
      If there is NO comma and we see "a && b", interpret as a->tank1, b->tank2.
    """
    parts = [p.strip() for p in inner.split(",") if p.strip()]

    t1_preds: List[Callable[[float], bool]] = []
    t2_preds: List[Callable[[float], bool]] = []

    if len(parts) >= 2:
        for a in _split_atoms(parts[0]):
            t1_preds.append(_atom_to_pred(a, thresholds=t1_thresholds))
        for p in parts[1:]:
            for a in _split_atoms(p):
                t2_preds.append(_atom_to_pred(a, thresholds=t2_thresholds))

    elif len(parts) == 1:
        atoms = _split_atoms(parts[0])
        if len(atoms) >= 2:
            t1_preds.append(_atom_to_pred(atoms[0], thresholds=t1_thresholds))
            for a in atoms[1:]:
                t2_preds.append(_atom_to_pred(a, thresholds=t2_thresholds))
        elif len(atoms) == 1:
            t1_preds.append(_atom_to_pred(atoms[0], thresholds=t1_thresholds))

    def guard(L1: float, L2: float) -> bool:
        return all(pred(L1) for pred in t1_preds) and all(pred(L2) for pred in t2_preds)

    return guard


# --- Specificity ranking for overlapping sense guards (main fix) ---

def _range_key(expr: str) -> Tuple:
    """
    Compute a specificity key for a single tank expression.
    Higher key => more specific.

    Heuristics:
      - more comparator atoms => more specific
      - bounded interval (lower and upper) => more specific than one-sided
      - smaller interval width => more specific
      - for one-sided: smaller upper bound (<) => more specific; larger lower bound (>) => more specific
    """
    atoms = _split_atoms(expr) if expr else []
    n = 0
    lower = -_INF
    upper = _INF

    for a in atoms:
        m = _CMP_RE.match(a.strip())
        if not m:
            # treat unknown atom as "not contributing" to specificity
            continue
        n += 1
        op, val = m.group(1), float(m.group(2))
        if op in (">", ">="):
            lower = max(lower, val)
        else:  # "<" or "<="
            upper = min(upper, val)

    bounded = 1 if (lower > -_INF / 2 and upper < _INF / 2) else 0
    width = (upper - lower) if bounded else _INF

    # Lexicographic: bigger is better
    return (n, bounded, -width, lower, -upper)


def _sense_specificity_key(inner: str) -> Tuple:
    parts = [p.strip() for p in inner.split(",") if p.strip()]
    t1 = parts[0] if len(parts) >= 1 else ""
    t2 = parts[1] if len(parts) >= 2 else ""
    k1 = _range_key(t1)
    k2 = _range_key(t2)
    return (k1[0] + k2[0], k1, k2)


@dataclass(frozen=True)
class Edge:
    src: int
    dst: int
    kind: str  # "time" | "action" | "sense" | "other"
    label: str
    delay: Optional[float] = None
    action: Optional[Tuple[str, int]] = None  # ("v1",1) etc.
    guard: Optional[Callable[[float, float], bool]] = None
    sense_key: Optional[Tuple] = None  # used only for kind=="sense"


def parse_rebeca_aut_two_tanks(path: str) -> Tuple[int, Dict[int, List[Edge]]]:
    with open(path, "r", encoding="utf-8") as f:
        lines = [ln.strip() for ln in f if ln.strip()]

    m = _AUT_HEADER_RE.match(lines[0])
    if not m:
        raise ValueError(f"Not an AUT/LTS header: {lines[0]!r}")
    init_state = int(m.group(1))

    raw_edges: List[Tuple[int, str, int]] = []
    for ln in lines[1:]:
        m2 = _AUT_EDGE_RE.match(ln)
        if not m2:
            continue
        src = int(m2.group(1))
        label = _normalize_label(m2.group(2))
        dst = int(m2.group(3))
        raw_edges.append((src, label, dst))

    # collect thresholds (only needed if you ever use ",2" bucket rules)
    t1_nums: List[float] = []
    t2_nums: List[float] = []

    for _src, label, _dst in raw_edges:
        sm = _SENSE_RE.match(label)
        if not sm:
            continue
        inner = sm.group(1).strip()
        parts = [p.strip() for p in inner.split(",") if p.strip()]

        if len(parts) >= 2:
            for a in _split_atoms(parts[0]):
                cm = _CMP_RE.match(a)
                if cm:
                    t1_nums.append(float(cm.group(2)))
            for p in parts[1:]:
                for a in _split_atoms(p):
                    cm = _CMP_RE.match(a)
                    if cm:
                        t2_nums.append(float(cm.group(2)))
        elif len(parts) == 1:
            atoms = _split_atoms(parts[0])
            if len(atoms) >= 1:
                cm = _CMP_RE.match(atoms[0])
                if cm:
                    t1_nums.append(float(cm.group(2)))
            if len(atoms) >= 2:
                for a in atoms[1:]:
                    cm = _CMP_RE.match(a)
                    if cm:
                        t2_nums.append(float(cm.group(2)))

    t1_thresholds = sorted(set(t1_nums))
    t2_thresholds = sorted(set(t2_nums))

    out: Dict[int, List[Edge]] = {}
    for src, label, dst in raw_edges:
        kind = "other"
        delay = None
        action = None
        guard = None
        sense_key = None

        tm = _TIME_RE.match(label)
        if tm:
            kind = "time"
            delay = float(tm.group(1))

        vm = _VALVE_RE.match(label)
        if vm:
            kind = "action"
            v = vm.group(1)
            is_open = (vm.group(2) == "open_valve")
            action = (v, 1 if is_open else 0)

        sm = _SENSE_RE.match(label)
        if sm:
            kind = "sense"
            inner = sm.group(1)
            guard = _make_sense_guard(inner, t1_thresholds=t1_thresholds, t2_thresholds=t2_thresholds)
            sense_key = _sense_specificity_key(inner)

        out.setdefault(src, []).append(
            Edge(
                src=src,
                dst=dst,
                kind=kind,
                label=label,
                delay=delay,
                action=action,
                guard=guard,
                sense_key=sense_key,
            )
        )

    return init_state, out


class TwoTanksRebecaController:
    """
    Execute LTS as a deterministic controller.

    Order:
      1) sense edges (choose MOST specific enabled one)
      2) action edges (first in file order)
      3) time edges (first ready in file order)
      4) otherwise wait
    """

    def __init__(self, rebeca_aut_path: str, *, max_internal_steps: int = 10_000):
        self.init_state, self.out = parse_rebeca_aut_two_tanks(rebeca_aut_path)
        self.state = self.init_state
        self.enter_time = 0.0
        self.max_internal_steps = max_internal_steps
        self.valves = {"v1": 0, "v2": 0, "v3": 0}

    def compute(self, t: float, L1: float, L2: float) -> Tuple[int, int, int]:
        steps = 0
        while steps < self.max_internal_steps:
            steps += 1
            edges = self.out.get(self.state, [])

            # 1) sense edges: pick MOST specific enabled (fixes overlap issues like <400 vs <100)
            enabled_sense: List[Edge] = []
            for e in edges:
                if e.kind == "sense" and e.guard is not None and e.guard(L1, L2):
                    enabled_sense.append(e)
            if enabled_sense:
                best = max(enabled_sense, key=lambda e: e.sense_key or ())
                self.state = best.dst
                self.enter_time = t
                continue

            # 2) action edges next (immediate, first in file order)
            for e in edges:
                if e.kind == "action" and e.action is not None:
                    v, val = e.action
                    self.valves[v] = val
                    self.state = e.dst
                    self.enter_time = t
                    break
            else:
                # 3) time edges last (only when ready; otherwise wait)
                for e in edges:
                    if e.kind == "time" and e.delay is not None and (t - self.enter_time) >= float(e.delay):
                        self.state = e.dst
                        self.enter_time = t
                        break
                else:
                    # 4) stable / waiting
                    return self.valves["v1"], self.valves["v2"], self.valves["v3"]

            continue

        raise RuntimeError("Controller exceeded max_internal_steps (possible LTS loop).")


# -----------------------------------------------------------------------------
# Main simulation
# -----------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tank1", default="Tank1.fmu")
    ap.add_argument("--tank2", default="Tank2.fmu")
    ap.add_argument("--stop-time", type=float, default=300.0)
    ap.add_argument("--dt", type=float, default=1.0)

    ap.add_argument("--p1_fill", type=float, default=4.0)
    ap.add_argument("--p2_trans", type=float, default=5.0)
    ap.add_argument("--p3_drain", type=float, default=6.0)

    ap.add_argument("--H1", type=float, default=500.0)
    ap.add_argument("--H2", type=float, default=5000.0)

    ap.add_argument("--controller", choices=["rebeca", "schedule"], default="rebeca")
    ap.add_argument("--rebeca", default="RebecaCore.txt")
    ap.add_argument("--max-lts-steps", type=int, default=10000)

    ap.add_argument("--out", default="TwoTanks.csv")
    args = ap.parse_args()

    tank1, vr1, dir1 = instantiate_cs(args.tank1, "tank1")
    tank2, vr2, dir2 = instantiate_cs(args.tank2, "tank2")

    t1_qin, t1_qout, t1_level = vr1["q_in"], vr1["q_out"], vr1["level"]
    t2_qin, t2_qout, t2_level = vr2["q_in"], vr2["q_out"], vr2["level"]

    H1 = float(args.H1)
    H2 = float(args.H2)

    ctrl: Optional[TwoTanksRebecaController] = None
    if args.controller == "rebeca":
        if not args.rebeca or not os.path.exists(args.rebeca):
            raise FileNotFoundError(f"--rebeca file not found: {args.rebeca!r}")
        ctrl = TwoTanksRebecaController(args.rebeca, max_internal_steps=args.max_lts_steps)

    rows: List[List[float]] = []
    t = 0.0
    dt = float(args.dt)

    # initial sample
    L1 = tank1.getReal([t1_level])[0]
    L2 = tank2.getReal([t2_level])[0]
    if ctrl is not None:
        v1, v2, v3 = ctrl.compute(t, L1, L2)
    else:
        v1, v2, v3 = valve_schedule(t)
    rows.append([t, L1, L2, v1, v2, v3, 0.0])

    try:
        while t < args.stop_time:
            L1 = tank1.getReal([t1_level])[0]
            L2 = tank2.getReal([t2_level])[0]

            if ctrl is not None:
                v1, v2, v3 = ctrl.compute(t, L1, L2)
            else:
                v1, v2, v3 = valve_schedule(t)

            q_in1 = v1 * args.p1_fill
            q_drain2 = v3 * args.p3_drain
            q_trans_desired = v2 * args.p2_trans

            # cap transfer
            max_from_t1 = max(0.0, (L1 + q_in1 * dt) / dt)
            max_into_t2 = max(0.0, (H2 - L2 + q_drain2 * dt) / dt)
            q_trans = min(q_trans_desired, max_from_t1, max_into_t2)

            tank1.setReal([t1_qin, t1_qout], [q_in1, q_trans])
            tank2.setReal([t2_qin, t2_qout], [q_trans, q_drain2])

            tank1.doStep(currentCommunicationPoint=t, communicationStepSize=dt)
            tank2.doStep(currentCommunicationPoint=t, communicationStepSize=dt)

            t += dt

            L1 = tank1.getReal([t1_level])[0]
            L2 = tank2.getReal([t2_level])[0]
            rows.append([t, L1, L2, v1, v2, v3, q_trans])

    finally:
        for fmu in (tank1, tank2):
            try:
                fmu.terminate()
            except Exception:
                pass
            try:
                fmu.freeInstance()
            except Exception:
                pass
        shutil.rmtree(dir1, ignore_errors=True)
        shutil.rmtree(dir2, ignore_errors=True)

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["time", "tank1_level", "tank2_level", "v1_cmd", "v2_cmd", "v3_cmd", "q_transfer"])
        w.writerows(rows)

    print(f"Saved {args.out}")


if __name__ == "__main__":
    main()
