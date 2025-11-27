import re
import sys
from collections import defaultdict

###############################################################################
# Parse DOT into structures we can work with
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

        # subgraph start
        m = subgraph_start_re.match(line)
        if m:
            seen_real_content = True
            cname = m.group(1)
            cluster_stack.append(cname)
            if cname not in clusters:
                clusters[cname] = {"attrs": [], "nodes": []}
            continue

        # subgraph end
        if subgraph_end_re.match(line):
            if cluster_stack:
                cluster_stack.pop()
            continue

        # edge
        m = edge_re.match(line)
        if m:
            seen_real_content = True
            src, dst = m.group(1), m.group(2)
            attr_block = m.group(4)  # may be None
            edges.append((src, dst, attr_block))
            outgoing[src].append(dst)
            incoming[dst].append(src)
            continue

        # node
        m = node_re.match(line)
        if m:
            seen_real_content = True
            nid = m.group(1)
            attr_block = m.group(2)

            label_m = label_attr_re.search(attr_block)
            if label_m:
                raw_label = label_m.group(1).strip()
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

        # attrs/header
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
# Identify prototype nodes = the entry node of interesting functions
# We consider a node a prototype if its label contains ".funcName("
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
# Figure out which nodes are "inside" a function body.
# Node ids in your graph share a prefix before the last ___<id> part.
###############################################################################

def function_prefix(node_id):
    m = re.match(r'^(.*?___)\d+$', node_id)
    if m:
        return m.group(1)
    parts = node_id.rsplit("___", 1)
    if len(parts) == 2:
        return parts[0] + "___"
    return node_id + "___"

def get_body_nodes(nodes, proto_id):
    pref = function_prefix(proto_id)
    return [nid for nid in nodes.keys() if nid.startswith(pref)]

###############################################################################
# Heuristic to tell if a node looks like a callsite (e.g. this.room.regulate(1))
# Rules:
#  - must have "(" and ")"
#  - must NOT start with <&lt;operator> (assignment etc)
#  - we don't color the prototype node itself blue
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
    result = set()
    for p in prototypes:
        result.update(find_callsites_for_proto(nodes, p))
    return result

###############################################################################
# Reverse DFS from a set of starting nodes using incoming edges
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
# Emit reduced graph:
# - Keep ONLY nodes that are red or blue.
# - Red = prototypes
# - Blue = callsites plus reverse-DFS ancestors of those callsites
# - Keep only edges where both ends are kept.
# - Keep only clusters that still have at least one kept node.
# - Preserve colors.
###############################################################################

def emit_reduced_dot(parsed, red_nodes, blue_nodes, out_file):
    digraph_name = parsed["digraph_name"]
    header_lines = parsed["header_lines"]
    nodes        = parsed["nodes"]
    edges        = parsed["edges"]
    clusters     = parsed["clusters"]

    kept_nodes = set(red_nodes) | set(blue_nodes)

    # keep only edges fully inside kept set
    kept_edges = [
        (src, dst, attrs)
        for (src, dst, attrs) in edges
        if src in kept_nodes and dst in kept_nodes
    ]

    # rebuild clusters with only kept nodes
    new_clusters = {}
    for cname, cinfo in clusters.items():
        kept_here = [nid for nid in cinfo["nodes"] if nid in kept_nodes]
        if kept_here:
            new_clusters[cname] = {
                "attrs": cinfo["attrs"][:],
                "nodes": kept_here,
            }

    clustered_nodes = set()
    for cinfo in new_clusters.values():
        clustered_nodes.update(cinfo["nodes"])
    loose_nodes = [nid for nid in kept_nodes if nid not in clustered_nodes]

    # color mapping
    node_colors = {}
    for n in blue_nodes:
        node_colors[n] = "lightblue"
    for n in red_nodes:
        node_colors[n] = "red"  # red overrides blue if overlap

    def render_node(nid, indent="    "):
        data = nodes[nid]
        label = data["label"] if data["label"] is not None else "\"\""

        # preserve HTML-like labels <...> or <<...>>
        if label.lstrip().startswith("<"):
            label_part = f"label = {label}"
        else:
            safe = label.replace('"', '\\"')
            label_part = f'label = "{safe}"'

        color_attr = ""
        if nid in node_colors:
            color_attr = f' style=filled fillcolor="{node_colors[nid]}"'

        return f'{indent}"{nid}" [{label_part}{color_attr} ];'

    def render_edge(src, dst, attrs, indent="    "):
        if attrs and attrs.strip():
            return f'{indent}"{src}" -> "{dst}" [{attrs}];'
        else:
            return f'{indent}"{src}" -> "{dst}";'

    # ----- actually write the DOT -----
    with open(out_file, "w") as f:
        f.write(f'digraph "{digraph_name}" {{\n')

        # global / header attributes
        for hl in header_lines:
            if hl in ("{", "}", ""):
                continue
            f.write(f"  {hl};\n")

        # surviving clusters
        for cname, cinfo in new_clusters.items():
            f.write(f'  subgraph "{cname}" {{\n')
            for attr in cinfo["attrs"]:
                f.write(f"    {attr};\n")
            for nid in cinfo["nodes"]:
                f.write(render_node(nid, indent="    ") + "\n")
            f.write("  }\n")

        # loose nodes (not in any cluster)
        for nid in loose_nodes:
            f.write(render_node(nid, indent="  ") + "\n")

        # edges
        for (src, dst, attrs) in kept_edges:
            f.write(render_edge(src, dst, attrs, indent="  ") + "\n")

        f.write("}\n")

###############################################################################
# main
###############################################################################

def main(in_path, out_path, functions_of_interest):
    # read .dot
    with open(in_path, "r") as fin:
        dot_text = fin.read()

    parsed = parse_dot(dot_text)

    # 1. prototypes (red)
    proto_nodes = find_prototypes(parsed["nodes"], functions_of_interest)

    # 2. callsites that appear inside those prototypes (blue seeds)
    blue_seed_nodes = collect_all_callsites(parsed["nodes"], proto_nodes)

    # 3. reverse DFS from blue seeds to pull in dependencies
    blue_closure = reverse_dfs_multi(blue_seed_nodes, parsed["incoming"])

    red_nodes  = set(proto_nodes)
    blue_nodes = set(blue_closure)

    emit_reduced_dot(parsed, red_nodes, blue_nodes, out_path)

    print("Red prototype nodes:", len(red_nodes))
    print("Blue slice nodes:", len(blue_nodes))

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python3 reduceCFG.py <input.dot> <output.dot>")
        sys.exit(1)

    in_path = sys.argv[1]
    out_path = sys.argv[2]

    FUNCTIONS_OF_INTEREST = ["getSense", "activateh", "switchoff"]

    main(in_path, out_path, FUNCTIONS_OF_INTEREST)
