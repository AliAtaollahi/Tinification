# 1.py
from __future__ import annotations
import sys, re
from pathlib import Path
from typing import List, Tuple, Dict, Set
PRINT_TIME_PATHS = True

# ---------------- AUT structures & parser ----------------

class AutLTS:
    def __init__(self, initial: str | None = None):
        self.initial = initial
        self.transitions: List[Tuple[str, str, str]] = []  # (src, label, dst) in input order
        self.state_set: Set[str] = set()
        self.header_counts: Dict[str, int] = {}  # {'transitions': N, 'states': M}

def _try_int_or_str(token: str) -> str:
    token = token.strip()
    if (token.startswith('"') and token.endswith('"')) or (token.startswith("'") and token.endswith("'")):
        inner = token[1:-1]
        inner = inner.replace(r"\\", "\\").replace(r"\"","\"").replace(r"\'","'")
        return inner
    try:
        return str(int(token))
    except ValueError:
        return token

def parse_aut(text: str) -> AutLTS:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    if not lines:
        raise ValueError("Empty .aut content")
    lts = AutLTS()

    header_re = re.compile(r'^des\s*\(\s*([^,]+)\s*,\s*([^,]+)\s*,\s*([^)]+)\s*\)\s*\.?\s*$', re.IGNORECASE)
    start_idx = 0
    m = header_re.match(lines[0])
    if m:
        lts.initial = _try_int_or_str(m.group(1))
        try: lts.header_counts["transitions"] = int(m.group(2).strip())
        except: pass
        try: lts.header_counts["states"] = int(m.group(3).strip())
        except: pass
        lts.state_set.add(lts.initial)
        start_idx = 1

    quoted_re = re.compile(r'^\(\s*([^,]+?)\s*,\s*"((?:[^"\\]|\\.)*)"\s*,\s*([^)]+?)\s*\)\s*;?\s*\.?\s*$')
    plain_re  = re.compile(r'^\(\s*([^,]+?)\s*,\s*([^,"]+?)\s*,\s*([^)]+?)\s*\)\s*;?\s*\.?\s*$')

    for ln in lines[start_idx:]:
        mq = quoted_re.match(ln)
        if mq:
            src, label, dst = mq.group(1), mq.group(2), mq.group(3)
            src, dst = _try_int_or_str(src), _try_int_or_str(dst)
            try:
                label = label.encode("utf-8").decode("unicode_escape")
            except Exception:
                pass
            lts.transitions.append((src, label, dst))
            lts.state_set.update([src, dst])
            continue
        mp = plain_re.match(ln)
        if mp:
            src, label, dst = mp.group(1), mp.group(2), mp.group(3)
            src, dst = _try_int_or_str(src), _try_int_or_str(dst)
            lts.transitions.append((src, label.strip(), dst))
            lts.state_set.update([src, dst])
            continue
        if ln.startswith("("):
            raise ValueError(f"Unrecognized transition syntax: {ln}")

    if lts.initial is None:
        lts.initial = lts.transitions[0][0] if lts.transitions else "0"
        lts.state_set.add(lts.initial)
    return lts

# ---------------- Transformation: accumulate time-only segments ----------------

_time_re = re.compile(r'^\s*time\s*\+\=\s*(\d+)\s*$', re.IGNORECASE)

def _is_time(label: str) -> int | None:
    m = _time_re.match(label)
    return int(m.group(1)) if m else None

def _nat_key(s: str):
    try:
        return (0, int(s))
    except:
        return (1, s)

def reachable_from(initial: str, edges: List[Tuple[str,str,str]]) -> Set[str]:
    adj: Dict[str, List[str]] = {}
    for u, _, v in edges:
        adj.setdefault(u, []).append(v)
    seen, stack = set([initial]), [initial]
    while stack:
        u = stack.pop()
        for v in adj.get(u, []):
            if v not in seen:
                seen.add(v)
                stack.append(v)
    return seen

