// CourseC — project overview, set in the project's own typesetting engine.
// Editorial Ink, adapted for print: paper stays white so it doesn't drink
// ink, the accent carries structure, and the margin holds the apparatus.

#let ink     = rgb("#12110F")
#let ink2    = rgb("#3A3630")
#let ink3    = rgb("#6B655C")
#let ink4    = rgb("#9A9287")
#let rule    = rgb("#D8D1C4")
#let panel   = rgb("#F4F1EA")
#let oxblood = rgb("#7A2E2E")
#let moss    = rgb("#2F5A38")
#let ochre   = rgb("#7A5A16")

#let serif = ("Libertinus Serif", "Bitstream Charter", "DejaVu Serif")
#let mono  = ("DejaVu Sans Mono", "Liberation Mono")

#set document(title: "CourseC — a course compiler", author: "Sanjay")

#set page(
  paper: "a4",
  margin: (top: 26mm, bottom: 22mm, left: 24mm, right: 24mm),
  header: context {
    if counter(page).get().first() > 1 [
      #set text(font: mono, size: 7pt, fill: ink4)
      #grid(columns: (1fr, auto),
        align(left)[COURSEC — A COURSE COMPILER],
        align(right)[PROJECT OVERVIEW])
      #v(-4pt)
      #line(length: 100%, stroke: 0.4pt + rule)
    ]
  },
  footer: context {
    set text(font: mono, size: 7pt, fill: ink4)
    align(center)[#counter(page).display("01")]
  },
)

#set text(font: serif, size: 9.8pt, fill: ink, lang: "en")
#set par(justify: true, leading: 0.62em, spacing: 0.9em)

#show heading: set text(font: serif, weight: "regular")
#show heading.where(level: 1): it => {
  v(6pt)
  block(breakable: false)[
    #text(size: 19pt, fill: ink)[#it.body]
    #v(-2pt)
    #line(length: 100%, stroke: 0.6pt + ink)
  ]
  v(4pt)
}
#show heading.where(level: 1): set block(above: 16pt, below: 8pt)
#show heading.where(level: 2): it => {
  v(6pt)
  text(size: 12pt, fill: oxblood)[#it.body]
  v(1pt)
}
#show heading.where(level: 3): it => {
  v(3pt); text(size: 10pt, fill: ink, style: "italic")[#it.body]; v(-1pt)
}

#let kicker(body) = text(font: mono, size: 7.2pt, fill: ink4, tracking: 1.4pt)[#upper(body)]
#let code(body)   = text(font: mono, size: 8.2pt, fill: ink2)[#body]
#let num(body)    = text(font: mono, size: 9pt, fill: ink)[#body]

// a marginal note, the house motif
#let marginnote(n, body) = {
  place(right, dx: 0pt, dy: 0pt, block(width: 0pt)[])
  text(size: 8pt, fill: ink3)[#super(text(font: mono, size: 6.5pt, fill: oxblood)[#n]) #body]
}

#let slab(title, body) = block(
  width: 100%, fill: panel, inset: (x: 11pt, y: 10pt), radius: 0pt,
)[
  #kicker(title) #v(3pt) #body
]

// ════════════════════════════════════════════════════════ cover
#v(14mm)

#grid(columns: (auto, 1fr), column-gutter: 12pt, align: horizon,
  // the C¹ mark, drawn as paths — same construction as the favicon
  box(width: 30pt, height: 27pt)[
    #place(top + left)[
      #polygon.regular(vertices: 3, size: 0pt) // spacer, keeps layout honest
    ]
    #text(font: serif, size: 34pt, fill: ink)[C#super(text(size: 13pt, fill: oxblood)[1])]
  ],
  [],
)

#v(2mm)
#text(size: 42pt)[A course compiler#text(fill: oxblood)[.]]

#v(3mm)
#block(width: 118mm)[
  #set text(size: 12pt, fill: ink2)
  #set par(leading: 0.68em, justify: false)
  One chapter PDF in. A cited concept graph, a lesson, an assessment and four
  typeset documents out — with every factual sentence traceable to the exact
  span of the page it came from.
]

#v(5mm)
#line(length: 100%, stroke: 0.6pt + ink)
#v(2mm)

