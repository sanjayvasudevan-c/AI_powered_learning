/* ═══════════════════════════════════════════════════════════════════════
   CourseC — Editorial Ink, wired to the real Concept Graph.

   No framework and no build step on purpose: this ships inside a Python
   package and is served by FastAPI from `static/`, so a bundler would be
   one more thing to keep green for no gain at this size.

   Everything drawn from the API is escaped before it reaches innerHTML —
   concept names and lesson sentences come out of a compiled chapter, i.e.
   out of a PDF someone else wrote.
   ═══════════════════════════════════════════════════════════════════════ */

const API = {
  summary:     () => get('/api/summary'),
  graph:       () => get('/api/graph'),
  concept:     (id) => get(`/api/concepts/${encodeURIComponent(id)}`),
  certificate: () => get('/api/certificate'),
  quizNext:    (student, asked) =>
    get(`/api/quiz/next?student_id=${encodeURIComponent(student)}&asked=${asked.join(',')}`),
  quizAnswer:  (body) => post('/api/quiz/answer', body),
  mastery:     (student) => get(`/api/quiz/mastery?student_id=${encodeURIComponent(student)}`),
};

async function get(url) {
  const r = await fetch(url);
  return r.json();
}

async function post(url, body) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return r.json();
}

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const $ = (sel) => document.querySelector(sel);
const STUDENT = 'web';

/* ── the press line: ten passes, drawn from data ───────────────────── */

const PASSES = [
  'INGEST', 'UNDERSTAND', 'STRUCTURE', 'GAP', 'EVIDENCE',
  'COMPOSE', 'VERIFY', 'ASSESS', 'EMIT', 'LEARN',
];

function drawPressLine() {
  const host = document.querySelector('.pressline .stages');
  if (!host) return;
  host.innerHTML = PASSES.map((name, i) => {
    const x = 90 + i * 100;
    const above = i % 2 === 0;
    const labelY = above ? 86 : 140;
    const folioY = above ? 72 : 155;
    const folio = String(i + 1).padStart(2, '0');
    return `<g class="stage" style="animation-delay:${(1.4 + i * 1.1).toFixed(1)}s">
      <line x1="${x}" y1="101" x2="${x}" y2="119" stroke="currentColor" stroke-width="1.2"/>
      <text class="svg-label" x="${x}" y="${labelY}" text-anchor="middle">${name}</text>
      <text class="svg-folio" x="${x}" y="${folioY}" text-anchor="middle">${folio}</text>
    </g>`;
  }).join('');
}

/* ── reveal on scroll ──────────────────────────────────────────────── */

/* Opt in to the hidden-until-seen state only now that a script is running:
   see the `.js-reveal` note in app.css. */
document.documentElement.classList.add('js-reveal');

const revealer = new IntersectionObserver((entries) => {
  for (const e of entries) {
    if (e.isIntersecting) { e.target.classList.add('seen'); revealer.unobserve(e.target); }
  }
}, { rootMargin: '0px 0px -12% 0px' });

function watchReveals(root = document) {
  root.querySelectorAll('.reveal:not(.seen)').forEach((el) => revealer.observe(el));
}

/* ── claims light their own margin note ────────────────────────────── */

function wireClaims() {
  document.querySelectorAll('.claim').forEach((claim) => {
    const note = document.getElementById(claim.dataset.note);
    if (!note) return;
    const on = () => note.classList.add('lit');
    const off = () => note.classList.remove('lit');
    claim.addEventListener('mouseenter', on);
    claim.addEventListener('mouseleave', off);
    claim.addEventListener('focus', on);
    claim.addEventListener('blur', off);
    claim.tabIndex = 0;
  });
}

/* ── empty state ───────────────────────────────────────────────────── */

