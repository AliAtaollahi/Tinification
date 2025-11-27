import re
import sys
from collections import defaultdict

###############################################################################
# Parse DOT
###############################################################################

def parse_dot(dot_text):
    subgraph_start = re.compile(r'^\s*subgraph\s+"([^"]+)"\s*\{')
    subgraph_end   = re.compile(r'^\s*\}')
    edge_re        = re.compile(r'^\s*"([^"]+)"\s*->\s*"([^"]+)"\s*(\[(.*?)\])?')
    node_re        = re.compile(r'^\s*"([^"]+)"\s*\[(.*?)\]\s*;?$')
    label_attr     = re.compile(r'label\s*=\s*(.+)')

    nodes     = {}  # node_id -> {"label": raw_label_text, "cluster": cluster_name or None}
    edges     = []  # (src, dst, attr_block_string_or_None)
    incoming  = defaultdict(list)
    outgoing  = defaultdict(list)
    clusters  = {} # cluster_name -> {"attrs":[], "nodes":[]}

    cluster_stack = []

    digraph_name = None
    header_lines = []

    seen_real_content = False

    for line in dot_text.splitlines():
        stripped = line.strip()

        # digraph "..." {
        if digraph_name is None:
            m = re.match(r'^\s*digraph\s+"([^"]+)"\s*\{', line)
            if m:
                digraph_name = m.group(1)
                continue

        # subgraph "cluster_x" {
        m = subgraph_start.match(line)
        if m:
            seen_real_content = True
            cname = m.group(1)
            cluster_stack.append(cname)
            if cname not in clusters:
                clusters[cname] = {"attrs": [], "nodes": []}
            continue

        # }
        m = subgraph_end.match(line)
        if m:
            if cluster_stack:
                cluster_stack.pop()
            continue

        # "a" -> "b" [ ... ]
        m = edge_re.match(line)
        if m:
            seen_real_content = True
            src = m.group(1)
            dst = m.group(2)
            attr_block = m.group(4)  # can be None
            edges.append((src, dst, attr_block))
            outgoing[src].append(dst)
            incoming[dst].append(src)
            continue

        # "node_id" [label = <...> ...]
        m = node_re.match(line)
        if m:
            seen_real_content = True
            nid = m.group(1)
            attrs = m.group(2)

            m2 = label_attr.search(attrs)
            lbl = m2.group(1).strip() if m2 else None

            cluster_name = cluster_stack[-1] if cluster_stack else None
            if nid not in nodes:
                nodes[nid] = {"label": lbl, "cluster": cluster_name}
            else:
                nodes[nid]["label"] = lbl
                if nodes[nid]["cluster"] is None and cluster_name is not None:
                    nodes[nid]["cluster"] = cluster_name

            if cluster_name is not None:
                clusters[cluster_name]["nodes"].append(nid)
            continue

        # attributes inside a cluster, or global header stuff
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
        "name": digraph_name or "G",
        "header": header_lines,
        "nodes": nodes,
        "edges": edges,
        "incoming": dict(incoming),
        "outgoing": dict(outgoing),
        "clusters": clusters,
    }

###############################################################################
# Helpers for prototypes / callsites / reverse slice
###############################################################################

def find_prototypes(nodes, fnames):
    """
    A node is a 'prototype' if its label contains ".<fname>(" for any fname.
    These become RED.
    """
    out = set()
    for nid, data in nodes.items():
        lbl = data["label"]
        if not lbl:
            continue
        low = lbl.lower()
        for f in fnames:
            if "." + f.lower() + "(" in low:
                out.add(nid)
                break
    return out

def func_prefix(nid):
    """
    Node IDs that are in one function's body share a prefix up to the last
    '___<digits>' suffix. We'll slice at the last triple-underscore.
    """
    m = re.match(r'^(.*?___)\d+$', nid)
    if m:
        return m.group(1)
    parts = nid.rsplit("___", 1)
    if len(parts) == 2:
        return parts[0] + "___"
    return nid + "___"

def body_nodes(nodes, proto_id):
    prefix = func_prefix(proto_id)
    return [n for n in nodes.keys() if n.startswith(prefix)]

def looks_like_callsite(lbl):
    """
    A body statement is considered a 'callsite' if:
      - it has '(' and ')'
      - it is NOT an <operator>... node.
    Those become initial BLUE seeds.
    """
    if not lbl:
        return False
    test = lbl.lower().lstrip()
    if test.startswith("<&lt;operator"):
        return False
    return ("(" in lbl) and (")" in lbl)