#grid(columns: (1fr, 1fr, 1fr, 1fr), column-gutter: 10pt,
  [#kicker[Source] #linebreak() #text(size: 9pt)[6,846 lines · 42 modules]],
  [#kicker[Tests] #linebreak() #text(size: 9pt)[247 · 100% core branches]],
  [#kicker[Passes] #linebreak() #text(size: 9pt)[10, each owning its column]],
  [#kicker[Targets] #linebreak() #text(size: 9pt)[4 PDFs + a certificate]],
)

#v(8mm)

// ════════════════════════════════════════════════════════ the pitch
= In one paragraph

Ask a language model to "make a lesson from this chapter" and you get prose
that reads fine and cannot be checked. You cannot point at a sentence and say
why it is true, you cannot separate a hallucinated formula from a real one,
and re-running it changes the output for reasons nobody can name. *CourseC
treats course generation as a compilation problem instead.* The source chapter
is the input language; a typed Concept Graph in SQLite is the intermediate
representation; ten independently-tested passes transform it; four typeset
documents are the code generation step. Every claim that reaches a page carries
the citation it was derived from, every number is executed rather than written,
and any pass that violates one of seven invariants fails the build rather than
degrading quietly.

#v(2mm)
#slab[Who it is for][
  #set text(size: 9.2pt, fill: ink2)
  An instructor or curriculum team who has the source material and needs
  defensible teaching artifacts from it — where "defensible" means a colleague
  can challenge any sentence and be shown the page it came from, and a wrong
  number is caught by the build rather than by a student.
]

= The problem, stated precisely

A single prompt produces a *plausible artifact with no audit surface*. Three
specific failures follow, and each one is a silent failure — the output looks
identical whether or not it happened.

#v(1mm)
#grid(columns: (1fr, 1fr, 1fr), column-gutter: 11pt,
  [#kicker[Unfalsifiable prose] #v(2pt)
   #text(size: 9pt, fill: ink3)[A claim with no citation cannot be checked, only believed. Reviewing it means re-reading the source yourself — the work the tool was supposed to do.]],
  [#kicker[Invented numbers] #v(2pt)
   #text(size: 9pt, fill: ink3)[A model that writes "#code[m·a = 5]" for #code[m=2, a=3] is producing text that pattern-matches arithmetic without performing any.]],
  [#kicker[Unstable output] #v(2pt)
   #text(size: 9pt, fill: ink3)[Re-running changes the result for untraceable reasons, so nothing can be regression-tested and no fix can be shown to hold.]],
)

= The thesis

#grid(columns: (auto, 1fr), column-gutter: 14pt,
  block(width: 52mm)[
    #set text(size: 9pt)
    #kicker[The mapping] #v(3pt)
    #table(columns: (auto, auto), stroke: none, inset: (x: 0pt, y: 3pt),
      column-gutter: 7pt,
      text(fill: ink3)[source chapter], [input language],
      text(fill: ink3)[Concept Graph], [the IR],
      text(fill: ink3)[ten passes], [compiler passes],
      text(fill: ink3)[four PDFs], [code generation],
      text(fill: ink3)[certificate], [build log],
    )
  ],
  [
    Compiling instead of prompting buys three things a prompt chain cannot.

    *A typed intermediate representation.* Every fact the system knows is a row
    in a real schema — a #code[Concept], a #code[SourceSpan], a #code[WebEvidence]
    chunk — not a paragraph one model wrote for another model to re-read. The
    graph can be queried, diffed, and rendered without asking a model anything.

    *Passes that own their columns.* #code[ingest] only ever writes #code[Block]
    and #code[SourceSpan]\; #code[compose] only ever writes #code[LessonBlock].
    This is enforced at runtime, not documented: #code[Graph.add()] looks up the
    declared owner for a row's type and raises #code[OwnershipViolation] if the
    writing pass is not it. A pass genuinely cannot write outside its lane.

    *Diagnostics instead of vibes.* Every pass reports through a structured
    #code[Diagnostic] carrying severity, code and message, rather than printing
    or swallowing problems. One error-severity diagnostic anywhere means the
    build emits no PDF at all.
  ],
)

= The seven invariants

These are not aspirations in a design document. Each one is the thing an
adversarial test exists to break, and each has a named mechanism behind it.

#v(1mm)
#table(
  columns: (auto, 1fr, 88mm),
  stroke: none,
  inset: (x: 0pt, y: 6pt),
  column-gutter: 9pt,
  table.hline(stroke: 0.6pt + ink),
  [#kicker[#h(1pt)]], [#kicker[Invariant]], [#kicker[What enforces it]],
  table.hline(stroke: 0.4pt + rule),

  text(fill: oxblood, size: 11pt)[I1], [*No unsupported sentence.* Every emitted sentence carries a citation and passes entailment against the spans it cites.],
  text(size: 8.8pt, fill: ink3)[A critic that sees one sentence and *only that sentence's own cited spans*, looked up fresh by id — never the dossier that produced it. Fails → repair twice → drop → quarantine the slot.],
  table.hline(stroke: 0.4pt + rule),

  text(fill: oxblood, size: 11pt)[I2], [*Numbers are computed, never generated.* Every numeric literal traces to a span, a cited source, or a sandbox result.],
  text(size: 8.8pt, fill: ink3)[A worked example's #code[formula], #code[substitutions] and #code[claimed_result] are executed as SymPy in a subprocess with a timeout, and compared. A linter then scans surviving prose for stray numerals.],
  table.hline(stroke: 0.4pt + rule),

  text(fill: oxblood, size: 11pt)[I3], [*Provenance is total.* Every node reachable from a document traces to file or URL, locator, retrieval time and content hash.],
  text(size: 8.8pt, fill: ink3)[#code[SourceSpan] carries the file, page, #code[bbox], #code[char_range] and #code[sha256], with a running character offset across the whole document — byte ranges, not "somewhere in the PDF".],
  table.hline(stroke: 0.4pt + rule),

  text(fill: oxblood, size: 11pt)[I4], [*The prerequisite graph is acyclic.* Cycles break by a deterministic, logged policy — never silently.],
  text(size: 8.8pt, fill: ink3)[Repeatedly remove a cycle's lowest-confidence edge, tie-broken on a *content hash* rather than a random id — a random tie-break would make cycle-breaking irreproducible, defeating the point.],
  table.hline(stroke: 0.4pt + rule),

  text(fill: oxblood, size: 11pt)[I5], [*Contract before emission.* A concept enters a document only with a contract status attached.],
  text(size: 8.8pt, fill: ink3)[Five slots: definition, intuition, worked example, visual-or-analogy, ≥2 assessment items. A concept short of them is marked #text(fill: ochre)[partial] and lists its unmet slots, never silently emitted as complete.],
  table.hline(stroke: 0.4pt + rule),

  text(fill: oxblood, size: 11pt)[I6], [*Contradiction is surfaced, never resolved.* The source stays primary.],
  text(size: 8.8pt, fill: ink3)[A numeric mismatch between retrieved evidence and the source writes a #code[contradicts] edge and a marked note carrying both citations. The source row is never overwritten.],
  table.hline(stroke: 0.4pt + rule),

  text(fill: oxblood, size: 11pt)[I7], [*Licence-clean media only.*],
  text(size: 8.8pt, fill: ink3)[An image is a bounding-box crop of the source page, generated diagram code (Mermaid), or nothing. No third image path exists in the code.],
  table.hline(stroke: 0.6pt + ink),
)

#v(3mm)
#slab[The rule that makes the rest credible][
  #set text(size: 9.2pt, fill: ink2)
  *A gate that never rejects anything is broken, not perfect.* The pipeline
  asserts its aggregate item-rejection rate is nonzero, and the adversarial
  suite is strengthened whenever it passes on first write.
]

= The intermediate representation

Everything the compiler knows lives in one SQLite database as typed SQLModel
tables. Every node carries #code[id], #code[created_by_pass], #code[content_hash]
and #code[created_at]\; #code[created_by_pass] is immutable after first write, so
provenance of *who created a row* cannot drift after the fact.

#v(2mm)
#grid(columns: (1fr, 1fr), column-gutter: 12pt,
  [
    #kicker[Node types] #v(3pt)
    #set text(size: 8.6pt)
    #table(columns: (auto, 1fr), stroke: none, inset: (x: 0pt, y: 2.6pt), column-gutter: 7pt,
      code[Block], text(fill: ink3)[a classified unit of a page],
      code[SourceSpan], text(fill: ink3)[exactly where it came from],
      code[Concept], text(fill: ink3)[definition, formula, procedure, …],
      code[Alias], text(fill: ink3)[a name that merged into a concept],
      code[SyllabusNode], text(fill: ink3)[a curriculum entry to anchor to],
      code[WebEvidence], text(fill: ink3)[a retrieved, scored, tiered chunk],
      code[LessonBlock], text(fill: ink3)[one contract slot, with citations],
      code[Verdict], text(fill: ink3)[the critic's judgement on a sentence],
      code[ExecResult], text(fill: ink3)[what the sandbox actually computed],
      code[Item], text(fill: ink3)[an assessment item and its gate history],
      code[Misconception], text(fill: ink3)[faulty reasoning a distractor encodes],
      code[ItemStats], text(fill: ink3)[difficulty and discrimination],
      code[Mastery], text(fill: ink3)[a student's posterior, append-only],
    )
  ],
  [
    #kicker[Ownership, enforced] #v(3pt)
    #block(fill: panel, inset: (x: 9pt, y: 8pt), width: 100%)[
      #set text(font: mono, size: 7.8pt, fill: ink2)
      #set par(justify: false, leading: 0.55em)
      >>> graph.add(Block(\ #h(4pt)created_by_pass="understand", ...)) \
      #text(fill: oxblood)[OwnershipViolation: Block is owned] \
      #text(fill: oxblood)[by pass 'ingest', not 'understand']
    ]
    #v(4pt)
    #set text(size: 8.8pt, fill: ink3)
    #set par(justify: true)
    The same check covers edges: #code[Edge.kind] is a closed enum, and each kind
    declares its owning pass. #code[prerequisite_of] and #code[part_of] belong to
    #code[structure]\; #code[contradicts] to #code[evidence]\; #code[assesses] to
    #code[assess]. #code[evidenced_by] is deliberately shared between
    #code[evidence] and #code[compose] — it is the same relationship at two
    stages, so it gets one kind rather than two near-duplicates.

    #v(3pt)
    An edge whose endpoints are not already in the graph is refused outright,
    which makes a dangling citation structurally impossible rather than merely
    unlikely.
  ],
)

= The pipeline, pass by pass

Run end to end by #code[coursec build chapter.pdf]. Each pass is independently
unit-tested against a scripted model backend — none of the tests need a live
model or a network connection.

#v(2mm)

#let pass(n, name, writes, body) = {
  grid(columns: (18mm, 1fr), column-gutter: 8pt,
    [#text(font: mono, size: 8pt, fill: ink4)[#n] #linebreak()
     #text(size: 9.5pt, fill: oxblood)[#name]],
    [#text(size: 7.2pt, font: mono, fill: ink4)[WRITES #writes] #v(2pt)
     #set text(size: 9pt, fill: ink2); #body],
  )
  v(2pt); line(length: 100%, stroke: 0.4pt + rule); v(3pt)
}

#line(length: 100%, stroke: 0.6pt + ink)
#v(3pt)

#pass("01", "ingest", "Block, SourceSpan")[
  PyMuPDF yields raw text and image blocks; a typography heuristic classifies
  each one — font size against the body-text baseline, bullet prefixes, a regex
  for #code[Figure N.M] captions — into heading, paragraph, figure, caption,
  table, equation or list. Captions bind to figures by sequence first and
  geometry second; an unbound figure raises a diagnostic rather than passing
  silently.
]

#pass("02", "understand", "Concept, Alias, SyllabusNode")[
  Extraction is *one model call per section*, never per paragraph and never per
  pair — a chapter has dozens of paragraphs but a handful of subsections, and
  calling a model in a loop over more than that is the cost anti-pattern the
  design forbids. Canonicalisation is then model-free: candidates are embedded
  locally and agglomeratively clustered. The merge threshold of #num[0.80] is not
  a guess — it is tuned against a committed labelled fixture where the pair that
  must merge ("Ohm's law" / "V = IR") embeds at #num[0.855], and the closest
  false-positive risks ("variance"/"covariance" at #num[0.764],
  "hypothesis"/"theory" at #num[0.773]) both sit below #num[0.78].
]

#pass("03", "structure", "prerequisite_of, part_of")[
  An edge exists only when *two independent signals agree*: concept A's
  definition text mentions concept B by name, #emph[and] a pairwise model
  judgement — run only over candidate pairs, never all #emph[O(n²)] — agrees on
  the direction. Cycles are then broken deterministically (I4).
]

#pass("04", "gap", "GapVector, RetrievalBudget (transient)")[
  Four fields per concept: coverage, depth, modality and prerequisite. Budget is
  allocated proportionally under a hard global cap. A fully-covered chapter
  allocates a budget of *zero* — deciding not to search is a valid outcome, not
  a missing feature — and the allocation is provably scale-invariant.
]

#pass("05", "evidence", "WebEvidence, evidenced_by, contradicts")[
  Real retrieval: #code[httpx], #code[robots.txt] honoured via
  #code[urllib.robotparser] rather than parsed and ignored, per-domain rate
  limiting, and BeautifulSoup stripping navigation before chunking by heading.
  Admission is policy, not judgement: present-in-source or corroborated by ≥2
  independent tier-1 domains → admitted; a single tier-1 source → #text(fill: ochre)[weak],
  usable for enrichment but *never as the sole citation for a formula or
  definition*; anything else → rejected with a reason.
]

#pass("06", "compose", "LessonBlock, evidenced_by")[
  Each generative slot is one model call constrained to a #emph[dossier]: the
  concept's source span, its admitted evidence best-first, and one-line
  prerequisite summaries. *If the dossier has no grounding material, generation
  refuses before the backend is ever called* — a code-level guard, not a prompt
  instruction hoping the model behaves, which is what makes "strip the dossier
  and confirm it refuses" a deterministic test rather than a hope.
]

#pass("07", "verify", "Verdict, ExecResult")[
  The critic is blind to everything except the sentence and its own citations
  (I1). Separately, every worked example is executed (I2). Every judgement —
  including intermediate repair attempts — is a stored row, not just the final
  one, so the history of a claim survives.
]

#pass("08", "assess", "Item, Misconception, ItemStats")[
  One item per concept × Bloom level, typed by the concept's kind. *Every MCQ
  distractor must link to a real misconception node*, sourced from sentences the
  critic actually marked contradicted. Four blocking gates run in full even after
  an early failure, so the rejection log shows every reason: independent key
  verification (5 samples, majority must agree), leakage (answerable with no
  course context means it tests trivia), single-answer (every distractor must be
  defensibly wrong), and numeric execution. Survivors face a synthetic pilot:
  #num[12] simulated students on a fixed ability grid, and a from-scratch 2PL IRT
  fit flags degenerate items. This is labelled #emph[screening], never
  calibration — twelve students cannot support a calibration claim, and the
  wording is not allowed to drift.
]

#pass("09", "emit", "rendered targets")[
  Typst markup assembled from the graph. The cheat sheet is a pure template over
  each concept's own span, so it makes *zero* model calls — asserted on the
  cache-miss counter. The question paper selects greedily against a blueprint of
  marks, Bloom mix and unit weightage; an infeasible blueprint names its binding
  constraint rather than returning something merely close. A fixed compile
  timestamp makes "identical IR → byte-identical PDF" a checked property.
]

#pass("10", "learn", "Mastery")[
  Bayesian Knowledge Tracing as one closed-form update, pure and free of I/O so a
  property test can hold it to the textbook formula directly. A wrong answer does
  not stop at "this concept is weak": #code[diagnose_root_cause] walks
  prerequisite edges upstream, following the weakest still-weak prerequisite, and
  stops at the deepest one — because remediating a concept is wasted effort if
  the real gap is two levels below it.
]

