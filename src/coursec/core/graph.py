"""`Graph` — a thin, enforcing wrapper over the SQLite session that holds the
Concept Graph IR.

Enforces CLAUDE.md §4 pass ownership: `add` raises unless the object's
`created_by_pass` matches the pass declared as owner for that node type (or,
for an `Edge`, for that edge kind).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine, select

from coursec.core.models import (
    NODE_TABLES,
    Alias,
    Block,
    Concept,
    Edge,
    EdgeKind,
    ExecResult,
    Item,
    LessonBlock,
    Mastery,
    Misconception,
    SourceSpan,
    SyllabusNode,
    Verdict,
    WebEvidence,
)

# CLAUDE.md §4, node types introduced through D1. `part_of` is owned by
# `structure` (decision log #8): D2's Extraction section mentions part_of
# while discussing Concept-to-section linking, but §4 assigns it to
# `structure`, and this map is what's enforced below — `understand` records
# section membership as plain data; `structure.py` is what turns it into
# `part_of` edges.
NODE_TYPE_OWNER: dict[type[SQLModel], str] = {
    Block: "ingest",
    SourceSpan: "ingest",
    Concept: "understand",
    Alias: "understand",
    SyllabusNode: "understand",
    WebEvidence: "evidence",
    LessonBlock: "compose",
    Item: "assess",
    Misconception: "assess",
    Verdict: "verify",
    ExecResult: "verify",
    Mastery: "learn",
}

# A value can be one pass name, or a frozenset of several: `evidenced_by` is
# "X is evidenced by Y" at two different levels — evidence writes
# Concept-evidenced_by->WebEvidence (this concept has retrieved evidence);
# compose writes LessonBlock-evidenced_by->SourceSpan|WebEvidence (this
# generated sentence cites this specific span). §4's table predates D4 and
# only names "evidence"; decision log #9 extends it rather than inventing a
# second edge kind for the same relationship at a different granularity.
EDGE_KIND_OWNER: dict[EdgeKind, str | frozenset[str]] = {
    EdgeKind.prerequisite_of: "structure",
    EdgeKind.part_of: "structure",
    EdgeKind.evidenced_by: frozenset({"evidence", "compose"}),
    EdgeKind.contradicts: "evidence",
    EdgeKind.assesses: "assess",
    EdgeKind.remediates: "learn",
    EdgeKind.mastery_of: "learn",
}


def _allowed_passes(owner: str | frozenset[str]) -> frozenset[str]:
    return owner if isinstance(owner, frozenset) else frozenset({owner})


class OwnershipViolation(ValueError):
    """Raised when a node/edge is added under a pass that does not own it."""


class MissingNodeError(ValueError):
    """Raised when an Edge references a source_id/target_id that is not in
    the graph."""


class Graph:
    """Wraps a SQLModel `Session` over one SQLite database."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        url = "sqlite://" if db_path == ":memory:" else f"sqlite:///{db_path}"
        self.engine = create_engine(url)
        SQLModel.metadata.create_all(self.engine)
        # expire_on_commit=False: callers routinely keep using a row (e.g. to
        # print a histogram) after the Graph/session is closed; committed
        # objects should stay readable rather than triggering a reload that
        # fails once the session is gone.
        self.session = Session(self.engine, expire_on_commit=False)

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> Graph:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- writes ------------------------------------------------------------

    def add(self, obj: SQLModel) -> SQLModel:
        """Persist one node or Edge, after checking pass ownership (and, for
        an Edge, that both endpoints already exist)."""
        if isinstance(obj, Edge):
            owner = EDGE_KIND_OWNER.get(obj.kind)
            if owner is None:
                raise OwnershipViolation(f"no declared owner for edge kind {obj.kind!r}")
            allowed = _allowed_passes(owner)
            if obj.created_by_pass not in allowed:
                raise OwnershipViolation(
                    f"edge kind {obj.kind!r} is owned by {sorted(allowed)!r}, "
                    f"not {obj.created_by_pass!r}"
                )
            for ref, role in ((obj.source_id, "source_id"), (obj.target_id, "target_id")):
                if self.get(ref) is None:
                    raise MissingNodeError(
                        f"Edge.{role}={ref!r} does not reference an existing node"
                    )
        else:
            owner = NODE_TYPE_OWNER.get(type(obj))
            if owner is None:
                raise OwnershipViolation(f"no declared owner for node type {type(obj).__name__}")
            if obj.created_by_pass != owner:
                raise OwnershipViolation(
                    f"{type(obj).__name__} is owned by pass {owner!r}, "
                    f"not {obj.created_by_pass!r}"
                )

        self.session.add(obj)
        self.session.commit()
        self.session.refresh(obj)
        return obj

    # -- reads ---------------------------------------------------------------

    def get(self, node_id: str) -> SQLModel | None:
        """Find a node (or Edge) by id, searching every table."""
        for table in (*NODE_TABLES, Edge):
            found = self.session.get(table, node_id)
            if found is not None:
                return found
        return None

    def neighbors(
        self,
        node_id: str,
        *,
        kind: EdgeKind | None = None,
        direction: str = "out",
    ) -> list[SQLModel]:
        """Nodes one edge-hop from `node_id`.

        `direction="out"` follows edges where `node_id` is the source (the
        default — "what does this node point to"); `"in"` follows edges
        where it is the target; `"both"` follows either.
        """
        if direction not in ("out", "in", "both"):
            raise ValueError(f"direction must be 'out', 'in', or 'both', got {direction!r}")

        ids: set[str] = set()
        if direction in ("out", "both"):
            stmt = select(Edge).where(Edge.source_id == node_id)
            if kind is not None:
                stmt = stmt.where(Edge.kind == kind)
            ids |= {e.target_id for e in self.session.exec(stmt)}
        if direction in ("in", "both"):
            stmt = select(Edge).where(Edge.target_id == node_id)
            if kind is not None:
                stmt = stmt.where(Edge.kind == kind)
            ids |= {e.source_id for e in self.session.exec(stmt)}

        nodes = [self.get(i) for i in ids]
        return [n for n in nodes if n is not None]

    def _transitive_closure(self, node_id: str, kind: EdgeKind, *, upstream: bool) -> list[str]:
        """Recursive-CTE walk of edges of one `kind`, returning the ids
        reached (not including `node_id` itself).

        `upstream=True` walks target->source (ancestors: nodes that point to
        this one); `upstream=False` walks source->target (descendants).
        """
        from_col, to_col = ("target_id", "source_id") if upstream else ("source_id", "target_id")
        stmt = text(
            f"""
            WITH RECURSIVE walk(id) AS (
                SELECT {to_col} FROM edge WHERE {from_col} = :start AND kind = :kind
                UNION
                SELECT e.{to_col}
                FROM edge e
                JOIN walk w ON e.{from_col} = w.id
                WHERE e.kind = :kind
            )
            SELECT id FROM walk
            """
        )
        result = self.session.execute(stmt, {"start": node_id, "kind": kind.value})
        return [row[0] for row in result]

    def ancestors(self, node_id: str, kind: EdgeKind) -> list[SQLModel]:
        """All nodes reachable by walking `kind` edges backward from
        `node_id` (e.g. every transitive prerequisite of a concept)."""
        ids = self._transitive_closure(node_id, kind, upstream=True)
        return [n for n in (self.get(i) for i in ids) if n is not None]

    def descendants(self, node_id: str, kind: EdgeKind) -> list[SQLModel]:
        """All nodes reachable by walking `kind` edges forward from
        `node_id` (e.g. everything this concept is a transitive prerequisite
        of)."""
        ids = self._transitive_closure(node_id, kind, upstream=False)
        return [n for n in (self.get(i) for i in ids) if n is not None]

    def subgraph(self, node_ids: list[str]) -> dict[str, Any]:
        """The induced subgraph on `node_ids`: those nodes, plus every Edge
        whose endpoints are both in the set. Same shape as `to_json`."""
        wanted = set(node_ids)
        nodes = [n for n in (self.get(i) for i in wanted) if n is not None]
        edges = [
            e
            for e in self.session.exec(select(Edge))
            if e.source_id in wanted and e.target_id in wanted
        ]
        return self._to_dict(nodes, edges)

    # -- (de)serialization ---------------------------------------------------

    def _to_dict(self, nodes: list[SQLModel], edges: list[Edge]) -> dict[str, Any]:
        by_type: dict[str, list[dict[str, Any]]] = {}
        for n in nodes:
            by_type.setdefault(type(n).__name__, []).append(json.loads(n.model_dump_json()))
        return {
            "nodes": by_type,
            "edges": [json.loads(e.model_dump_json()) for e in edges],
        }

    def to_json(self) -> str:
        """Dump every node and Edge currently in the graph."""
        nodes = [n for table in NODE_TABLES for n in self.session.exec(select(table))]
        edges = list(self.session.exec(select(Edge)))
        return json.dumps(self._to_dict(nodes, edges))

    def from_json(self, payload: str) -> None:
        """Load a `to_json` dump into this graph, bypassing pass-ownership
        checks (the rows were already validated once, when first written)."""
        # `model_validate` (not `model(**row)`) is required here: SQLModel
        # table models don't run pydantic coercion on plain construction, so
        # a JSON string for a datetime field would otherwise reach SQLite
        # unconverted and fail on insert.
        data = json.loads(payload)
        type_by_name = {t.__name__: t for t in NODE_TABLES}
        for type_name, rows in data["nodes"].items():
            model = type_by_name[type_name]
            for row in rows:
                self.session.add(model.model_validate(row))
        self.session.commit()
        for row in data["edges"]:
            self.session.add(Edge.model_validate(row))
        self.session.commit()
