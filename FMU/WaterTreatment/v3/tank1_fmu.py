#!/usr/bin/env python3
import re
import argparse
from collections import defaultdict
from typing import Optional, Set, Dict, List, Tuple

# ---------------- DOT parse ----------------

def parse_dot(dot_text: str):
    subgraph_start = re.compile(r'^\s*subgraph\s+"([^"]+)"\s*\{')
    subgraph_end   = re.compile(r'^\s*\}')
    edge_re        = re.compile(r'^\s*"([^"]+)"\s*->\s*"([^"]+)"\s*(\[(.*?)\])?')
    node_re        = re.compile(r'^\s*"([^"]+)"\s*\[(.*?)\]\s*;?$')
    label_attr     = re.compile(r'label\s*=\s*(.+)')

    nodes = {}      # nid -> {"label": ..., "cluster": ...}
    edges = []      # (src, dst, attrs)
    clusters = {}   # cname -> {"attrs":[], "nodes":[]}
    header = []

    incoming_all = defaultdict(list)  # dst -> list[(src, attrs)]
    outgoing_all = defaultdict(list)  # src -> list[(dst, attrs)]

    stack = []
    digraph_name = None

    for line in dot_text.splitlines():
        stripped = line.strip()

        if digraph_name is None:
            m = re.match(r'^\s*digraph\s+"([^"]+)"\s*\{', line)
            if m:
                digraph_name = m.group(1)
                continue

        m = subgraph_start.match(line)
        if m:
            cname = m.group(1)
            stack.append(cname)
            clusters.setdefault(cname, {"attrs": [], "nodes": []})
            continue

        if subgraph_end.match(line):
            if stack:
                stack.pop()
            continue

        m = edge_re.match(line)
        if m:
            src, dst = m.group(1), m.group(2)
            attrs = m.group(4)  # may be None
            edges.append((src, dst, attrs))
            outgoing_all[src].append((dst, attrs))
            incoming_all[dst].append((src, attrs))
            continue

        m = node_re.match(line)
        if m:
            nid = m.group(1)
            attrs = m.group(2)
            m2 = label_attr.search(attrs)
            lbl = m2.group(1).strip() if m2 else None

            cluster = stack[-1] if stack else None
            if nid not in nodes:
                nodes[nid] = {"label": lbl, "cluster": cluster}
            else:
                if lbl:
                    nodes[nid]["label"] = lbl
                if nodes[nid].get("cluster") is None and cluster is not None:
                    nodes[nid]["cluster"] = cluster

            if cluster is not None:
                clusters[cluster]["nodes"].append(nid)
            continue

        # header/attrs
        if stack:
            if stripped and stripped not in ("{", "}"):
                clusters[stack[-1]]["attrs"].append(stripped.rstrip(";"))
        else:
            if stripped and stripped not in ("{", "}"):
                header.append(stripped.rstrip(";"))

    return {
        "name": digraph_name or "G",
        "header": header,
        "nodes": nodes,
        "edges": edges,
        "incoming_all": dict(incoming_all),
        "outgoing_all": dict(outgoing_all),
        "clusters": clusters,
    }

def edge_label(attrs: Optional[str]) -> Optional[str]:
    if not attrs:
        return None
    m = re.search(r'label\s*=\s*"([^"]+)"', attrs)
    return m.group(1) if m else None

def adj_by_label(pairs_map, wanted: Optional[Set[str]]):
    """
    pairs_map is incoming_all or outgoing_all.
    Returns adjacency with filtering by label.
    If wanted is None => accept all edges.
    """
    out = defaultdict(list)
    for k, lst in pairs_map.items():
        for (other, attrs) in lst:
            if wanted is None:
                out[k].append(other)
            else:
                lab = edge_label(attrs)
                if lab in wanted:
                    out[k].append(other)
    return dict(out)

# ---------------- find observables (method roots) ----------------

def is_method_root_label(lbl: Optional[str]) -> bool:
    # In your graph, method roots are typically a single-line "Owner.method(args)" label
    # while statements usually contain <BR/>.
    if not lbl:
        return False
    low = lbl.lower()
    if "<br" in low:
        return False
    return ("." in lbl) and ("(" in lbl) and (")" in lbl)

def find_observable_roots(nodes, fnames: List[str]) -> Set[str]:
    want = [f.lower() for f in fnames]
    out = set()
    for nid, data in nodes.items():
        lbl = data.get("label")
        if not lbl:
            continue
        low = lbl.lower()
        for f in want:
            if "." + f + "(" in low:
                out.add(nid)
                break
    return out

# ---------------- body via ctrl-dep closure ----------------

def ctrl_body_closure(roots: Set[str], out_ctrl: Dict[str, List[str]]) -> Set[str]:
    """Forward reachability over ctrl-dep edges."""
    seen = set()
    stack = list(roots)
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        for succ in out_ctrl.get(n, []):
            if succ not in seen:
                stack.append(succ)
    return seen

# ---------------- slice: reverse ALL + forward act-dep + include callee body ----------------