= The stack, and why each piece

#v(1mm)
#table(
  columns: (auto, 1fr),
  stroke: none, inset: (x: 0pt, y: 5pt), column-gutter: 10pt,
  table.hline(stroke: 0.6pt + ink),
  [#kicker[Choice]], [#kicker[Why this one]],
  table.hline(stroke: 0.4pt + rule),
  code[SQLModel + SQLite], [The IR must be queryable, diffable and inspectable without a server. SQLModel gives Pydantic validation and SQL in one type, so the schema *is* the documentation. One file is the whole build output.],
  table.hline(stroke: 0.4pt + rule),
  code[PyMuPDF], [Gives per-block bounding boxes and font metrics, which is what makes both the typography classifier and licence-clean figure cropping (I7) possible. A plain text extractor would lose exactly the data the invariants need.],
  table.hline(stroke: 0.4pt + rule),
  code[sentence-transformers], [Canonicalisation and retrieval scoring run *locally*, so the expensive, non-deterministic model is reserved for judgement calls and never used for similarity.],
  table.hline(stroke: 0.4pt + rule),
  code[SymPy], [Executes a claimed calculation in a subprocess with a timeout. This is what turns I2 from a promise into arithmetic that either agrees or does not.],
  table.hline(stroke: 0.4pt + rule),
  code[Typst], [Programmatic typesetting with a deterministic compile — the fixed-timestamp property that makes byte-identical output testable. A LaTeX toolchain would be heavier and harder to pin.],
  table.hline(stroke: 0.4pt + rule),
  code[Anthropic API], [The one production model backend, injected as an explicit argument everywhere. It fails loudly when unconfigured and never fabricates — there is no code path where a pass can quietly reach for a live model.],
  table.hline(stroke: 0.4pt + rule),
  code[FastAPI + uvicorn], [A read-mostly HTTP surface over the graph. Chosen for typed request models that match the Pydantic already in use, so the API shares the IR's validation rather than duplicating it.],
  table.hline(stroke: 0.4pt + rule),
  code[httpx + BeautifulSoup], [Real fetching with real politeness — robots.txt, rate limits, navigation stripping — because "retrieval" that ignores those is a demo, not a system.],
  table.hline(stroke: 0.4pt + rule),
  code[Hypothesis], [Property tests over generated input, for the invariants that must hold for *any* graph rather than one fixture.],
  table.hline(stroke: 0.4pt + rule),
  code[Typer · ruff · uv], [A CLI that documents itself, one linter, and reproducible locked environments.],
  table.hline(stroke: 0.6pt + ink),
)

= How it is proven

Four kinds of test, and every pass writes whichever apply.

#v(2mm)
#grid(columns: (1fr, 1fr), column-gutter: 12pt, row-gutter: 7pt,
  [#kicker[Unit] #v(2pt) #text(size: 8.8pt, fill: ink3)[Pure functions and schema validation — domain-tier classification, gap-vector arithmetic, the BKT step.]],
  [#kicker[Property] #v(2pt) #text(size: 8.8pt, fill: ink3)[Invariants over generated input: *the prerequisite graph is acyclic after cycle-breaking, for any graph*.]],
  [#kicker[Golden] #v(2pt) #text(size: 8.8pt, fill: ink3)[A committed snapshot of ingest against a real, openly-licensed fixture chapter. Diffs are reviewed, never blanket-regenerated.]],
  [#kicker[Adversarial] #v(2pt) #text(size: 8.8pt, fill: ink3)[Poisoned input the system must catch on purpose: a low-authority page asserting a wrong constant, an MCQ with two defensible answers, arithmetic that does not hold.]],
)

#v(3mm)
#grid(columns: (1fr, auto), column-gutter: 14pt, align: horizon,
  [
    Above those sits a *demo harness* (#code[coursec demo]) that answers a
    different question than the per-pass tests do: not "does each pass catch its
    own defect in isolation", but "is the same planted defect still caught once
    the real passes are wired together the way a build wires them". Five
    scenarios, each scored against the specific diagnostic code its invariant
    promises to raise — never against "the build did not crash". A scenario that
    raises an unexpected exception is reported as its own #emph[error] status,
    distinct from a scenario that ran clean and failed to catch its defect, so an
    environment problem can never be mistaken for a regression.
  ],
  block(width: 46mm)[
    #set text(size: 8.6pt)
    #kicker[By the numbers] #v(4pt)
    #table(columns: (1fr, auto), stroke: none, inset: (x: 0pt, y: 3pt),
      text(fill: ink3)[tests], num[247],
      text(fill: ink3)[core branch coverage], num[100%],
      text(fill: ink3)[source lines], num[6,846],
      text(fill: ink3)[test lines], num[4,359],
      text(fill: ink3)[modules], num[42],
    )
    #v(3pt)
    #text(size: 7.6pt, fill: ink4, style: "italic")[Coverage is enforced at 100% on the core IR specifically, where a silent regression would corrupt every pass downstream.]
  ],
)

= What ships

#grid(columns: (1fr, 1fr), column-gutter: 12pt,
  [
    #kicker[Command line] #v(3pt)
    #set text(size: 8.8pt)
    #table(columns: (auto, 1fr), stroke: none, inset: (x: 0pt, y: 3.4pt), column-gutter: 8pt,
      code[build], text(fill: ink3)[compile a chapter end to end],
      code[web], text(fill: ink3)[serve the frontend and its API],
      code[quiz], text(fill: ink3)[adaptive session in the terminal],
      code[serve], text(fill: ink3)[the Streamlit quiz],
      code[demo], text(fill: ink3)[the adversarial harness],
    )
    #v(5pt)
    #kicker[Documents] #v(3pt)
    #set text(size: 8.8pt, fill: ink3)
    #table(columns: (auto, 1fr), stroke: none, inset: (x: 0pt, y: 3.4pt), column-gutter: 8pt,
      text(fill: ink)[Booklet], [every contract slot, diagrams rendered at build time, figures cropped by stored bbox, bibliography walked from citation edges],
      text(fill: ink)[Cheat sheet], [a pure template over source spans — zero model calls],
      text(fill: ink)[Question paper], [greedy selection against a blueprint],
      text(fill: ink)[Answer key], [reads stored execution results rather than recomputing],
      text(fill: ink)[Certificate], [written *either way* — it is the artifact that explains a failure],
    )
  ],
  [
    #kicker[The web surface] #v(3pt)
    #set text(size: 9pt, fill: ink2)
    Four views over a live graph: an overview, a concept-graph explorer laid out
    by prerequisite depth, the adaptive quiz, and the invariant ledger. The quiz
    endpoints call the learn pass rather than reimplementing its mathematics in a
    request handler, and an item's key is never sent to the browser until an
    answer is in.

    #v(4pt)
    The certificate view is *recomputed, never replayed*: a build's diagnostics
    are not persisted, so the API derives each row from the stored verdicts,
    execution results and gate outcomes — and says so in its own response rather
    than implying it read a log it never saw.

    #v(6pt)
    #kicker[Design language] #v(3pt)
    #set text(size: 9pt, fill: ink2)
    #emph[Editorial Ink] — warm paper, one oxblood accent, and two semantic hues
    at matched lightness. The organising idea is that *the margin is a
    first-class column*, because marginalia is precisely what this compiler's
    output is: a claim in the body lights its own source in the margin. The mark
    is a serif #emph[C] with a superscript numeral — a compiled claim carries its
    citation.
  ],
)

