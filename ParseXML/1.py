import xmltodict, pprint

# ---------- 0)  list the rebecs to ignore ------------------------------------
IGNORE_REBECS = {"meaningless"}       # ← EDIT THIS SET AS NEEDED

# ---------- helpers -----------------------------------------------------------
def as_list(x):
    if x is None:
        return []
    return x if isinstance(x, list) else [x]

def to_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return v          # 'null', 'infinity', …

def massage_res(v):
    return None if v in ("null", None) else to_int(v)

def parse_msg(rname, m):
    return {
        "sid":  m["@sender"],
        "rid":  rname,
        "body": m["#text"].strip(),
        "ar":   to_int(m["@arrival"]),
        "dl":   to_int(m["@deadline"]),
    }

# ---------- 1)  read once -----------------------------------------------------
with open("maw.xml") as f:
    ts = xmltodict.parse(f.read())["transitionsystem"]

# ---------- 2)  build shift_state --------------------------------------------
shift_state = {}

for st in as_list(ts["state"]):
    sid = st["@id"]

    # -- pick a rebec we do *not* ignore to read the global clock -------------
    visible = [r for r in as_list(st.get("rebec"))
               if r["@name"] not in IGNORE_REBECS]
    if not visible:           # state contains only ignored rebecs → drop it
        continue

    global_now = to_int(visible[0].get("now"))
    state_rec  = {"now": global_now, "rebecs": {}}

    for rebec in as_list(st.get("rebec")):
        rname = rebec["@name"]
        if rname in IGNORE_REBECS:
            continue                         # ← skip everything about it

        # ---- local variables V_r -------------------------------------------
        var_block = (rebec.get("statevariables") or {})
        vars_map  = {v["@name"]: v["#text"]
                     for v in as_list(var_block.get("variable"))}

        # ---- scheduler data -------------------------------------------------
        pc  = rebec.get("pc")
        res = massage_res(rebec.get("res"))

        # ---- bag / queue B_r  ----------------------------------------------
        q    = rebec.get("queue")
        msgs = [parse_msg(rname, m) for m in as_list(q.get("message"))] if q else []

        # ---- final per-rebec record -----------------------------------------
        state_rec["rebecs"][rname] = {
            "pc": pc,
            "res": res,
            "vars": vars_map,
            "bag_size": len(msgs),
            "messages": msgs,
        }

    shift_state[sid] = state_rec

# ---------- 3)  demo ----------------------------------------------------------
example_sid = next(iter(shift_state))
print(f"\n=== Canonical record for state {example_sid} ===")
pprint.pp(shift_state[example_sid])

print("\nController.sensedValue in state 1_0 →",
      shift_state["1_0"]["rebecs"]["controller"]["vars"]["Controller.sensedValue"])

# ---------------------------------------------------------------------------
# 0)  Accept the "shift_state" map built in the previous step
#     shift_state[sid] = { "now": int, "rebecs": { … } }
#     (import or paste it here, or pass it in from another module)
# ---------------------------------------------------------------------------
# from build_shift_state import shift_state             # ← typical import
# (for this demo I assume shift_state already exists)

from collections import Counter, defaultdict

# ---------------------------------------------------------------------------
# helpers --------------------------------------------------------------------
def add_delta(value, delta):
    """Return value + Δ  (handles None / 'infinity')."""
    if value in (None, "infinity"):
        return value
    return value + delta                # they are stored as int already

def msgs_to_multiset(msgs, delta=0):
    """
    Convert a list of message dicts to a Counter that can be compared as a bag.
    If delta!=0, shift ar & dl by delta first.
    """
    def key(m):
        return (
            m["sid"],
            m["rid"],
            m["body"],
            add_delta(m["ar"], delta),
            add_delta(m["dl"], delta),
        )
    return Counter(key(m) for m in msgs)

def res_equal(ra, rb, delta):
    """Check Definition-5 condition on 'res'."""
    if ra is None and rb is None:
        return True
    if ra is None or rb is None:
        return False
    return ra == rb + delta

# ---------------------------------------------------------------------------
# core predicate -------------------------------------------------------------
def shift_equivalent(stateA, stateB):
    """
    Return (True, Δ)  if the two states are shift-equivalent, else (False, None).
    Uses the *symmetric* version of the definition (Δ ≥ 0).
    """
    delta = stateA["now"] - stateB["now"]
    if delta < 0:                       # always ensure Δ ≥ 0
        is_eq, d = shift_equivalent(stateB, stateA)
        return is_eq, d

    # 1) now_s = now_t + Δ
    if stateA["now"] != stateB["now"] + delta:
        return False, None

    rebecsA = stateA["rebecs"]
    rebecsB = stateB["rebecs"]

    if set(rebecsA) != set(rebecsB):
        return False, None              # different actor sets

    for r in rebecsA:
        RA = rebecsA[r]
        RB = rebecsB[r]

        # 2)  equal variables, pc, bag size,   res differs by Δ
        if RA["vars"] != RB["vars"]:
            return False, None
        if RA["pc"] != RB["pc"]:
            return False, None
        if RA["bag_size"] != RB["bag_size"]:
            return False, None
        if not res_equal(RA["res"], RB["res"], delta):
            return False, None

        # 3)  bag-wise message matching with ar/dl shifted by Δ
        bagA = msgs_to_multiset(RA["messages"])
        bagB_shifted = msgs_to_multiset(RB["messages"], delta)
        if bagA != bagB_shifted:
            return False, None

    return True, delta

# ---------------------------------------------------------------------------
# 4)  Build merge-classes -----------------------------------------------------
classes = []                       # list[list[state-ids]]
assigned = set()

state_ids = list(shift_state.keys())
for i, sid in enumerate(state_ids):
    if sid in assigned:
        continue
    cls = [sid]                    # start a new equivalence class
    assigned.add(sid)

    for sid2 in state_ids[i + 1 :]:
        if sid2 in assigned:
            continue
        eq, Δ = shift_equivalent(shift_state[sid], shift_state[sid2])
        if eq:
            cls.append(f"{sid2}  (Δ={Δ})")
            assigned.add(sid2)

    classes.append(cls)

# ---------------------------------------------------------------------------
# 5)  Pretty-print the result -------------------------------------------------
print("\n=== Shift-equivalence classes ===")
for idx, cls in enumerate(classes, 1):
    print(f"Class {idx}: ", ", ".join(cls))