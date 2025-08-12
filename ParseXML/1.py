import xmltodict, pprint

# ---------- 0)  list the rebecs to ignore ------------------------------------
IGNORE_REBECS = {"meaningless"}       # ← EDIT THIS SET AS NEEDED

# ---------- helpers -----------------------------------------------------------
import xmltodict, pathlib

def write_transitionsystem_xml(ts_dict: dict, out_path: str | pathlib.Path):
    xml_body = xmltodict.unparse(
        ts_dict,
        pretty=True,
        short_empty_elements=False          # ← ■■■ change this line ■■■
    )

    xml_full = '<?xml version="1.0" encoding="utf-8"?>\n' + xml_body
    out_path = pathlib.Path(out_path)
    out_path.write_text(xml_full, encoding="utf-8")
    print(f"✓  Wrote {out_path} ({out_path.stat().st_size} bytes)")
import re, xmltodict, pathlib

def write_like_original(ts_dict, out_path):
    xml = xmltodict.unparse(
        ts_dict,
        pretty=True,
        short_empty_elements=False
    )

    # A) turn  <time …></time>  into  <time …/>
    xml = re.sub(
        r'<time([^/>]*)></time>',
        r'<time\1/>',
        xml,
        flags=re.DOTALL
    )

    # B) self-close <messageserver> too
    xml = re.sub(
        r'<messageserver([^/>]*)></messageserver>',
        r'<messageserver\1/>',
        xml,
        flags=re.DOTALL
    )

    # C) put transition+messageserver on one line
    xml = re.sub(
        r'<transition([^>]*)>\s*<messageserver([^/>]*)/>\s*</transition>',
        r'<transition\1> <messageserver\2/></transition>',
        xml,
        flags=re.DOTALL
    )

    # D) put transition+time on one line
    xml = re.sub(
        r'<transition([^>]*)>\s*<time([^/>]*)/>\s*</transition>',
        r'<transition\1> <time\2/></transition>',
        xml,
        flags=re.DOTALL
    )

    # E) single XML declaration
    if not xml.lstrip().startswith('<?xml'):
        xml = '<?xml version="1.0" encoding="utf-8"?>\n' + xml

    pathlib.Path(out_path).write_text(xml, encoding="utf-8")
    #print("✓ wrote", out_path)





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


import copy
from collections import OrderedDict