= What is deliberately not claimed

Credibility depends on being exact about the edges of the work.

#v(2mm)
#table(columns: (auto, 1fr), stroke: none, inset: (x: 0pt, y: 5pt), column-gutter: 10pt,
  table.hline(stroke: 0.6pt + ink),
  [#kicker[Limit]], [#kicker[The honest version]],
  table.hline(stroke: 0.4pt + rule),
  [Search backend], [The retrieval machinery is real — fetching, robots, tiering, admission, contradiction — but no search provider is wired in by default. Without one it stops cleanly rather than fabricating results.],
  table.hline(stroke: 0.4pt + rule),
  [Pilot statistics], [#num[12] simulated students is a #emph[screen] for degenerate items, not calibration, and every output string says so.],
  table.hline(stroke: 0.4pt + rule),
  [BKT parameters], [Illustrative defaults, not fit to any real cohort. The update rule is the textbook one; the four constants are not evidence about your students.],
  table.hline(stroke: 0.4pt + rule),
  [Demo-grade components], [The block classifier, the token-budget proxy and the numeric-mismatch check are heuristics, and each is marked as such in the source rather than dressed up.],
  table.hline(stroke: 0.4pt + rule),
  [Remediation content], [Mastery tracking and root-cause diagnosis ship; *generating* remediation for a diagnosed gap does not. The edge kind is declared and unused, and the README says so.],
  table.hline(stroke: 0.6pt + ink),
)

= Where it goes next

#grid(columns: (1fr, 1fr, 1fr), column-gutter: 11pt,
  [#kicker[Near] #v(2pt) #text(size: 9pt, fill: ink3)[Wire a search provider, turning correct retrieval machinery into retrieval that actually crawls. Generate remediation against diagnosed root causes, closing the loop the learn pass opens.]],
  [#kicker[Structural] #v(2pt) #text(size: 9pt, fill: ink3)[Multi-chapter builds, where the prerequisite graph spans a whole course and the distance from a concept to its foundations becomes a curriculum-design signal rather than a per-chapter one.]],
  [#kicker[The interesting one] #v(2pt) #text(size: 9pt, fill: ink3)[Real cohort data makes the pilot a calibration rather than a screen, and the BKT constants something fitted rather than assumed — at which point the honest caveats above can be retired one at a time, with evidence.]],
)

#v(4mm)
#line(length: 100%, stroke: 0.6pt + ink)
#v(1.5mm)
#grid(columns: (12mm, 1fr), column-gutter: 10pt, align: horizon,
  [#text(font: serif, size: 17pt)[C#super(text(size: 7pt, fill: oxblood)[1])]],
  [#set text(size: 9.5pt, fill: ink2)
   #set par(justify: false)
   #emph[This document was typeset by Typst — the same engine, and the same code path, that CourseC uses to render its own booklets and question papers.]],
)