def compute_slice(parsed,
                  observable_roots: Set[str],
                  in_all: Dict[str, List[str]],
                  out_act: Dict[str, List[str]],
                  out_ctrl: Dict[str, List[str]]) -> Set[str]:

    keep = set()

    # seed: observable roots + their ctrl-body
    seed_body = ctrl_body_closure(observable_roots, out_ctrl)
    keep |= seed_body

    work = list(seed_body)

    while work:
        n = work.pop()

        # reverse: add all predecessors (you can restrict if you want)
        for pred in in_all.get(n, []):
            if pred not in keep:
                keep.add(pred)
                work.append(pred)

        # forward: follow act-dep edges to callees
        for callee_root in out_act.get(n, []):
            if callee_root not in keep:
                keep.add(callee_root)
                work.append(callee_root)

            # if it looks like a method root, include its ctrl-body too
            lbl = parsed["nodes"].get(callee_root, {}).get("label")
            if is_method_root_label(lbl):
                body = ctrl_body_closure({callee_root}, out_ctrl)
                for bn in body:
                    if bn not in keep:
                        keep.add(bn)
                        work.append(bn)

    return keep

# ---------------- emit DOT (pruned) ----------------

def build_label_part(lbl: Optional[str]) -> str:
    if lbl is None:
        return 'label = ""'
    if lbl.lstrip().startswith("<"):
        return "label = " + lbl
    safe = lbl.replace('"', '\\"')
    return f'label = "{safe}"'

def render_node(nid: str, nodes, color: Optional[str], indent="  ") -> str:
    lbl = nodes.get(nid, {}).get("label")
    label_part = build_label_part(lbl)
    style = f' style=filled fillcolor="{color}"' if color else ""
    return f'{indent}"{nid}" [{label_part}{style} ];'

def render_edge(src: str, dst: str, attrs: Optional[str], indent="  ") -> str:
    if attrs and attrs.strip():
        return f'{indent}"{src}" -> "{dst}" [{attrs}];'
    return f'{indent}"{src}" -> "{dst}";'

def emit_dot(parsed, red_roots: Set[str], slice_nodes: Set[str], out_path: str, prune: bool):
    # blue = slice, red overrides
    color_map = {n: "lightblue" for n in slice_nodes}
    for r in red_roots:
        color_map[r] = "red"

    keep_nodes = set(parsed["nodes"].keys()) if not prune else set(slice_nodes)

    keep_edges = []
    for (s, d, a) in parsed["edges"]:
        if s in keep_nodes and d in keep_nodes:
            keep_edges.append((s, d, a))

    with open(out_path, "w") as f:
        f.write(f'digraph "{parsed["name"]}" {{\n')
        for h in parsed["header"]:
            if h:
                f.write(f"  {h};\n")

        # clusters
        for cname, cinfo in parsed["clusters"].items():
            kept_cluster_nodes = [n for n in cinfo["nodes"] if n in keep_nodes]
            if not kept_cluster_nodes:
                continue
            f.write(f'  subgraph "{cname}" {{\n')
            for a in cinfo["attrs"]:
                f.write(f"    {a};\n")
            for nid in kept_cluster_nodes:
                f.write(render_node(nid, parsed["nodes"], color_map.get(nid), indent="    ") + "\n")
            f.write("  }\n")

        # nodes not in clusters
        clustered = set()
        for cinfo in parsed["clusters"].values():
            for nid in cinfo["nodes"]:
                if nid in keep_nodes:
                    clustered.add(nid)

        for nid in sorted(keep_nodes):
            if nid not in clustered:
                f.write(render_node(nid, parsed["nodes"], color_map.get(nid), indent="  ") + "\n")

        # edges
        for (s, d, a) in keep_edges:
            f.write(render_edge(s, d, a, indent="  ") + "\n")

        f.write("}\n")

# ---------------- main ----------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_dot")
    ap.add_argument("output_dot")
    ap.add_argument("--func", default="activateh,switchoff,activateh",
                    help="comma-separated observable method names")
    ap.add_argument("--no-prune", action="store_true",
                    help="keep full graph; only color slice nodes")
    args = ap.parse_args()

    funcs = [x.strip() for x in args.func.split(",") if x.strip()]

    with open(args.input_dot, "r") as f:
        parsed = parse_dot(f.read())

    # Build adjacencies we need
    incoming_all = adj_by_label(parsed["incoming_all"], wanted=None)                 # reverse over all
    outgoing_act = adj_by_label(parsed["outgoing_all"], wanted={"act dep"})          # forward calls
    outgoing_ctrl= adj_by_label(parsed["outgoing_all"], wanted={"ctrl dep"})         # method body

    red = find_observable_roots(parsed["nodes"], funcs)
    slice_nodes = compute_slice(parsed, red, incoming_all, outgoing_act, outgoing_ctrl)

    emit_dot(parsed, red_roots=red, slice_nodes=slice_nodes,
             out_path=args.output_dot, prune=(not args.no_prune))

    print(f"Wrote: {args.output_dot}")
    print(f"RED observables: {len(red)}")
    print(f"Slice nodes:     {len(slice_nodes)}")

if __name__ == "__main__":
    main()
