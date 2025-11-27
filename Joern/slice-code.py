#!/usr/bin/env python3
"""
Backward slice + highlight on a Graphviz DOT produced by CfgByClass.run().

Usage:
    python slice_dot.py program_cfg_abstract_by_class.dot sliced.dot
"""

import sys
import pydot


# ---------- graph helpers ----------

def collect_nodes(g):
    """Recursively collect all nodes from graph and its subgraphs."""
    nodes = list(g.get_nodes())
    for sg in g.get_subgraphs():
        nodes.extend(collect_nodes(sg))
    return nodes


def collect_edges(g):
    """Recursively collect all edges from graph and its subgraphs."""
    edges = list(g.get_edges())
    for sg in g.get_subgraphs():
        edges.extend(collect_edges(sg))
    return edges


def build_predecessor_map(graph):
    """
    Build a map: dst_node_name -> set of src_node_names
    using all directed edges.
    """
    preds = {}
    for edge in collect_edges(graph):
        src = edge.get_source().strip('"')
        dst = edge.get_destination().strip('"')
        preds.setdefault(dst, set()).add(src)
        preds.setdefault(src, set())  # ensure src appears as key
    return preds


def build_node_index(graph):
    """
    Return:
      - name_to_node: node_name -> pydot.Node
      - label_map:    node_name -> label string (or "")
    """
    name_to_node = {}
    label_map = {}

    for node in collect_nodes(graph):
        name = node.get_name()
        if not name:
            continue

        # skip Graphviz defaults like 'node', 'graph'
        name = name.strip('"')
        if name in ("node", "graph"):
            continue

        name_to_node[name] = node
        label = node.get("label") or ""
        label_map[name] = label

    return name_to_node, label_map


# ---------- observable selection ----------

def find_observable_nodes_by_label(label_map, observable_keywords):
    """
    Return a set of node names whose label contains ANY of the observable_keywords.
    """
    observables = set()
    for name, label in label_map.items():
        for kw in observable_keywords:
            if kw and kw in label:
                observables.add(name)
                break
    return observables


# ---------- backward DFS coloring ----------

def backward_color(graph, observable_nodes, node_color="red"):
    """
    - observable_nodes: set of node *names* that are initially observable.
    - Colors all observable nodes and their backward slice (predecessors)
      until a colored node is reached.

    Returns the set of all colored node names.
    """
    preds = build_predecessor_map(graph)
    name_to_node, _ = build_node_index(graph)

    colored = set(observable_nodes)
    stack = list(observable_nodes)

    while stack:
        current = stack.pop()
        for parent in preds.get(current, []):
            if parent not in colored:
                colored.add(parent)
                stack.append(parent)

    # Apply styling to colored nodes
    for name in colored:
        node = name_to_node.get(name)
        if node is None:
            continue
        node.set("style", "filled")
        node.set("fillcolor", node_color)
        node.set("color", node_color)
        node.set("fontcolor", "white")

    return colored


# ---------- main ----------

def main():
    if len(sys.argv) < 3:
        print("Usage: python slice_dot.py input.dot output.dot")
        sys.exit(1)

    input_dot = sys.argv[1]
    output_dot = sys.argv[2]

    graphs = pydot.graph_from_dot_file(input_dot)
    if not graphs:
        raise RuntimeError(f"Could not read DOT from {input_dot}")
    graph = graphs[0]

    # --- 1) define your observable actions here ---
    # These are substrings searched inside the node labels.
    # Example: method nodes look like <<FONT>HVAC$Room.tempchange()</FONT>>
    #          statement nodes like <getTemp, 63<BR/>this.sensor.getTemp(temperature)>
    observable_keywords = [
        "HVAC$Room.tempchange()",
        "HVAC$Sensor.getTemp(temp)",
        # add whatever "observable actions" you care about
        # e.g. "this.meaningless.start()", "activateh", ...
    ]

    # If you prefer to identify nodes by ID instead of label substrings,
    # just compute observable_nodes = {"HVAC_Room__HVAC_Room_tempchange_void___107374182403", ...}
    # and skip find_observable_nodes_by_label.

    name_to_node, label_map = build_node_index(graph)
    observable_nodes = find_observable_nodes_by_label(label_map, observable_keywords)

    if not observable_nodes:
        print("Warning: no observable nodes found for the given keywords.")

    colored = backward_color(graph, observable_nodes, node_color="red")
    print(f"Colored {len(colored)} nodes (including observables).")

    # Write modified DOT
    graph.write_raw(output_dot)
    print(f"Wrote sliced DOT to: {output_dot}")


if __name__ == "__main__":
    main()
