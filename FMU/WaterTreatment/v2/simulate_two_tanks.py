# simulate_two_tanks.py
import argparse
import csv
import shutil

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


def valve_schedule(t):
    """
    0-100:  v1=1, v2=0, v3=0
    100-200: v1=1, v2=1, v3=0
    200-300: v1=0, v2=1, v3=1
    """
    # if t < 100.0:
    #     return 1, 0, 0
    if t < 200.0:
        return 1, 1, 0
    return 0, 1, 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tank1", default="Tank1.fmu")
    ap.add_argument("--tank2", default="Tank2.fmu")
    ap.add_argument("--stop-time", type=float, default=300.0)
    ap.add_argument("--dt", type=float, default=1.0)

    # speeds in m/s (level change rates)
    ap.add_argument("--p1_fill", type=float, default=4.0)     # v1 -> tank1 in
    ap.add_argument("--p2_trans", type=float, default=5.0)    # v2 -> transfer tank1->tank2
    ap.add_argument("--p3_drain", type=float, default=6.0)    # v3 -> tank2 out (slightly different)

    ap.add_argument("--out", default="TwoTanks.csv")
    args = ap.parse_args()

    tank1, vr1, dir1 = instantiate_cs(args.tank1, "tank1")
    tank2, vr2, dir2 = instantiate_cs(args.tank2, "tank2")

    # value references
    t1_qin, t1_qout, t1_level = vr1["q_in"], vr1["q_out"], vr1["level"]
    t2_qin, t2_qout, t2_level = vr2["q_in"], vr2["q_out"], vr2["level"]

    # known heights (same as in FMUs)
    H1 = 500.0
    H2 = 2000.0

    rows = []
    t = 0.0

    # sample initial
    L1 = tank1.getReal([t1_level])[0]
    L2 = tank2.getReal([t2_level])[0]
    rows.append([t, L1, L2, 0, 0, 0, 0.0])

    try:
        while t < args.stop_time:
            v1, v2, v3 = valve_schedule(t)

            # current levels at time t
            L1 = tank1.getReal([t1_level])[0]
            L2 = tank2.getReal([t2_level])[0]

            # commanded flows (m/s)
            q_in1 = v1 * args.p1_fill
            q_drain2 = v3 * args.p3_drain
            q_trans_desired = v2 * args.p2_trans

            # cap transfer so tank1 doesn't go <0 and tank2 doesn't exceed its height
            dt = args.dt

            # allow inflow and outflow within same macro step
            max_from_t1 = max(0.0, (L1 + q_in1 * dt) / dt)
            max_into_t2 = max(0.0, (H2 - L2 + q_drain2 * dt) / dt)

            q_trans = min(q_trans_desired, max_from_t1, max_into_t2)

            # set inputs
            tank1.setReal([t1_qin, t1_qout], [q_in1, q_trans])
            tank2.setReal([t2_qin, t2_qout], [q_trans, q_drain2])

            # advance both FMUs
            tank1.doStep(currentCommunicationPoint=t, communicationStepSize=dt)
            tank2.doStep(currentCommunicationPoint=t, communicationStepSize=dt)

            t += dt

            # sample after step
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

    # write CSV
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["time", "tank1_level", "tank2_level", "v1_cmd", "v2_cmd", "v3_cmd", "q_transfer"])
        w.writerows(rows)

    print(f"Saved {args.out}")


if __name__ == "__main__":
    main()
