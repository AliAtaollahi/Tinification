import re
import sys
from collections import defaultdict, deque

###############################################################################
# 1. Parse DOT to get nodes, edges, cluster info
###############################################################################

def parse_dot(dot_text):
    subgraph_start_re = re.compile(r'^\s*subgraph\s+"([^"]+)"\s*\{')
    subgraph_end_re   = re.compile(r'^\s*\}')
    edge_re           = re.compile(r'^\s*"([^"]+)"\s*->\s*"([^"]+)"\s*(\[(.*?)\])?')
    node_re           = re.compile(r'^\s*"([^"]+)"\s*\[(.*?)\]\s*;?$')
    label_attr_re     = re.compile(r'label\s*=\s*(.+)')

    nodes     = {}  # node_id -> {"label": raw_label_text, "cluster": cluster_name or None}
    edges     = []  # list of (src, dst, attr_block)
    incoming  = defaultdict(list)
    outgoing  = defaultdict(list)
    clusters  = {} # cluster_name -> {"attrs":[], "nodes":[]}

    cluster_stack = []

    digraph_name = None
    header_lines = []

    seen_real_content = False
    lines = dot_text.splitlines()

    for line in lines:
        stripped = line.strip()

        # digraph start
        if digraph_name is None:
            m = re.match(r'^\s*digraph\s+"([^"]+)"\s*\{', line)
            if m:
                digraph_name = m.group(1)
                continue

        # subgraph start?
        m = subgraph_start_re.match(line)
        if m:
            seen_real_content = True
            cname = m.group(1)
            cluster_stack.append(cname)
            if cname not in clusters:
                clusters[cname] = {"attrs": [], "nodes": []}
            continue

        # subgraph end?
        if subgraph_end_re.match(line):
            if cluster_stack:
                cluster_stack.pop()
            continue

        # edge line?
        m = edge_re.match(line)
        if m:
            seen_real_content = True
            src, dst = m.group(1), m.group(2)
            attr_block = m.group(4)  # may be None
            edges.append((src, dst, attr_block))
            outgoing[src].append(dst)
            incoming[dst].append(src)
            continue

        # node line?
        m = node_re.match(line)
        if m:
            seen_real_content = True
            nid = m.group(1)
            attr_block = m.group(2)

            label_match = label_attr_re.search(attr_block)
            if label_match:
                raw_label = label_match.group(1).strip()
            else:
                raw_label = None

            cluster_name = cluster_stack[-1] if cluster_stack else None
            if nid not in nodes:
                nodes[nid] = {"label": raw_label, "cluster": cluster_name}
            else:
                nodes[nid]["label"] = raw_label
                if nodes[nid]["cluster"] is None and cluster_name is not None:
                    nodes[nid]["cluster"] = cluster_name

            if cluster_name is not None:
                clusters[cluster_name]["nodes"].append(nid)

            continue

        # cluster/global attrs or header lines
        if cluster_stack:
            if stripped and stripped != "{":
                clusters[cluster_stack[-1]]["attrs"].append(stripped.rstrip(";"))
        else:
            if (not seen_real_content and stripped and stripped != "{"):
                header_lines.append(stripped.rstrip(";"))
            else:
                if stripped and stripped not in ("{", "}"):
                    header_lines.append(stripped.rstrip(";"))

    return {
        "digraph_name": digraph_name or "G",
        "header_lines": header_lines,
        "nodes": nodes,
        "edges": edges,
        "incoming": dict(incoming),
        "outgoing": dict(outgoing),
        "clusters": clusters,
    }

###############################################################################
# 2. Find prototype nodes for the functions of interest
#    Heuristic: node label contains ".funcName(" (case-insensitive)
###############################################################################

def find_prototypes(nodes, func_names):
    prototypes = set()
    for nid, data in nodes.items():
        label = data["label"]
        if not label:
            continue
        low = label.lower()
        for f in func_names:
            if "." + f.lower() + "(" in low:
                prototypes.add(nid)
                break
    return prototypes

###############################################################################
# 3. Helper: function prefix for body nodes
#    Node IDs in your graph look like:
#      HVAC_HC_Unit__HVAC_HC_Unit_activateh_void___107374182409  (prototype)
#      HVAC_HC_Unit__HVAC_HC_Unit_activateh_void___30064771138   (body stmt)
#    They share the prefix up to the last "___<digits>".
###############################################################################

def function_prefix(node_id):
    m = re.match(r'^(.*?___)\d+$', node_id)
    if m:
        return m.group(1)
    # fallback
    parts = node_id.rsplit("___", 1)
    if len(parts) == 2:
        return parts[0] + "___"
    return node_id + "___"

def get_body_nodes(nodes, proto_id):
    pref = function_prefix(proto_id)
    body = [nid for nid in nodes.keys() if nid.startswith(pref)]
    return body

###############################################################################
# 4. Find callsite nodes inside each prototype's body.
#    We want those "regulate, 113 this.room.regulate(0)" style nodes to be blue.
#    Heuristic for "call":
#      - node is in the same function body as the prototype
#      - node != prototype node
#      - label contains "(" and ")"
#      - label does NOT start with "<&lt;operator&gt;" (to skip assignments)
###############################################################################