def accumulate_time_edges(lts: AutLTS) -> List[Tuple[str, str, str]]:
    """
    Traverse from root; whenever a consecutive block of time edges is encountered,
    replace that entire block with a single 'time += SUM' edge from the block's
    entry node to its terminal node. Non-time edges remain unchanged (same order).

    Additionally, if PRINT_TIME_PATHS is True, print every discovered time-only path
    (the exact sequence of states and increments, and the total sum) to stderr.
    """
    non_time_edges: List[Tuple[str,str,str]] = []
    time_out: Dict[str, List[Tuple[int,str]]] = {}
    time_in_count: Dict[str, int] = {}
    time_src_order: Dict[str, int] = {}
    non_time_in_from: Dict[str, List[str]] = {}

    for idx, (u, label, v) in enumerate(lts.transitions):
        n = _is_time(label)
        if n is None:
            non_time_edges.append((u, label, v))
            non_time_in_from.setdefault(v, []).append(u)
        else:
            time_out.setdefault(u, []).append((n, v))
            time_in_count[v] = time_in_count.get(v, 0) + 1
            time_src_order.setdefault(u, idx)

    reach = reachable_from(lts.initial, lts.transitions)

    entries: List[str] = []
    for u in time_out.keys():
        if u not in reach:
            continue
        pred_non_time = any(p in reach for p in non_time_in_from.get(u, []))
        if (u == lts.initial) or pred_non_time or (time_in_count.get(u, 0) == 0):
            entries.append(u)
    entries.sort(key=lambda x: time_src_order.get(x, 1_000_000))

    new_time_edges_list: List[Tuple[str,int,str]] = []
    seen_triples: Set[Tuple[str,int,str]] = set()

    def add_time_edge(s: str, w: int, t: str):
        triple = (s, w, t)
        if triple not in seen_triples:
            seen_triples.add(triple)
            new_time_edges_list.append(triple)

    # simple counter for pretty logs
    path_log_counter = {"n": 0}

    def _log_time_path(path_nodes: List[str], deltas: List[int], total: int):
        if not PRINT_TIME_PATHS:
            return
        # Example: [A, B, C] with deltas [2, 5] => "A -(time+=2)-> B ; B -(time+=5)-> C"
        segments = []
        for i, d in enumerate(deltas):
            segments.append(f'{path_nodes[i]} -(time += {d})-> {path_nodes[i+1]}')
        path_log_counter["n"] += 1
        print(
            f'[time-path #{path_log_counter["n"]}] start={path_nodes[0]} end={path_nodes[-1]} '
            f'sum={total} : ' + ' ; '.join(segments),
            file=sys.stderr
        )

    def dfs(start: str, node: str, acc: int, seen_nodes: Set[str],
            path_nodes: List[str], deltas: List[int]):
        outs = time_out.get(node, [])
        if not outs:
            if node != start:
                # Only record/print if this triple hasn't been added yet
                if (start, acc, node) not in seen_triples:
                    add_time_edge(start, acc, node)
                    _log_time_path(path_nodes, deltas, acc)
            return
        for n, nxt in outs:
            if nxt in seen_nodes:
                # skip cycles; not a terminal time-only block
                continue
            dfs(start, nxt, acc + n, seen_nodes | {nxt}, path_nodes + [nxt], deltas + [n])

    for u in entries:
        for n, v in time_out[u]:
            dfs(u, v, n, {u, v}, [u, v], [n])

    out: List[Tuple[str,str,str]] = []
    out.extend(non_time_edges)
    for u, w, v in new_time_edges_list:
        out.append((u, f"time +={w}", v))
    return out

# ---------------- I/O ----------------

def format_aut(lts: AutLTS, transitions: List[Tuple[str,str,str]]) -> str:
    # FIX: transitions count should reflect the NEW number of transitions
    tr_count = len(transitions)
    # Keep initial and states exactly as in the input header (fallbacks if absent)
    st_count = lts.header_counts.get("states", len(lts.state_set))
    lines = [f"des ({lts.initial},{tr_count},{st_count})"]
    for u, label, v in transitions:
        safe_label = label.replace('"', r'\"')
        lines.append(f'({u},"{safe_label}",{v})')
    return "\n".join(lines)

def process_path(p: Path) -> None:
    text = p.read_text(encoding="utf-8", errors="replace")
    lts = parse_aut(text)
    new_transitions = accumulate_time_edges(lts)
    print(format_aut(lts, new_transitions))

def main():
    if len(sys.argv) != 2:
        print("Usage: python 1.py <path_to_.aut_or_folder>")
        sys.exit(1)

    p = Path(sys.argv[1]).expanduser().resolve()
    if not p.exists():
        print(f"Path not found: {p}")
        sys.exit(1)

    if p.is_dir():
        files = sorted(p.glob("*.aut"), key=lambda q: (_nat_key(q.stem)))
        if not files:
            print(f"No .aut files found in: {p}")
            sys.exit(1)
        first = True
        for f in files:
            if not first:
                print()
            process_path(f)
            first = False
    else:
        if p.suffix.lower() != ".aut":
            print(f"Not an .aut file: {p}")
            sys.exit(1)
        process_path(p)

if __name__ == "__main__":
    main()
