"""Application process: a small directed graph of steps, plus one Closed node.

Pure data + graph operations (no storage). Every step implicitly leads to the
Closed node, so its edges are not stored. The application's status and stage
are derived from the graph, never set by hand.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from .models_base import new_id

NodeState = Literal["todo", "planned", "done"]
NodeKind = Literal["step", "offer", "closed"]
# What a step is, for analytics (inferred from the name when empty)
Category = Literal["", "applied", "response", "hr", "technical", "interview", "reference", "offer", "other"]
CATEGORIES = {"applied": "Applied", "response": "Response", "hr": "HR interview", "technical": "Technical interview",
              "interview": "Other interview", "reference": "Reference check", "offer": "Offer", "other": "Other"}
_CATEGORY_RULES = [  # first match wins
    ("offer", r"offer"), ("reference", r"reference"), ("applied", r"appl|sent|submitted"),
    ("response", r"response|repl|answer|got back|screen"),
    ("technical", r"technical|tech\b|coding|take.?home|home assignment|assignment|case|test|system design|live code"),
    ("hr", r"\bhr\b|recruit|talent|human resources|phone screen"),
    ("interview", r"interview|meet|call|panel|onsite|on-site|final|cto|ceo|manager"),
]

DEFAULT_STEPS = ["Applied", "Response received", "First HR interview", "Reference check"]
CLOSED_NAME = "Rejected / Closed"

STATUSES = ["Not applied", "Applied", "In process", "Offer", "Rejected"]


class Node(BaseModel):
    id: str = Field(default_factory=new_id)
    name: str
    kind: NodeKind = "step"      # "offer": reaching it means an offer; "closed": the end of the process
    category: Category = ""      # applied / response / hr / technical / interview / reference / offer / other
    state: NodeState = "todo"    # todo = off, planned = scheduled, done = on (red for the Closed node)
    date: str | None = None
    notes: str = ""


    def type(self) -> str:
        """The step's category, inferred from its name when not set."""
        if self.kind == "offer":
            return "offer"
        if self.category:
            return self.category
        for cat, pattern in _CATEGORY_RULES:
            if re.search(pattern, self.name, re.IGNORECASE):
                return cat
        return "other"


class Edge(BaseModel):
    source: str
    target: str


class GraphError(ValueError):
    pass