def callsites_in_proto(nodes, proto_id):
    result = []
    for nid in body_nodes(nodes, proto_id):
        if nid == proto_id:
            continue
        lbl = nodes[nid]["label"]
        if looks_like_callsite(lbl):
            result.append(nid)
    return result

def reverse_dfs_multi(starts, incoming):
    """
    Walk incoming edges ONLY.
    Everything you can reach backwards from these callsites = BLUE (closure).
    """
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
# Rendering helpers that avoid the f-string backslash problem
###############################################################################

def build_label_part(lbl):
    if lbl is None:
        lbl = "\"\""
    # If it starts with '<', it's already HTML-like label `<...>` or `<<...>>`
    if lbl.lstrip().startswith("<"):
        return "label = " + lbl
    # Otherwise quote it and escape inner quotes
    safe = lbl.replace('"', '\\"')
    return 'label = "{}"'.format(safe)

def render_node_line(nid, nodes, color=None, indent="  "):
    lbl = nodes[nid]["label"]
    label_part = build_label_part(lbl)
    style_part = ""
    if color is not None:
        style_part = ' style=filled fillcolor="{}"'.format(color)
    return '{}"{}" [{}{} ];'.format(indent, nid, label_part, style_part)

def render_edge_line(src, dst, attrs, indent="  "):
    if attrs and attrs.strip():
        return '{}"{}" -> "{}" [{}];'.format(indent, src, dst, attrs)
    else:
        return '{}"{}" -> "{}";'.format(indent, src, dst)

###############################################################################
# Emit colored DOT
###############################################################################

def emit_colored(parsed, red_nodes, blue_nodes, out_path):
    # build node->color map
    color_map = {}
    for n in blue_nodes:
        color_map[n] = "lightblue"
    for n in red_nodes:
        color_map[n] = "red"  # red overrides blue if overlap

    with open(out_path, "w") as f:
        f.write('digraph "{}" {{\n'.format(parsed["name"]))

        # global header lines
        for h in parsed["header"]:
            if h not in ("{", "}", ""):
                f.write("  {};\n".format(h))

        # clusters
        for cname, cinfo in parsed["clusters"].items():
            f.write('  subgraph "{}" {{\n'.format(cname))
            for a in cinfo["attrs"]:
                f.write("    {};\n".format(a))
            for nid in cinfo["nodes"]:
                if nid in parsed["nodes"]:
                    clr = color_map.get(nid)
                    f.write(render_node_line(nid, parsed["nodes"], clr, indent="    ") + "\n")
            f.write("  }\n")

        # nodes not in any cluster
        clustered = set()
        for ci in parsed["clusters"].values():
            for nid in ci["nodes"]:
                clustered.add(nid)

        for nid in parsed["nodes"]:
            if nid not in clustered:
                clr = color_map.get(nid)
                f.write(render_node_line(nid, parsed["nodes"], clr, indent="  ") + "\n")

        # edges
        for (s, d, a) in parsed["edges"]:
            f.write(render_edge_line(s, d, a, indent="  ") + "\n")

        f.write("}\n")

###############################################################################
# Main driver
###############################################################################

def main(input_path, output_path, functions_of_interest):
    with open(input_path, "r") as f:
        dot_text = f.read()

    parsed = parse_dot(dot_text)

    # 1. prototypes -> RED
    red = find_prototypes(parsed["nodes"], functions_of_interest)

    # 2. callsites inside those prototypes -> BLUE seeds
    blue_seeds = set()
    for p in red:
        blue_seeds.update(callsites_in_proto(parsed["nodes"], p))

    # 3. reverse DFS from seeds -> BLUE closure
    blue = reverse_dfs_multi(blue_seeds, parsed["incoming"])

    # emit
    emit_colored(parsed, red, blue, output_path)

    print("RED prototypes:", len(red), red)
    print("BLUE seeds:", len(blue_seeds), blue_seeds)
    print("BLUE closure:", len(blue))

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python3 sliceCfg.py <input.dot> <output.dot>")
        sys.exit(1)

    FUNCTIONS_OF_INTEREST = ["switchoff", "getSense","activateh"]

    main(sys.argv[1], sys.argv[2], FUNCTIONS_OF_INTEREST)