def is_callsite_node(label):
    if not label:
        return False
    low = label.lower().lstrip()
    if low.startswith("<&lt;operator"):
        return False
    return "(" in label and ")" in label

def find_callsites_for_proto(nodes, proto_id):
    body_nodes = get_body_nodes(nodes, proto_id)
    callsites = []
    for nid in body_nodes:
        if nid == proto_id:
            continue
        label = nodes[nid]["label"]
        if is_callsite_node(label):
            callsites.append(nid)
    return callsites

def collect_all_callsites(nodes, prototypes):
    blue_starts = set()
    for p in prototypes:
        callsites = find_callsites_for_proto(nodes, p)
        blue_starts.update(callsites)
    return blue_starts

###############################################################################
# 5. Reverse DFS from a set of start nodes, following incoming edges.
#    This gives us "all dependencies" for those starts.
###############################################################################

def reverse_dfs_multi(starts, incoming):
    visited = set()
    stack = list(starts)
    while stack:
        n = stack.pop()
        if n in visited:
            continue
        visited.add(n)
        for pred in incoming.get(n, []):
            if pred not in visited:
                stack.append(pred)
    return visited

###############################################################################
# 6. Emit DOT with color:
#    - prototypes (target functions): red
#    - callsites + their reverse-DFS ancestors: blue
#    Red wins if overlap.
###############################################################################

def emit_colored_dot(parsed, red_nodes, blue_nodes, out_file):
    digraph_name = parsed["digraph_name"]
    header_lines = parsed["header_lines"]
    nodes        = parsed["nodes"]
    edges        = parsed["edges"]
    clusters     = parsed["clusters"]

    node_colors = {}
    for n in blue_nodes:
        node_colors[n] = "lightblue"
    for n in red_nodes:
        node_colors[n] = "red"  # override blue if conflict

    def render_node(nid, indent="    "):
        data = nodes[nid]
        label = data["label"] if data["label"] is not None else "\"\""

        # preserve HTML-like labels <...> / <<...>>
        if label.lstrip().startswith("<"):
            label_part = f"label = {label}"
        else:
            safe_label = label.replace('"', '\\"')
            label_part = f'label = "{safe_label}"'

        color_attr = ""
        if nid in node_colors:
            color_attr = f' style=filled fillcolor="{node_colors[nid]}"'

        return f'{indent}"{nid}" [{label_part}{color_attr} ];'

    def render_edge(src, dst, attrs, indent="    "):
        if attrs and attrs.strip():
            return f'{indent}"{src}" -> "{dst}" [{attrs}];'
        else:
            return f'{indent}"{src}" -> "{dst}";'

    with open(out_file, "w") as f:
        f.write(f'digraph "{digraph_name}" {{\n')

        # global/header attrs
        for hl in header_lines:
            if hl in ("{", "}", ""):
                continue
            f.write(f"  {hl};\n")

        # clusters first
        for cname, cinfo in clusters.items():
            f.write(f'  subgraph "{cname}" {{\n')
            for attr in cinfo["attrs"]:
                f.write(f"    {attr};\n")
            for nid in cinfo["nodes"]:
                if nid in nodes:
                    f.write(render_node(nid, indent="    ") + "\n")
            f.write("  }\n")

        # any non-clustered nodes
        clustered_nodes = set()
        for cinfo in clusters.values():
            clustered_nodes.update(cinfo["nodes"])

        for nid in nodes:
            if nid not in clustered_nodes:
                f.write(render_node(nid, indent="  ") + "\n")

        # edges
        for (src, dst, attrs) in edges:
            f.write(render_edge(src, dst, attrs, indent="  ") + "\n")

        f.write("}\n")

###############################################################################
# 7. Main
###############################################################################

def main(in_path, out_path, functions_of_interest):
    with open(in_path, "r") as fin:
        dot_text = fin.read()

    parsed = parse_dot(dot_text)

    # STEP A: prototypes (functions of interest)
    proto_nodes = find_prototypes(parsed["nodes"], functions_of_interest)

    # STEP B: callsite nodes inside those prototypes
    blue_start_nodes = collect_all_callsites(parsed["nodes"], proto_nodes)

    # STEP C: reverse DFS from blue_start_nodes
    blue_closure = reverse_dfs_multi(blue_start_nodes, parsed["incoming"])

    # Final coloring sets
    red_nodes  = set(proto_nodes)
    blue_nodes = set(blue_closure)

    emit_colored_dot(parsed, red_nodes, blue_nodes, out_path)

    print("Prototypes (red):", len(red_nodes), red_nodes)
    print("Initial callsites (blue seeds):", len(blue_start_nodes), blue_start_nodes)
    print("Blue closure size:", len(blue_nodes))

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python color_slice_cfg.py <input.dot> <output_colored.dot>")
        sys.exit(1)

    in_path = sys.argv[1]
    out_path = sys.argv[2]

    FUNCTIONS_OF_INTEREST = ["getSense", "activateh", "switchoff"]

    main(in_path, out_path, FUNCTIONS_OF_INTEREST)