class Process(BaseModel):
    nodes: list[Node] = []
    edges: list[Edge] = []

    # ------------------------------------------------------------- lookups

    @property
    def closed(self) -> Node:
        return next(n for n in self.nodes if n.kind == "closed")

    @property
    def steps(self) -> list[Node]:
        return [n for n in self.nodes if n.kind != "closed"]

    def node(self, ref: str) -> Node:
        """Find a node by id, or by name (case-insensitive)."""
        for n in self.nodes:
            if n.id == ref:
                return n
        hits = [n for n in self.nodes if n.name.strip().lower() == ref.strip().lower()]
        if len(hits) == 1:
            return hits[0]
        if not hits:
            raise GraphError(f"No step {ref!r}. Steps: {', '.join(n.name for n in self.nodes)}")
        raise GraphError(f"Several steps are named {ref!r}: use the id ({', '.join(n.id for n in hits)})")

    def successors(self, node_id: str) -> list[str]:
        return [e.target for e in self.edges if e.source == node_id]

    def predecessors(self, node_id: str) -> list[str]:
        return [e.source for e in self.edges if e.target == node_id]

    def order(self) -> list[Node]:
        """Steps in a stable topological order (graph order, ties by creation order)."""
        index = {n.id: i for i, n in enumerate(self.steps)}
        indegree = {n.id: len(self.predecessors(n.id)) for n in self.steps}
        ready = sorted([i for i, d in indegree.items() if d == 0], key=index.get)
        out = []
        while ready:
            current = ready.pop(0)
            out.append(current)
            for t in self.successors(current):
                indegree[t] -= 1
                if indegree[t] == 0:
                    ready.append(t)
                    ready.sort(key=index.get)
        by_id = {n.id: n for n in self.steps}
        return [by_id[i] for i in out]

    def _reaches(self, start: str, goal: str) -> bool:
        stack, seen = [start], set()
        while stack:
            current = stack.pop()
            if current == goal:
                return True
            if current not in seen:
                seen.add(current)
                stack.extend(self.successors(current))
        return False

    def _link(self, source: str, target: str) -> None:
        if source == target or self._reaches(target, source):
            raise GraphError("That link would create a loop")
        if not any(e.source == source and e.target == target for e in self.edges):
            self.edges.append(Edge(source=source, target=target))

    def _unlink(self, source: str, target: str) -> None:
        self.edges = [e for e in self.edges if not (e.source == source and e.target == target)]

    def _new(self, node: Node) -> Node:
        if node.kind == "closed":
            raise GraphError("There is only one Closed node")
        self.nodes.insert(len(self.steps), node)  # keep the Closed node last
        return node

    # ---------------------------------------------------------- operations

    def insert_between(self, source: str, target: str, node: Node) -> Node:
        """Split the link source -> target with a new step."""
        if not any(e.source == source and e.target == target for e in self.edges):
            raise GraphError("These two steps are not directly linked")
        self._new(node)
        self._unlink(source, target)
        self._link(source, node.id)
        self._link(node.id, target)
        return node

    def insert_after(self, ref: str, node: Node) -> Node:
        """New step right after `ref`: ref -> new -> (what came after ref)."""
        after = self.node(ref).id
        self._new(node)
        for t in self.successors(after):
            self._unlink(after, t)
            self._link(node.id, t)
        self._link(after, node.id)
        return node

    def insert_before(self, ref: str, node: Node) -> Node:
        """New step right before `ref`: (what came before ref) -> new -> ref."""
        before = self.node(ref).id
        self._new(node)
        for s in self.predecessors(before):
            self._unlink(s, before)
            self._link(s, node.id)
        self._link(node.id, before)
        return node

    def append(self, node: Node) -> Node:
        """New step at the end of the main path."""
        order = self.order()
        if not order:
            return self._new(node)
        sinks = [n for n in order if not self.successors(n.id)]
        return self.insert_after(sinks[-1].id, node)

    def add_branch(self, ref: str, node: Node, join: str | None = None) -> Node:
        """Alternative path: ref -> new (-> join). Without `join` the branch is a dead end."""
        source = self.node(ref).id
        target = self.node(join).id if join else None
        if target == source:
            raise GraphError("A branch can't join back at its own start")
        self._new(node)
        self._link(source, node.id)
        if target:
            self._link(node.id, target)
        return node

    def remove(self, ref: str) -> Node:
        """Delete a step and reconnect what came before it to what came after it."""
        node = self.node(ref)
        if node.kind == "closed":
            raise GraphError("The Closed node can't be deleted (rename or turn it off instead)")
        preds, succs = self.predecessors(node.id), self.successors(node.id)
        self.edges = [e for e in self.edges if node.id not in (e.source, e.target)]
        self.nodes.remove(node)
        for s in preds:
            for t in succs:
                self._link(s, t)
        return node

    # ------------------------------------------------------------ derived

    def status(self) -> str:
        if self.closed.state == "done":
            return "Rejected"
        done = [n for n in self.steps if n.state == "done"]
        if any(n.type() == "offer" for n in done):
            return "Offer"
        if not done:
            return "Not applied"
        roots = {n.id for n in self.steps if not self.predecessors(n.id)}
        return "Applied" if all(n.id in roots for n in done) else "In process"

    def stage(self) -> str:
        """Where it stands: the next planned step, else the last done step, else the status."""
        if self.closed.state == "done":
            return self.closed.name
        order = self.order()
        planned = [n for n in order if n.state == "planned"]
        done = [n for n in order if n.state == "done"]
        if planned:
            return planned[0].name
        if done:
            return done[-1].name
        return self.status()

    def outline(self) -> list[str]:
        """Readable lines for agents: '[done] Applied (2026-11-10) -> Response received'."""
        names = {n.id: n.name for n in self.nodes}
        lines = []
        for n in self.order():
            nxt = ", ".join(names[t] for t in self.successors(n.id)) or "(end)"
            when = f" ({n.date})" if n.date else ""
            lines.append(f"[{n.state}] {n.name} <{n.type()}>{when} -> {nxt}   id={n.id}")
        c = self.closed
        lines.append(f"[{'CLOSED' if c.state == 'done' else 'open'}] {c.name} (reachable from every step)   id={c.id}")
        return lines


def default_process(step_names: list[str] | None = None) -> Process:
    p = Process(nodes=[Node(name=CLOSED_NAME, kind="closed")])
    for name in step_names or DEFAULT_STEPS:
        p.append(Node(name=name))
    return p


def from_legacy(steps: list[dict], status: str) -> Process:
    """Convert the old flat step list (and status) of saved data into a graph."""
    if steps:
        p = Process(nodes=[Node(name=CLOSED_NAME, kind="closed")])
        for s in steps:
            state = {"done": "done", "planned": "planned"}.get(s.get("status"), "todo")
            p.append(Node(id=s.get("id") or new_id(), name=s["name"], state=state,
                          date=s.get("date"), notes=s.get("notes", "")))
    else:
        p = default_process()  # status alone isn't trusted: nothing is marked done
    if status in ("Rejected", "Withdrawn"):
        p.closed.state = "done"
        p.closed.notes = status
    return p
