"""The Concept Graph IR.

Every table here is a node type in the graph (`Edge` rows are the arcs).
Field sets match what each owning pass actually needs — later stages extend
a table only when the pass that owns it (CLAUDE.md §4) is built; this file
does not get ahead of the stage that is live.

Every node carries `id, created_by_pass, content_hash, created_at`
(`IRNode`, below). `created_by_pass` is immutable after first write — see
`IRNode.__setattr__` — and `Graph.add` (core/graph.py) enforces that it
matches the declared owner for the node's type before it is ever persisted.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar
from uuid import uuid4

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid4().hex


class IRNode(SQLModel):
    """Shared base for every graph node. Not a table itself."""

    id: str = Field(default_factory=_new_id, primary_key=True)
    created_by_pass: str
    content_hash: str
    created_at: datetime = Field(default_factory=_utcnow)

    def __setattr__(self, name: str, value: Any) -> None:
        # CLAUDE.md §4/§5: a pass writes only its own column, and provenance
        # of *who created a node* must not silently drift after the fact.
        if name == "created_by_pass" and name in self.__dict__:
            existing = self.__dict__[name]
            if existing is not None and existing != value:
                raise ValueError(
                    f"created_by_pass is immutable after write "
                    f"(was {existing!r}, tried to set {value!r})"
                )
        super().__setattr__(name, value)


# ---------------------------------------------------------------------------
# ingest — Block, SourceSpan
# ---------------------------------------------------------------------------


class BlockType(StrEnum):
    heading = "heading"
    paragraph = "paragraph"
    figure = "figure"
    caption = "caption"
    table = "table"
    equation = "equation"
    list = "list"


class Block(IRNode, table=True):
    """One structural unit of a source page, as classified by ingest."""

    file_id: str
    page: int
    bbox: list[float] = Field(sa_column=Column(JSON))  # [x0, y0, x1, y1]
    text: str
    block_type: BlockType
    # Set on a figure/table Block once a Caption Block is bound to it.
    bound_caption_id: str | None = None


class SourceSpan(IRNode, table=True):
    """The provenance record for one Block: exact file/page/byte origin."""

    block_id: str = Field(foreign_key="block.id")
    file_id: str
    page: int
    bbox: list[float] = Field(sa_column=Column(JSON))
    char_range: list[int] = Field(sa_column=Column(JSON))  # [start, end)
    sha256: str


# ---------------------------------------------------------------------------
# understand — Concept, Alias, SyllabusNode
#
# CLAUDE.md §4's ownership table names this node type `SyllabusRef`; D1/D2 of
# PROMPTS.md — the section that actually defines its shape — names it
# `SyllabusNode` consistently. Decision log #6 below resolves this in favor
# of the schema-defining text.
# ---------------------------------------------------------------------------


class ConceptType(StrEnum):
    definition = "definition"
    formula = "formula"
    procedure = "procedure"
    theorem = "theorem"
    phenomenon = "phenomenon"
    example = "example"


class Concept(IRNode, table=True):
    name: str
    concept_type: ConceptType
    definition_span_id: str | None = Field(default=None, foreign_key="sourcespan.id")
    salience: float = 0.0
    # Zero or one SyllabusNode (D2 syllabus anchoring). A plain FK, not an
    # Edge: EdgeKind (below) is a closed enum with no "syllabus link" member,
    # and linking is capped at one node, unlike a real `part_of` edge set.
    syllabus_node_id: str | None = Field(default=None, foreign_key="syllabusnode.id")
    # Set by verify (D4) when dropping an unsupported/contradicted sentence
    # empties one of this concept's contract slots (CLAUDE.md I5).
    status: str = "ok"  # "ok" | "partial"


class Alias(IRNode, table=True):
    concept_id: str = Field(foreign_key="concept.id")
    alias_text: str


class SyllabusNode(IRNode, table=True):
    code: str  # e.g. "1.1"
    title: str
    order: int


# ---------------------------------------------------------------------------
# evidence — WebEvidence
# ---------------------------------------------------------------------------


class WebEvidence(IRNode, table=True):
    url: str
    domain: str
    retrieved_at: datetime
    chunk_text: str
    tier: str  # "tier1" | "tier2" | "blocklisted"
    admission: str = "pending"  # "admitted" | "weak" | "rejected" | "pending"
    score: float | None = None


# ---------------------------------------------------------------------------
# compose — LessonBlock
# ---------------------------------------------------------------------------


class LessonBlock(IRNode, table=True):
    concept_id: str = Field(foreign_key="concept.id")
    slot: str  # one of the 5 I5 contract slots
    content: str
    status: str = "draft"  # "draft" | "ok" | "partial" | "quarantined"


# ---------------------------------------------------------------------------
# assess — Item, Misconception
# ---------------------------------------------------------------------------


class Item(IRNode, table=True):
    concept_id: str = Field(foreign_key="concept.id")
    bloom_level: str
    item_type: str  # "mcq" | "numeric" | "short" | "derivation" | "application"
    stem: str
    key: str


class Misconception(IRNode, table=True):
    concept_id: str = Field(foreign_key="concept.id")
    description: str


# ---------------------------------------------------------------------------
# verify — Verdict, ExecResult
# ---------------------------------------------------------------------------


class Verdict(IRNode, table=True):
    lesson_block_id: str = Field(foreign_key="lessonblock.id")
    sentence_index: int
    classification: str  # "entailed" | "unsupported" | "contradicted"


class ExecResult(IRNode, table=True):
    expression: str
    result: str
    success: bool


# ---------------------------------------------------------------------------
# learn — Mastery
# ---------------------------------------------------------------------------


class Mastery(IRNode, table=True):
    concept_id: str = Field(foreign_key="concept.id")
    student_id: str
    probability: float


# ---------------------------------------------------------------------------
# Edge — the arcs. Owned pass depends on `kind`, not on Edge itself.
# ---------------------------------------------------------------------------


class EdgeKind(StrEnum):
    prerequisite_of = "prerequisite_of"
    part_of = "part_of"
    evidenced_by = "evidenced_by"
    contradicts = "contradicts"
    assesses = "assesses"
    remediates = "remediates"
    mastery_of = "mastery_of"


class Edge(IRNode, table=True):
    source_id: str
    target_id: str
    kind: EdgeKind
    confidence: float | None = None


# Every node table, keyed by class — used by Graph for id lookup, ownership
# enforcement, and to_json/from_json. Add a new table here in the stage that
# introduces it.
NODE_TABLES: ClassVar[tuple[type[SQLModel], ...]] = (
    Block,
    SourceSpan,
    Concept,
    Alias,
    SyllabusNode,
    WebEvidence,
    LessonBlock,
    Item,
    Misconception,
    Verdict,
    ExecResult,
    Mastery,
)