def merge_shift_equivalent(ts_root, classes):
    """
    Return a *new* transitionsystem dictionary in which every class of
    shift-equivalent states (Definition 5/6) has been collapsed to a single
    representative state, and the transitions are rewired and deduplicated.

    ── parameters ───────────────────────────────────────────────────────────
    ts_root  – the parsed xmltodict tree (the original `transitionsystem`)
    classes  – list[list[state-ids]] exactly as produced in step 4 above

    ── returns ──────────────────────────────────────────────────────────────
    new_ts   – a deep-copy of `ts_root` with «state» and «transition» rebuilt
    """

    # ---------------------------------------------------- 6.1 build rep_map
    # Each state id  →  its class representative (we take the *first* id)
    rep_map = {}
    for cls in classes:
        rep = cls[0].split()[0]        # drop the “ (Δ=…)” suffix, if present
        for sid in cls:
            rep_map[sid.split()[0]] = rep

    # pprint.pprint(rep_map)
    # print("*****************ts_root***********************")
    # pprint.pprint(ts_root)
    # print("*****************ts_root***********************")
    # ---------------------------------------------------- 6.2 clone the root
    new_ts = copy.deepcopy(ts_root)

    # ---------------------------------------------------- 6.3 rebuild <state>
    id_to_state = {st["@id"]: st for st in as_list(ts_root["state"])}
    merged_states = OrderedDict()      # keep insertion order

    for sid, rep in rep_map.items():
        if rep != sid:
            continue                   # keep only *one* state per class
        merged_states[rep] = id_to_state[sid]

    new_ts["state"] = list(merged_states.values())

    def get_node_id(node):
        if node is None:
            return None
        if isinstance(node, str):
            return node.strip()
        return node.get("@ref") or node.get("#text")

    uniq = {}
    for tr in as_list(ts_root["transition"]):

        # ── ✂ NEW: skip if the message touches an ignored rebec ────────────
        ms = tr.get("messageserver")
        if ms:
            sender = ms.get("@sender") or get_node_id(ms.get("sender"))
            owner  = ms.get("@owner")  or get_node_id(ms.get("owner"))
            if sender in IGNORE_REBECS or owner in IGNORE_REBECS:
                # silently drop the edge
                continue
        # ───────────────────────────────────────────────────────────────────

        # 1)  source / target / destination
        src = (tr.get("@source")                       or
               get_node_id(tr.get("source")))
        tgt = (tr.get("@target") or tr.get("@destination")     or
               get_node_id(tr.get("target")) or get_node_id(tr.get("destination")))

        # 2)  label / title
        lbl = (tr.get("@label") or tr.get("@title") or
               get_node_id(tr.get("label")) or get_node_id(tr.get("title")) or "")

        if src is None or tgt is None:
            print("⚠  skipping transition with missing source/target:", tr)
            continue

        src_rep, tgt_rep = rep_map[src], rep_map[tgt]
        key = (src_rep, tgt_rep, lbl)
        if key in uniq:
            continue

        new_tr = copy.deepcopy(tr)
        # patch @source / @target / @destination exactly as before …
        # (rest of the original code is unchanged)

        # a) source
        if "@source" in new_tr:
            new_tr["@source"] = src_rep
        elif "source" in new_tr:
            new_tr["source"]["@ref" if "@ref" in new_tr["source"] else "#text"] = src_rep

        # b) *either* target or destination
        if "@target" in new_tr:
            new_tr["@target"] = tgt_rep
        elif "@destination" in new_tr:
            new_tr["@destination"] = tgt_rep
        elif "target" in new_tr:
            new_tr["target"]["@ref" if "@ref" in new_tr["target"] else "#text"] = tgt_rep
        elif "destination" in new_tr:
            new_tr["destination"]["@ref" if "@ref" in new_tr["destination"] else "#text"] = tgt_rep

        uniq[key] = new_tr

    new_ts["transition"] = list(uniq.values())
    # Assuming new_ts['transition'] is your list of transitions
    # Assuming new_ts['state'] and new_ts['transition'] are the original state and transition lists
    states = new_ts['state']
    transitions = new_ts['transition']

    # Create a set to hold unique states (source + destination)
    unique_states = set()

    # Iterate through all transitions and add source and destination to the set
    for transition in transitions:
        source_state = transition.get('@source')
        destination_state = transition.get('@destination')
        
        # Add source and destination states to the set
        if source_state:
            unique_states.add(source_state)
        if destination_state:
            unique_states.add(destination_state)

    # Sort the states to ensure they are in a consistent order (optional)
    sorted_states = sorted(unique_states)

    # Create a mapping from the original state IDs to new numbers in the format '1_0', '2_0', etc.
    state_mapping = {state: f"{i + 1}_0" for i, state in enumerate(sorted_states)}

    # Now, update the states section with the new state numbers in the '1_0' format
    for state in states:
        old_state_id = state.get('@id')
        if old_state_id in state_mapping:
            state['@id'] = state_mapping[old_state_id]

    # Update transitions with the new state numbers in the '1_0' format
    for transition in transitions:
        if '@source' in transition:
            transition['@source'] = state_mapping.get(transition['@source'], transition['@source'])
        if '@destination' in transition:
            transition['@destination'] = state_mapping.get(transition['@destination'], transition['@destination'])

    # Print the updated state section
    print("**************** Updated states ****************")
    pprint.pprint(states)
    print("**************** End of updated states ****************")

    # Print the updated transitions section
    print("**************** Updated transitions ****************")
    pprint.pprint(transitions)
    print("**************** End of updated transitions ****************")
    new_ts['state']=states
    new_ts['transition']=transitions


        # ---- call it ---------------------------------------------------------------
    # If new_ts already has the right wrapper, just hand it in directly
    # for st in new_ts["state"]:
        #  st["rebec"] = [r for r in as_list(st["rebec"]) if r["@name"] != "meaningless"]

     
    write_like_original({"transitionsystem": new_ts}, "maw_shift_merged.xml")

   

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
# example_sid = next(iter(shift_state))
# print(f"\n=== Canonical record for state {example_sid} ===")
# pprint.pp(shift_state[example_sid])

# print("\nController.sensedValue in state 1_0 →",
#       shift_state["1_0"]["rebecs"]["controller"]["vars"]["Controller.sensedValue"])

# ---------------------------------------------------------------------------
# 0)  Accept the "shift_state" map built in the previous step
#     shift_state[sid] = { "now": int, "rebecs": { … } }
#     (import or paste it here, or pass it in from another module)
# ---------------------------------------------------------------------------
# from build_shift_state import shift_state             # ← typical import
# (for this demo I assume shift_state already exists)
# ---------------------------------------------------------------------------
# 7)  example usage – write the merged TS back to XML
# ---------------------------------------------------------------------------
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
# # 5)  Pretty-print the result -------------------------------------------------
# print("\n=== Shift-equivalence classes ===")
# for idx, cls in enumerate(classes, 1):
#     print(f"Class {idx}: ", ", ".join(cls))
    # ---------------------------------------------------------------------------
# 6)  Collapse shift-equivalent states and rebuild the transition system
# ---------------------------------------------------------------------------
from xml.dom import minidom
import xmltodict, pathlib

merged_ts = merge_shift_equivalent(ts, classes)

# xml_str = xmltodict.unparse({"transitionsystem": merged_ts},
#                             pretty=True, short_empty_elements=True)
# out_path = pathlib.Path("maw_shift_merged.xml")
# out_path.write_text(xml_str, encoding="utf-8")

# ---------------------------------------------------------------------------
# 8)  dump `new_ts` to maw_shift_merged.xml
# ---------------------------------------------------------------------------