function emptyState(payload, what) {
  return `<div class="empty">
    <span class="eyebrow">No compiled chapter</span>
    <h2>Nothing has been through the press yet.</h2>
    <p>${esc(payload.reason || `There is no graph to read for ${what}.`)}</p>
    <p class="muted">Compile one with <code>coursec build path/to/chapter.pdf</code>, then reload —
    the server resolves the database per request, so it appears without a restart.</p>
  </div>`;
}

/* ═══ graph view ═══════════════════════════════════════════════════ */

let graphCache = null;

async function renderGraph() {
  const data = graphCache || (graphCache = await API.graph());
  const stage = $('#graph-canvas');

  if (!data.available) {
    $('#view-graph').querySelector('.app-grid').innerHTML = emptyState(data, 'the graph');
    return;
  }

  const byDepth = new Map();
  data.concepts.forEach((c) => {
    if (!byDepth.has(c.depth)) byDepth.set(c.depth, []);
    byDepth.get(c.depth).push(c);
  });

  const depths = [...byDepth.keys()].sort((a, b) => a - b);
  const colW = 210;
  const rowH = 118;
  const tallest = Math.max(1, ...[...byDepth.values()].map((v) => v.length));
  const width = Math.max(560, 120 + depths.length * colW);
  const height = Math.max(420, 90 + tallest * rowH);

  const pos = new Map();
  depths.forEach((d, di) => {
    const col = byDepth.get(d);
    col.forEach((c, ci) => {
      pos.set(c.id, {
        x: 90 + di * colW,
        y: 70 + (height - 120) * ((ci + 0.5) / col.length),
        c,
      });
    });
  });

  const edges = data.edges.map((e, i) => {
    const a = pos.get(e.source);
    const b = pos.get(e.target);
    if (!a || !b) return '';
    const mx = (a.x + b.x) / 2;
    return `<path class="gedge" style="animation-delay:${(0.1 + i * 0.05).toFixed(2)}s"
      d="M${a.x + 14} ${a.y} C ${mx} ${a.y}, ${mx} ${b.y}, ${b.x - 16} ${b.y}"
      fill="none" stroke="var(--ink-5)" stroke-width="1.1" marker-end="url(#tip)"/>`;
  }).join('');

  const guides = depths.map((d, di) =>
    `<line x1="${90 + di * colW}" y1="52" x2="${90 + di * colW}" y2="${height - 30}"
       stroke="var(--rule-soft)" stroke-width="1"/>
     <text class="svg-micro" x="${90 + di * colW}" y="40" text-anchor="middle">DEPTH ${d}</text>`
  ).join('');

  const nodes = data.concepts.map((c, i) => {
    const p = pos.get(c.id);
    const partial = !c.contract_complete;
    const mark = partial
      ? `<circle cx="${p.x}" cy="${p.y}" r="6.5" fill="var(--paper)" stroke="var(--ochre)" stroke-width="2"/>`
      : `<circle cx="${p.x}" cy="${p.y}" r="6.5" fill="var(--ink)"/>`;
    return `<g class="gnode" data-id="${esc(c.id)}" style="animation-delay:${(i * 0.05).toFixed(2)}s">
      ${mark}
      <text x="${p.x}" y="${p.y + 30}" text-anchor="middle">${esc(c.name)}</text>
      ${partial ? `<text class="svg-micro" x="${p.x}" y="${p.y + 46}" text-anchor="middle" fill="var(--ochre)">PARTIAL</text>` : ''}
    </g>`;
  }).join('');

  stage.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Prerequisite graph">
    <defs><marker id="tip" markerWidth="7" markerHeight="7" refX="6.4" refY="3" orient="auto">
      <path d="M0 .4 L6.4 3 L0 5.6 Z" fill="var(--ink-5)"/></marker></defs>
    <g>${guides}</g><g>${edges}</g><g>${nodes}</g></svg>`;

  $('#concept-index').innerHTML = data.concepts.map((c, i) => `
    <button class="index-row" data-id="${esc(c.id)}">
      <span class="n">${String(i + 1).padStart(2, '0')}</span>
      <span>${esc(c.name)}</span>
      ${c.contract_complete ? '' : '<span class="flag">PARTIAL</span>'}
    </button>`).join('');

  const linked = data.concepts.filter((c) => c.syllabus_linked).length;
  $('#graph-stats').innerHTML =
    `<div>SYLLABUS LINKED <span style="color:var(--ink)">${linked}/${data.concepts.length}</span></div>
     <div>PREREQUISITE EDGES <span style="color:var(--ink)">${data.edges.length}</span></div>
     <div class="ok">ACYCLIC ✓</div>`;

  document.querySelectorAll('[data-id]').forEach((el) => {
    el.addEventListener('click', () => selectConcept(el.dataset.id));
  });

  if (data.concepts.length) selectConcept(data.concepts[0].id);
}

async function selectConcept(id) {
  document.querySelectorAll('.index-row').forEach((r) =>
    r.setAttribute('aria-current', String(r.dataset.id === id)));

  const c = await API.concept(id);
  const host = $('#concept-detail');
  if (!c.available) { host.innerHTML = emptyState(c, 'this concept'); return; }

  const slots = c.slots.map((s) => {
    if (s.status === 'missing') {
      return `<div class="slab"><div class="slab-title">${esc(s.slot.replace(/_/g, ' '))}</div>
        <div class="muted" style="font-style:italic">not generated — slot unmet</div></div>`;
    }
    const body = s.sentences.map((sen) =>
      `${esc(sen.text)}${sen.citations.map((ci) =>
        `<span class="cite" title="${esc(ci.detail)}">${esc(ci.label)}</span>`).join('')}`
    ).join(' ');
    const comp = s.computation ? `<div class="codeblock" style="margin-top:10px">
        <div>${esc(s.computation.formula)}</div>
        <div class="muted">${esc(JSON.stringify(s.computation.substitutions))}</div>
        <div style="border-top:1px solid var(--rule);margin-top:6px;padding-top:6px">
          = ${esc(s.computation.claimed_result)}
          ${s.executed && s.executed.length
            ? (s.executed[0].agrees
                ? '<span class="ok">✓ sandbox agrees</span>'
                : '<span class="bad">✕ sandbox disagrees</span>')
            : ''}
        </div></div>` : '';
    const flag = s.status === 'quarantined' ? ' <span class="bad">· quarantined</span>' : '';
    return `<div class="slab">
      <div class="slab-title">${esc(s.slot.replace(/_/g, ' '))}${flag}</div>
      <div class="sentence">${body || '<span class="muted">every sentence was dropped</span>'}</div>
      ${comp}</div>`;
  }).join('');

  const prov = c.provenance
    ? `<div class="slab"><div class="slab-title">Provenance</div>
        <div class="codeblock">
          <div>${esc(c.provenance.file)} · p.${esc(c.provenance.page)} · chars ${esc((c.provenance.char_range || []).join('–'))}</div>
          <div class="muted">sha256 ${esc(String(c.provenance.sha256).slice(0, 12))}…</div>
        </div></div>`
    : '';

  const prereqs = c.prerequisites.length
    ? `<div class="slab"><div class="slab-title">Prerequisites</div>
        <div class="sentence">${c.prerequisites.map((p) =>
          `<a href="#" data-goto="${esc(p.id)}">${esc(p.name)}</a>`).join(' · ')}</div></div>`
    : '';

  host.innerHTML = `
    <div>
      <div class="slab-title">Selected</div>
      <div class="detail-name">${esc(c.name)}</div>
      <div class="meta-row">
        <span>${esc(c.type)}</span>
        <span>salience <span style="color:var(--ink)">${esc(c.salience)}</span></span>
        <span class="${c.contract_complete ? 'ok' : 'warn'}">
          contract ${c.contract_complete ? 'complete' : esc(c.unmet_slots.join(', '))}</span>
      </div>
      ${c.syllabus ? `<div class="meta-row"><span>syllabus ${esc(c.syllabus.code)} — ${esc(c.syllabus.title)}</span></div>` : ''}
    </div>
    <div class="hr"></div>
    ${slots}${prereqs}${prov}`;

  host.querySelectorAll('[data-goto]').forEach((a) =>
    a.addEventListener('click', (e) => { e.preventDefault(); selectConcept(a.dataset.goto); }));
}

/* ═══ quiz view ════════════════════════════════════════════════════ */

const quiz = { asked: [], current: null, answered: false };

async function renderQuiz() {
  const data = await API.quizNext(STUDENT, quiz.asked);
  const stage = $('#quiz-stage');

  if (!data.available) {
    $('#view-quiz').querySelector('.quiz-grid').innerHTML = emptyState(data, 'the quiz');
    return;
  }

  if (!data.item) {
    stage.innerHTML = `<div class="empty" style="padding-left:0">
      <span class="eyebrow">Session complete</span>
      <h2>Every accepted item has been asked.</h2>
      <p>${esc(data.reason || '')}</p></div>`;
    await renderLedger();
    return;
  }

  quiz.current = data.item;
  quiz.answered = false;

  const options = data.item.options.length
    ? `<div class="options">${data.item.options.map((o, i) => `
        <button class="option" data-answer="${esc(o.text)}">
          <span class="letter">${'ABCDEFGH'[i]}</span>
          <span>${esc(o.text)}</span>
        </button>`).join('')}</div>`
    : `<div class="answer-field">
        <input id="free-answer" type="text" placeholder="Type your answer" autocomplete="off">
        <button class="btn" id="free-submit">Submit</button></div>`;

  stage.innerHTML = `
    <div class="quiz-concept">
      <span class="eyebrow">Assesses</span>
      <span class="name">${esc(data.item.concept.name)}</span>
      <span class="eyebrow">Bloom · ${esc(data.item.bloom_level)}</span>
    </div>
    <h2 class="quiz-stem">${esc(data.item.stem)}</h2>
    ${options}
    <div id="quiz-feedback"></div>`;

  stage.querySelectorAll('.option').forEach((b) =>
    b.addEventListener('click', () => submitAnswer(b.dataset.answer)));
  const free = $('#free-submit');
  if (free) {
    free.addEventListener('click', () => submitAnswer($('#free-answer').value));
    $('#free-answer').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') submitAnswer(e.target.value);
    });
  }

  await renderLedger();
}

async function submitAnswer(answer) {
  if (quiz.answered) return;
  quiz.answered = true;

  const res = await API.quizAnswer({
    item_id: quiz.current.id, answer, student_id: STUDENT,
  });
  quiz.asked.push(quiz.current.id);

  document.querySelectorAll('.option').forEach((b) => {
    b.disabled = true;
    if (b.dataset.answer === answer) {
      b.classList.add(res.correct ? 'key' : 'chosen');
      b.insertAdjacentHTML('beforeend', '<span class="tag">YOUR ANSWER</span>');
    } else if (b.dataset.answer === res.key) {
      b.classList.add('key');
      b.insertAdjacentHTML('beforeend', '<span class="tag">KEY</span>');
    }
  });

  const feedback = $('#quiz-feedback');
  feedback.innerHTML = `
    ${res.correct
      ? `<p class="ok" style="font-size:19px;margin-top:26px">Correct.</p>`
      : `<p style="font-size:19px;margin-top:26px">Not quite — the key was
           <span class="ok">${esc(res.key)}</span>.</p>`}
    ${res.misconception ? `<div class="misconception">
      <span class="glyph">✻</span>
      <div><div class="slab-title">The misconception this distractor encodes</div>
      <div class="sentence">${esc(res.misconception)}</div></div></div>` : ''}
    <button class="btn" id="next-q" style="margin-top:28px">Next question
      <svg width="15" height="10" viewBox="0 0 15 10" fill="none"><path d="M0 5h13M9 1l4 4-4 4" stroke="currentColor" stroke-width="1.3"/></svg>
    </button>`;
  $('#next-q').addEventListener('click', renderQuiz);

  await renderLedger(res.root_cause);
}

async function renderLedger(rootCause) {
  const m = await API.mastery(STUDENT);
  const host = $('#quiz-ledger');
  if (!m.available) { host.innerHTML = ''; return; }

  const root = rootCause ? `
    <div class="rootcause">
      <div class="slab-title">Root cause</div>
      <div style="font-family:var(--display);font-size:26px;line-height:1.2;margin-top:8px">
        This traces back to<br><span style="color:var(--accent)">${esc(rootCause.name)}</span>
      </div>
      <p style="font-size:15.5px;line-height:1.5;color:var(--ink-3);margin:10px 0 0">
        ${rootCause.depth} prerequisite level${rootCause.depth === 1 ? '' : 's'} back, still at
        <b>${esc(rootCause.mastery)}</b>. Its own prerequisites are solid, so the gap is there —
        not further upstream.</p>
    </div><div class="hr"></div>` : '';

  const rows = m.concepts.map((c, i) => {
    const tone = c.mastery >= 0.7 ? 'var(--moss)'
      : c.mastery >= m.weak_threshold ? 'var(--ochre)'
      : c.observed ? 'var(--accent)' : 'var(--ink-5)';
    return `<div style="margin-bottom:14px">
      <div class="mrow">
        <span${c.observed ? '' : ' class="muted"'}>${esc(c.name)}</span>
        <span class="v" style="color:${tone}">${c.mastery.toFixed(2)}</span>
      </div>
      <div class="bar-track"><div class="bar-fill"
        style="width:${(c.mastery * 100).toFixed(0)}%;background:${tone};animation-delay:${(i * 0.06).toFixed(2)}s"></div></div>
    </div>`;
  }).join('');

  host.innerHTML = `${root}
    <div class="slab-title">Mastery · BKT posterior</div>
    <div style="margin-top:14px">${rows}</div>
    <p class="aside-italic" style="font-size:13.5px;color:var(--ink-5);margin-top:14px">
      Unasked concepts sit at the prior (${m.prior}), not at zero — silence is not evidence of mastery.</p>`;
}

/* ═══ certificate view ═════════════════════════════════════════════ */

async function renderCertificate() {
  const c = await API.certificate();
  const host = $('#certificate-body');

  if (!c.available) { host.innerHTML = emptyState(c, 'the certificate'); return; }

  const broken = c.ledger.filter((l) => !l.held);
  const rows = c.ledger.map((l) => `
    <div class="ledger-row${l.held ? '' : ' broken'}">
      <span class="c">${esc(l.code)}</span>
      <span class="t">${esc(l.title)}</span>
      <span class="m">${esc(l.measured)}</span>
      <span class="s ${l.held ? 'ok' : 'bad'}">${l.held ? 'HELD' : 'BROKEN'}</span>
    </div>`).join('');

  const stamp = c.emission_withheld
    ? `<div class="stamp"><div class="k">EMISSION</div><div class="v">WITHHELD</div>
       <div class="k">${esc(broken.map((b) => b.code).join(' · ') || 'QUARANTINE')}</div></div>`
    : `<div class="stamp" style="border-color:var(--moss);color:var(--moss)">
       <div class="k">EMISSION</div><div class="v">CLEARED</div><div class="k">ALL SEVEN HELD</div></div>`;

  const m = c.measured;
  host.innerHTML = `
    <main class="cert-main">
      <div class="cert-head">
        <div>
          <span class="eyebrow">Attestation · § 001</span>
          <h1>${c.emission_withheld
            ? `An invariant did not hold.<br><span style="color:var(--accent)">No PDF.</span>`
            : `Every invariant held.<br><span style="color:var(--moss)">Cleared to print.</span>`}</h1>
          <p style="font-size:17px;line-height:1.55;color:var(--ink-3);margin:16px 0 0;max-width:560px">
            ${esc(c.derived_from)}</p>
        </div>
        ${stamp}
      </div>
      <div style="margin-top:40px">
        <div class="eyebrow" style="padding-bottom:12px;border-bottom:1px solid var(--rule)">Invariant ledger</div>
        ${rows}
      </div>
    </main>
    <aside class="cert-side">
      <div class="eyebrow">Measured</div>
      <div class="stat-grid">
        <div class="stat"><div class="n">${m.concepts}</div><div class="k">Concepts</div></div>
        <div class="stat"><div class="n">${m.verdicts}</div><div class="k">Critic verdicts</div></div>
        <div class="stat"><div class="n">${m.items_accepted}</div><div class="k">Items accepted</div></div>
        <div class="stat"><div class="n${m.items_rejected ? '' : ''}" style="color:${m.items_rejected ? 'var(--accent)' : 'inherit'}">${m.items_rejected}</div><div class="k">Items rejected</div></div>
      </div>
      <div class="hr"></div>
      <div class="eyebrow">Evidence admission</div>
      <div style="margin-top:12px;display:flex;flex-direction:column;gap:9px;font-size:15.5px">
        <div style="display:flex;justify-content:space-between"><span>Admitted</span><span class="v" style="font-family:var(--mono);font-size:12px">${m.evidence_admitted}</span></div>
        <div style="display:flex;justify-content:space-between"><span>Rejected</span><span class="v" style="font-family:var(--mono);font-size:12px">${m.evidence_rejected}</span></div>
        <div style="display:flex;justify-content:space-between"><span>Quarantined items</span><span class="v" style="font-family:var(--mono);font-size:12px">${m.items_quarantined}</span></div>
      </div>
      <div class="hr"></div>
      <div class="eyebrow">Pilot · ${esc(c.pilot.label)}</div>
      <p class="aside-italic" style="font-size:15px;color:var(--ink-3);margin-top:12px">
        n = ${c.pilot.n} simulated students on a fixed ability grid.
        ${c.pilot.discrimination
          ? `Discrimination ${c.pilot.discrimination.min}–${c.pilot.discrimination.max},
             difficulty ${c.pilot.difficulty.min}–${c.pilot.difficulty.max}.`
          : 'No items have been screened yet.'}</p>
      <div style="margin-top:auto;font-family:var(--mono);font-size:9.5px;letter-spacing:.14em;color:var(--ink-5);line-height:1.9;padding-top:24px">
        <div>RECOMPUTED FROM THE GRAPH</div><div>NOT REPLAYED FROM A LOG</div>
      </div>
    </aside>`;
}

/* ═══ router ═══════════════════════════════════════════════════════ */

const ROUTES = {
  '/':            { view: 'view-home',        render: null },
  '/graph':       { view: 'view-graph',       render: renderGraph },
  '/quiz':        { view: 'view-quiz',        render: renderQuiz },
  '/certificate': { view: 'view-certificate', render: renderCertificate },
};

let rendered = new Set();

async function route() {
  const path = (location.hash.replace(/^#/, '') || '/');
  const match = ROUTES[path] || ROUTES['/'];

  document.querySelectorAll('.view').forEach((v) => { v.hidden = v.id !== match.view; });
  document.querySelectorAll('[data-route-link]').forEach((a) =>
    a.toggleAttribute('aria-current', a.dataset.routeLink === path));

  if (match.render && !rendered.has(path)) { rendered.add(path); await match.render(); }
  window.scrollTo({ top: 0, behavior: 'instant' });
}

async function showBuildChip() {
  const s = await API.summary();
  const chip = $('#build-chip');
  if (!chip) return;
  chip.hidden = false;
  chip.textContent = s.available
    ? `${s.concepts} concepts · ${s.prerequisite_edges} edges · ${s.coverage_pct}% covered`
    : 'no chapter compiled';
}

drawPressLine();
watchReveals();
wireClaims();
showBuildChip();
window.addEventListener('hashchange', route);
route();
