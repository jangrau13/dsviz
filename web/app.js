/*
 * dsviz — the course language, in the browser.
 *
 * Everything runs client-side: the Lark parser, the type checker, the
 * simulator and the metrics all execute in Pyodide. There is no backend, so
 * the same Python that renders the lecture videos is what checks a student's
 * program.
 */

const PYODIDE_VERSION = "0.29.4";
const MONACO_VERSION = "0.55.0";
const SVGNS = "http://www.w3.org/2000/svg";

let pyodide = null;
let editor = null;
let monacoRef = null;
let frame = null;
let playing = false;
let clock = 0;
let lastTick = 0;
let pasteAttempts = [];        // recorded, then saved with the submission

/*
 * Element by id.
 *
 * Wiring up the UI is one long run of `$("x").addEventListener(...)`, so a
 * single renamed id used to throw and leave every later control dead — the
 * page looked fine and nothing responded. Missing ids now report themselves
 * and hand back an inert stand-in, so one stale name costs one button.
 */
/*
 * Light and dark.
 *
 * The stylesheet keys off data-theme on <html>; Monaco carries its own theme
 * registry and has to be told separately. The choice is remembered, and with
 * nothing remembered the operating system decides.
 *
 * applyTheme runs once at startup — before Monaco exists — and again as soon
 * as the editor is created, which is why the editor half is optional rather
 * than assumed.
 */
function currentTheme() {
  const saved = localStorage.getItem("dsviz.theme");
  if (saved === "light" || saved === "dark") return saved;
  return window.matchMedia?.("(prefers-color-scheme: light)").matches
    ? "light" : "dark";
}

function applyTheme(mode) {
  const theme = mode === "light" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", theme);
  localStorage.setItem("dsviz.theme", theme);
  const button = document.getElementById("theme");
  if (button) {
    button.textContent = theme === "light" ? "dark mode" : "light mode";
    button.title = `switch to ${theme === "light" ? "dark" : "light"} mode`;
  }
  // Monaco is created later than the first call; theme it when it exists.
  if (typeof monaco !== "undefined" && typeof editor !== "undefined" && editor) {
    monaco.editor.setTheme(theme === "light" ? "dsviz-light" : "dsviz-dark");
  }
}

const $ = (id) => {
  const el = document.getElementById(id);
  if (!el) {
    console.error(`dsviz: no element #${id} — that control will not work`);
    return document.createElement("span");
  }
  return el;
};
const stage = () => $("stage");

function setStatus(text, state = "") {
  $("statusText").textContent = text;
  $("status").className = "status " + state;
}

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const el = document.createElement("script");
    el.src = src;
    el.onload = resolve;
    el.onerror = () => reject(new Error("could not load " + src));
    document.head.appendChild(el);
  });
}

// --- boot ---------------------------------------------------------------

async function boot() {
  setStatus("loading editor…", "busy");
  await bootMonaco();

  setStatus("loading Python…", "busy");
  const base = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;
  await loadScript(base + "pyodide.js");
  pyodide = await loadPyodide({ indexURL: base });

  setStatus("installing packages…", "busy");
  await pyodide.loadPackage(["micropip", "networkx"]);
  const micropip = pyodide.pyimport("micropip");
  await micropip.install(["simpy", "lark"]);

  setStatus("loading dsviz…", "busy");
  // `render_manim` is deliberately excluded: it imports manim, which is not
  // available in the browser. The page draws from `shapes` instead.
  const modules = ["__init__", "values", "core", "patterns", "types", "expr",
                   "grammar", "syntax", "runtime", "project", "notation",
                   "notation_mr", "notation_spark", "metrics", "contest",
                   "shapes", "assignment", "langserver"];
  pyodide.FS.mkdirTree("/home/pyodide/dsviz");
  for (const name of modules) {
    const src = await fetch(`dsviz/${name}.py`).then((r) => {
      if (!r.ok) throw new Error(`missing dsviz/${name}.py`);
      return r.text();
    });
    pyodide.FS.writeFile(`/home/pyodide/dsviz/${name}.py`, src);
  }
  await pyodide.runPythonAsync(
    `import sys; sys.path.insert(0, "/home/pyodide")\n` +
    `from dsviz.langserver import analyse, analyse_project, completions, hover, reference`);

  await pyodide.runPythonAsync(
    "from dsviz.assignment import catalogue, judge_assignment, ASSIGNMENTS");

  // Each task's code is a .ds file, not a Python string, so it has to be
  // fetched like the modules were. `assignment.py` looks for them beside the
  // package, which is /home/pyodide/tasks once the modules are in place.
  const taskNames = (await pyodide.runPythonAsync(
    "','.join(ASSIGNMENTS)")).split(",").filter(Boolean);
  pyodide.FS.mkdirTree("/home/pyodide/tasks");
  for (const task of taskNames) {
    const src = await fetch(`tasks/${task}.ds`).then((r) => {
      if (!r.ok) throw new Error(`missing tasks/${task}.ds`);
      return r.text();
    });
    pyodide.FS.writeFile(`/home/pyodide/tasks/${task}.ds`, src);
  }

  loadCatalogue();
  buildDocs();
  // Before the first task opens, so it opens on the student's own work rather
  // than resetting them to the starter every reload.
  await loadWorkspace();
  setStatus("ready", "ok");
  chooseItem($("examples").value);
}

// --- Monaco -------------------------------------------------------------

async function bootMonaco() {
  const base = `https://cdn.jsdelivr.net/npm/monaco-editor@${MONACO_VERSION}/min`;
  await loadScript(`${base}/vs/loader.js`);
  await new Promise((resolve) => {
    require.config({ paths: { vs: `${base}/vs` } });
    require(["vs/editor/editor.main"], resolve);
  });
  monacoRef = window.monaco;
  registerLanguage(monacoRef);

  editor = monacoRef.editor.create($("editor"), {
    value: "",
    language: LANG_ID,
    theme: currentTheme() === "light" ? "dsviz-light" : "dsviz-dark",
    fontSize: 13,
    fontFamily: "ui-monospace, Menlo, Consolas, monospace",
    minimap: { enabled: false },
    scrollBeyondLastLine: false,
    automaticLayout: true,
    renderWhitespace: "selection",
    tabSize: 4,
    insertSpaces: true,
    contextmenu: false,           // no right-click paste
    quickSuggestions: true,
    suggestOnTriggerCharacters: true,
  });

  blockClipboard(editor);
  // Re-apply once Monaco exists: the first call at startup runs before the
  // editor is created and so cannot theme it.
  applyTheme(currentTheme());
  editor.onDidChangeModelContent(scheduleRun);
}

/*
 * The clipboard is closed in both directions, and every attempt is recorded.
 *
 * Paste stops code arriving from elsewhere; copy and cut stop it leaving — a
 * student cannot lift the starter, or their neighbour's screen, into another
 * window. Both are speed bumps rather than barriers: a screenshot, DevTools,
 * or simply retyping all still work. The point is to make transcription the
 * path of least resistance, and to leave a record for the viva.
 */
function blockClipboard(ed) {
  const node = ed.getDomNode();

  const record = (how, text) => {
    pasteAttempts.push({ at: new Date().toISOString(), how,
                         chars: (text || "").length });
    const verb = how.startsWith("copy") || how.startsWith("cut")
      ? "copying" : "pasting";
    toast(`${verb} is off, so type it out (${pasteAttempts.length} attempt${
      pasteAttempts.length === 1 ? "" : "s"} recorded)`);
    updatePasteBadge();
  };

  const stop = (kind) => (e) => {
    e.preventDefault();
    e.stopPropagation();
    const text = kind === "paste"
      ? ((e.clipboardData || {}).getData?.("text") || "")
      : String(ed.getModel().getValueInRange(ed.getSelection()) || "");
    // Cut would otherwise still delete the selection.
    if (kind === "cut") ed.trigger("clipboard", "undo", null);
    record(kind, text);
  };

  for (const kind of ["paste", "copy", "cut"]) {
    node.addEventListener(kind, stop(kind), true);
  }
  node.addEventListener("drop", (e) => {
    e.preventDefault(); e.stopPropagation(); record("drop", "");
  }, true);
  node.addEventListener("dragstart", (e) => {
    e.preventDefault(); record("drag-out", "");
  }, true);

  // Monaco owns its keybindings, so the shortcuts have to be caught there too.
  const K = monacoRef.KeyMod, C = monacoRef.KeyCode;
  const key = (code, how) => ed.addCommand(K.CtrlCmd | code, () => record(how, ""));
  key(C.KeyV, "paste-key");
  key(C.KeyC, "copy-key");
  key(C.KeyX, "cut-key");
  ed.addCommand(K.CtrlCmd | K.Shift | C.KeyV, () => record("paste-key", ""));
  // Monaco treats ctrl/cmd + K as the start of a chord, so it needs telling.
  ed.addCommand(K.CtrlCmd | C.KeyK, () => {
    if ($("palette").hidden) openPalette(); else closePalette();
  });
}

function updatePasteBadge() {
  const el = $("pasteBadge");
  if (!pasteAttempts.length) { el.hidden = true; return; }
  el.hidden = false;
  el.textContent = `${pasteAttempts.length} clipboard attempt${
    pasteAttempts.length === 1 ? "" : "s"}`;
}

let toastTimer = null;
function toast(text) {
  const el = $("toast");
  el.textContent = text;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), 2600);
}

// --- analysis -----------------------------------------------------------

let runTimer = null;
/* --- files ---------------------------------------------------------------
 *
 * A task is a set of files, not a single buffer. Each gets its own Monaco
 * model so undo history, cursor and error markers survive switching tabs, and
 * all of them are checked together as one program — a helper in one file is in
 * scope in another that says `use` it.
 */
const files = new Map();        // name (no extension) -> monaco model
let activeFile = "";

const fileLabel = (name) => `${name}.ds`;

function openFiles(sources, first) {
  for (const model of files.values()) model.dispose();
  files.clear();
  for (const [name, text] of Object.entries(sources)) {
    // The .ds uri is what makes Monaco apply our language to the model.
    files.set(name, monacoRef.editor.createModel(
      text, LANG_ID, monacoRef.Uri.parse(`inmemory:///${fileLabel(name)}`)));
  }
  showFile(first ?? Object.keys(sources)[0] ?? "");
  drawTabs();
}

function showFile(name) {
  const model = files.get(name);
  if (!model) return;
  activeFile = name;
  editor.setModel(model);
  drawTabs();
}

function drawTabs() {
  const bar = $("files");
  // Always shown, even for one file: a student editing `t1-wordcount.ds` should
  // be able to see that is what they are editing. With one file there is
  // nothing to navigate, but there is still something to know.
  bar.hidden = files.size === 0;
  bar.innerHTML = "";
  for (const name of files.keys()) {
    const tab = document.createElement("button");
    tab.className = "file-tab" + (name === activeFile ? " active" : "");
    tab.textContent = fileLabel(name);
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-selected", String(name === activeFile));
    tab.addEventListener("click", () => showFile(name));
    bar.appendChild(tab);
  }
}

/** Every file's text, as the analyser wants it. */
function sources() {
  const out = {};
  for (const [name, model] of files) out[name] = model.getValue();
  return out;
}

/*
 * Where the program starts.
 *
 * The task's own file is the entry — that is the one holding the functions the
 * runtime calls. Helper files reach it by being named in a `use`.
 */
function entryFile() {
  if (currentAssignment && files.has(currentAssignment.name)) {
    return currentAssignment.name;
  }
  return files.has("main") ? "main" : (files.keys().next().value ?? "main");
}

/** A dot on any tab whose file has an error, so nothing hides behind a tab. */
function markTabsWithErrors(diagnostics) {
  const bad = new Set(diagnostics.filter((d) => d.severity === "error")
                                 .map((d) => d.file).filter(Boolean));
  for (const tab of $("files").children) {
    const name = tab.textContent.replace(/\.ds$/, "");
    tab.classList.toggle("has-error", bad.has(name));
  }
}

function scheduleRun() {
  clearTimeout(runTimer);
  runTimer = setTimeout(run, 300);
  scheduleSave();
}

/* --- the workspace -------------------------------------------------------
 *
 * Where a student's files actually live.
 *
 * They used to be loose `.ds` files in the checkout, which had two problems.
 * Work was lost on reload unless save was pressed, and `solutions/` could be
 * filled by copying one of those files into it — a submission that never went
 * near the editor. Both go away if the files are not loose: the server keeps
 * them in `.dsviz/workspace.json` and hands them back when the page opens.
 *
 * The store is not required. Opened from a plain static server there is no
 * `/api/workspace`, and the page falls back to the browser's own storage, so
 * the editor still works and still remembers — it just cannot hand in, which
 * is the one thing that has to go through the server anyway.
 */
const WORKSPACE = "/api/workspace";
let workspace = {};             // file name (with extension) -> text
let workspaceServed = false;    // is the store the server's, or the browser's?
let saveTimer = null;

const LOCAL_KEY = "dsviz.workspace";
const fileKey = (name) => `${name}.ds`;

async function loadWorkspace() {
  try {
    const res = await fetch(WORKSPACE);
    if (res.ok) {
      workspace = (await res.json()).files ?? {};
      workspaceServed = true;
      return;
    }
  } catch {
    // No server-side store; the browser keeps the work instead.
  }
  try {
    workspace = JSON.parse(localStorage.getItem(LOCAL_KEY) ?? "{}");
  } catch {
    workspace = {};
  }
}

/** The text this workspace holds for a file, or null if it holds none. */
function remembered(name) {
  const text = workspace[fileKey(name)];
  return typeof text === "string" ? text : null;
}

function scheduleSave() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveWorkspace, 600);
}

async function saveWorkspace() {
  if (!files.size) return;
  const changed = [];
  for (const [name, text] of Object.entries(sources())) {
    if (workspace[fileKey(name)] === text) continue;
    workspace[fileKey(name)] = text;
    changed.push(name);
  }
  if (!changed.length) return;
  if (!workspaceServed) {
    localStorage.setItem(LOCAL_KEY, JSON.stringify(workspace));
    return;
  }
  for (const name of changed) {
    try {
      await fetch(`${WORKSPACE}/${encodeURIComponent(fileKey(name))}`, {
        method: "PUT",
        headers: { "content-type": "text/plain" },
        body: workspace[fileKey(name)],
      });
    } catch {
      // A save that cannot reach the server is not worth interrupting the
      // student over; the next keystroke schedules another one.
    }
  }
}

async function run() {
  if (!pyodide || !editor || !files.size) return;
  const task = currentAssignment ? currentAssignment.name : "";
  let result;
  try {
    // Every file is checked as one program, so a helper defined in one is in
    // scope in another that uses it. Diagnostics come back tagged with the
    // file they belong to.
    const fn = pyodide.globals.get("analyse_project");
    result = JSON.parse(fn(pyodide.toPy(sources()), entryFile(), task));
    fn.destroy();
  } catch (err) {
    setStatus("error", "bad");
    console.error(err);
    return;
  }

  // Mark each file's own model, so an error in a file you are not looking at
  // still shows on its tab rather than vanishing.
  for (const [name, model] of files) {
    setDiagnostics(monacoRef, model,
                   result.diagnostics.filter((d) => (d.file ?? name) === name));
  }
  markTabsWithErrors(result.diagnostics);
  showDiagnostics(result.diagnostics);
  $("dialect").textContent = result.dialect;
  setJourney(journeyFrom(result));
  showNextStep(nextStep(result));

  if (!result.frame) {
    stage().innerHTML = "";
    $("metrics").innerHTML = "";
    $("verdict").innerHTML = "";
    return;
  }
  frame = result.frame;
  showMetrics(result.metrics, result.verdict, result.outputs);
  clock = 0;
  draw();
  play(true);
}

/*
 * Where the student is, and what to do next.
 *
 * The four steps in the header are the whole loop: write, run, meet the
 * budgets, hand in. Which one is current is read off the last analysis rather
 * than tracked, so it stays right when the student jumps around.
 */
function journeyFrom(result) {
  const errors = (result.diagnostics || []).filter((d) => d.severity === "error");
  if (errors.length || !result.frame) return "write";
  const budgets = result.metrics || [];
  const missed = budgets.filter((m) => m.limit != null && m.value > m.limit);
  if (missed.length) return "improve";
  return result.verdict === "AC" ? "handin" : "run";
}

function setJourney(step) {
  const order = ["write", "run", "improve", "handin"];
  const at = order.indexOf(step);
  for (const li of $("journey").children) {
    const i = order.indexOf(li.dataset.step);
    li.classList.toggle("now", i === at);
    li.classList.toggle("done", i >= 0 && i < at);
  }
}

/** One line saying what to do, so no one has to guess which panel to read. */
function nextStep(result) {
  const errors = (result.diagnostics || []).filter((d) => d.severity === "error");
  if (errors.length) {
    return { text: "fix this first: " + errors[0].message, tone: "bad" };
  }
  if (!result.frame) {
    return { text: "write your functions, wire them into a job, and run it",
             tone: "" };
  }
  const missed = (result.metrics || [])
    .filter((m) => m.limit != null && m.value > m.limit);
  if (missed.length) {
    const m = missed[0];
    return { text: `${m.label ?? m.key} is ${m.value}${m.unit ?? ""}, over the `
                   + `budget of ${m.limit}${m.unit ?? ""}. Hover it to see why.`,
             tone: "warn" };
  }
  if (result.verdict === "AC") {
    return { text: "this passes on the input you can see. Hand in to run it "
                   + "on input you cannot.", tone: "ok" };
  }
  return { text: "it runs; check the results panel against what you expected",
           tone: "" };
}

function showNextStep(step) {
  const el = $("nextStep");
  if (!step || !step.text) { el.hidden = true; return; }
  el.hidden = false;
  el.className = "next-step " + (step.tone || "");
  el.textContent = step.text;
}

function showDiagnostics(diags) {
  const box = $("diagnostics");
  if (!diags.length) {
    box.innerHTML = `<div class="ok">no problems</div>`;
    return;
  }
  box.innerHTML = diags.map((d) => `
    <div class="diag ${d.severity}" data-line="${d.line}">
      <span class="where">${d.line}:${d.col ?? 1}</span>
      <span class="msg">${escapeHtml(d.message)}</span>
      ${d.hint ? `<div class="hint">${escapeHtml(d.hint)}</div>` : ""}
    </div>`).join("");
  box.querySelectorAll(".diag").forEach((el) =>
    el.addEventListener("click", () => {
      const line = Number(el.dataset.line);
      editor.revealLineInCenter(line);
      editor.setPosition({ lineNumber: line, column: 1 });
      editor.focus();
    }));
}

function showMetrics(metrics, verdict, outputs) {
  const cards = (metrics || []).map((m, i) => `
    <div class="metric" data-metric="${i}" tabindex="0">
      <div class="mname">${m.name}</div>
      <div class="mvalue">${m.value}<span class="unit">${m.unit}</span></div>
    </div>`).join("");
  metricInfo = metrics || [];

  let v = "";
  if (verdict) {
    const bad = verdict.cases.filter((c) => c.verdict !== "AC");
    v = `<div class="verdict ${verdict.verdict}">
      ${escapeHtml(verdict.label || verdict.verdict)} ·
      ${verdict.score}/${verdict.max_score} checks
      ${bad.map((c) => `<div class="case">${escapeHtml(c.name)}${
        c.message ? " — " + escapeHtml(c.message) : ""}</div>`).join("")}
    </div>`;
  }

  let out = "";
  if (outputs && Object.keys(outputs).length) {
    const items = Object.entries(outputs).slice(0, 12).map(([k, val]) => {
      const shown = val && val.sample
        ? `${val.count} records: ${JSON.stringify(val.sample).slice(0, 46)}…`
        : JSON.stringify(val);
      return `<span class="out"><b>${escapeHtml(k)}</b> ${escapeHtml(shown)}</span>`;
    }).join("");
    out = `<div class="outputs">${items}</div>`;
  }
  // Verdict and outputs stay visible; only the tiles scroll beneath them.
  $("verdict").innerHTML = v + out;
  $("metrics").innerHTML = `<div class="metric-row">${cards}</div>`;
  wireMetricHovers();
}

let metricInfo = [];

/* Every number is explained on hover: what it measures, why it matters in a
 * distributed system, and what a student can do about it. */
function wireMetricHovers() {
  const pop = $("metricPop");
  const show = (el) => {
    const m = metricInfo[Number(el.dataset.metric)];
    if (!m || !m.explain) return;
    pop.innerHTML = `
      <div class="pop-title">${escapeHtml(m.name)}
        <span class="pop-dir">${escapeHtml(m.explain.better)} is better</span></div>
      <div class="pop-what">${escapeHtml(m.explain.what)}</div>
      ${m.explain.why ? `<div class="pop-why">${escapeHtml(m.explain.why)}</div>` : ""}
      ${m.explain.how ? `<div class="pop-how">${escapeHtml(m.explain.how)}</div>` : ""}`;
    pop.hidden = false;
    const r = el.getBoundingClientRect();
    // Prefer above the tile; flip below when there is no room.
    pop.style.left = Math.max(8, Math.min(
      r.left, window.innerWidth - pop.offsetWidth - 8)) + "px";
    const above = r.top - pop.offsetHeight - 8;
    pop.style.top = (above > 8 ? above : r.bottom + 8) + "px";
  };
  const hide = () => (pop.hidden = true);

  $("metrics").querySelectorAll(".metric").forEach((el) => {
    el.addEventListener("mouseenter", () => show(el));
    el.addEventListener("focus", () => show(el));
    el.addEventListener("mouseleave", hide);
    el.addEventListener("blur", hide);
  });
}

// --- drawing ------------------------------------------------------------
/* Static shapes are drawn once per frame; chips are positioned by
 * interpolating along their flight path, which is what makes it live. */

function draw() {
  if (!frame) return;
  const svg = stage();
  svg.innerHTML = "";
  landed.clear();
  fitViewBox();

  const defs = document.createElementNS(SVGNS, "defs");
  defs.innerHTML = `<marker id="ah" markerWidth="6" markerHeight="6" refX="5"
    refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 z" fill="context-stroke"/></marker>`;
  svg.appendChild(defs);

  for (const s of frame.shapes)
    if (s.kind === "box") drawBox(s);
    else if (s.kind === "lane") drawLane(s);
    else if (s.kind === "label") drawLabel(s);
  for (const s of frame.shapes)
    if (s.kind === "arrow") drawArrow(s);
    else if (s.kind === "marker") drawMarker(s);
    else if (s.kind === "chip") drawChip(s);

  $("clock").textContent = clock.toFixed(2) + "s";
  $("scrub").value = frame.duration ? (clock / frame.duration) * 1000 : 0;
}

function fitViewBox() {
  /*
   * What the frame has to contain.
   *
   * A chip in flight travels between two nodes, so its path sweeps the space
   * between them and including it would stretch the view and leave the
   * diagram off-centre in dead space. A chip at rest is different: it sits
   * inside a machine, and leaving it out clipped whatever a machine was
   * holding — the taller the stack, the more was cut off.
   *
   * The two are told apart by whether the chip has somewhere to go: a flight
   * carries a destination (x2, y2), a resting chip does not.
   */
  const xs = [], ys = [];
  for (const s of frame.shapes) {
    const flying = s.kind === "chip" && s.x2 !== undefined && s.x2 !== null;
    if (flying) continue;
    const w = s.w || 0.6, h = s.h || 0.6;
    xs.push(s.x - w / 2, s.x + w / 2);
    ys.push(s.y - h / 2, s.y + h / 2);
    if (s.kind === "lane" || s.kind === "arrow") { xs.push(s.x2); ys.push(s.y2); }
  }
  if (!xs.length) return;

  const padX = 0.9, padY = 0.7;
  let minX = Math.min(...xs) - padX, maxX = Math.max(...xs) + padX;
  let minY = Math.min(...ys) - padY, maxY = Math.max(...ys) + padY;

  /*
   * A floor on how far in it will zoom.
   *
   * The viewBox scales to whatever it holds, so a one-machine program would
   * otherwise fill the panel with a single enormous box. Below this the frame
   * grows around its own centre instead. It is deliberately small: a floor
   * larger than the diagram is what left three machines adrift in a field of
   * empty panel, which is worse than a diagram that is merely big.
   */
  const grow = (lo, hi, min) => {
    const short = min - (hi - lo);
    if (short <= 0) return [lo, hi];
    return [lo - short / 2, hi + short / 2];
  };
  [minX, maxX] = grow(minX, maxX, 5);
  [minY, maxY] = grow(minY, maxY, 3.4);

  /*
   * Then take the panel's own shape.
   *
   * preserveAspectRatio letterboxes whatever is left over, so a 16:9 frame in
   * a squarer panel paints two dead bands and shrinks the diagram to pay for
   * them. Widening the frame to the panel's ratio spends that space on the
   * margin instead: the content keeps its proportions, and the scale is set by
   * the tighter of the two axes rather than by the mismatch.
   */
  const box = stage().getBoundingClientRect();
  const panel = box.width > 0 && box.height > 0 ? box.width / box.height : 0;
  if (panel > 0) {
    const w = maxX - minX, h = maxY - minY;
    if (w / h < panel) [minX, maxX] = grow(minX, maxX, h * panel);
    else [minY, maxY] = grow(minY, maxY, w / panel);
  }

  stage().setAttribute("viewBox",
    `${minX} ${-maxY} ${maxX - minX} ${maxY - minY}`);
}

const sy = (y) => -y;

function el(name, attrs, parent = stage()) {
  const n = document.createElementNS(SVGNS, name);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  parent.appendChild(n);
  return n;
}

function drawBox(s) {
  const g = el("g", { class: "node" });
  el("rect", { x: s.x - s.w / 2, y: sy(s.y) - s.h / 2, width: s.w, height: s.h,
               rx: 0.1, fill: s.color, "fill-opacity": 0.10,
               stroke: s.color, "stroke-width": 0.035 }, g);
  const t = el("text", { x: s.x, y: sy(s.y) - s.h / 2 + 0.32,
                         "text-anchor": "middle", class: "node-label",
                         fill: s.color }, g);
  t.textContent = s.text;
  if (s.meta && s.meta.role) {
    const r = el("text", { x: s.x, y: sy(s.y) - s.h / 2 + 0.58,
                           "text-anchor": "middle", class: "node-role" }, g);
    r.textContent = s.meta.role +
      (s.meta.speed && s.meta.speed < 1 ? `  ·  ${s.meta.speed}× slow` : "");
  }
}

function drawLane(s) {
  el("line", { x1: s.x, y1: sy(s.y), x2: s.x2, y2: sy(s.y2),
               stroke: "#3a3f45", "stroke-width": 0.025 });
  const t = el("text", { x: s.x - 0.2, y: sy(s.y) + 0.08, "text-anchor": "end",
                         class: "lane-label", fill: s.color });
  t.textContent = s.text;
}

function drawLabel(s) {
  const t = el("text", { x: s.x, y: sy(s.y), "text-anchor": "middle", class: "note" });
  t.textContent = s.text;
}

function drawMarker(s) {
  if (clock < s.t_in) return;
  const t = el("text", { x: s.x, y: sy(s.y), "text-anchor": "middle",
                         class: "marker", fill: s.color });
  t.textContent = s.text;
}

function drawArrow(s) {
  if (clock < s.t_in) return;
  const active = s.t_out == null || clock <= s.t_out;
  const g = el("g", { class: "rpc" + (active ? " active" : "") });
  el("line", { x1: s.x, y1: sy(s.y), x2: s.x2, y2: sy(s.y2), stroke: s.color,
               "stroke-width": active ? 0.055 : 0.028,
               "stroke-opacity": active ? 1 : 0.35,
               "marker-end": "url(#ah)" }, g);
  if (s.text) {
    const t = el("text", { x: (s.x + s.x2) / 2, y: (sy(s.y) + sy(s.y2)) / 2 - 0.12,
                           "text-anchor": "middle", class: "rpc-label",
                           fill: s.color, "fill-opacity": active ? 1 : 0.5 }, g);
    t.textContent = s.text;
  }
}

/* The payload in flight: interpolated between source and target so the
 * student sees what is being transported, and when it lands. */
const landed = new Map();      // node key -> how many chips rest there

function drawChip(s) {
  const depart = s.t_in, arrive = s.t_out == null ? s.t_in : s.t_out;
  if (clock < depart) return;

  let x = s.x, y = s.y, moving = false;
  if (arrive > depart) {
    const p = Math.min(1, (clock - depart) / (arrive - depart));
    x = s.x + (s.x2 - s.x) * p;
    y = s.y + (s.y2 - s.y) * p;
    moving = p < 1;
  } else if (s.x2 || s.y2) { x = s.x2; y = s.y2; }

  // Once it arrives, stack it inside the destination so the node visibly fills
  // up rather than looking empty. The shape carries where its box's interior
  // starts and how much room is in it — stacking blind from the centre is what
  // used to send a busy reducer's arrivals out through the bottom edge.
  if (!moving) {
    const key = `${x.toFixed(2)},${y.toFixed(2)}`;
    const n = landed.get(key) || 0;
    landed.set(key, n + 1);
    const top = s.meta?.land_top ?? y;
    const step = s.meta?.land_step ?? 0.34;
    const room = s.meta?.land_room ?? 0;
    // Tighten rather than overflow: the last arrival stays in the box.
    y = room > 0 ? top - Math.min(n * step, room) : top - n * step;
  }

  const w = Math.max(0.45, s.text.length * 0.105 + 0.18);
  const g = el("g", { class: "chip " + (moving ? "moving" : "landed") });
  el("rect", { x: x - w / 2, y: sy(y) - 0.15, width: w, height: 0.3, rx: 0.07,
               fill: s.color, "fill-opacity": moving ? 0.32 : 0.15,
               stroke: s.color, "stroke-width": 0.025 }, g);
  const t = el("text", { x, y: sy(y) + 0.07, "text-anchor": "middle",
                         class: "chip-label", fill: s.color }, g);
  t.textContent = s.text;
}

// --- playback -----------------------------------------------------------

function play(restart = false) {
  if (restart) clock = 0;
  playing = true;
  $("play").textContent = "❚❚";
  lastTick = performance.now();
  requestAnimationFrame(tick);
}

function pause() { playing = false; $("play").textContent = "▶"; }

function tick(now) {
  if (!playing || !frame) return;
  clock += (now - lastTick) / 1000;
  lastTick = now;
  if (clock >= frame.duration + 0.8) { clock = frame.duration + 0.8; pause(); }
  draw();
  if (playing) requestAnimationFrame(tick);
}

// --- video --------------------------------------------------------------
/*
 * The dataflow, as a file the student can keep.
 *
 * render_manim.py already turns a trace into a real video, but Manim wants a
 * machine with ffmpeg on it, and the point of this page is that a student needs
 * to install nothing. So the video is made out of what is already on screen:
 * the animation is replayed while each frame is drawn onto a canvas, and
 * MediaRecorder encodes the canvas as it goes. Nothing leaves the browser.
 *
 * Replay is driven by the wall clock rather than by a frame counter, so a
 * diagram too heavy to rasterise 25 times a second loses frames instead of
 * running in slow motion — the recording lasts as long as the run did.
 */
const VIDEO_FPS = 25;
let recording = false;

/** The first container this browser will actually encode. */
function videoType() {
  if (!window.MediaRecorder) return "";
  const wanted = ["video/mp4;codecs=avc1", "video/mp4",
                  "video/webm;codecs=vp9", "video/webm;codecs=vp8", "video/webm"];
  return wanted.find((t) => MediaRecorder.isTypeSupported?.(t)) ?? "";
}

/*
 * A standalone SVG carries no stylesheet, so text would rasterise as unstyled
 * black at the browser's default size. Rather than keep a second copy of the
 * rules here — which would drift the first time style.css changed — the real
 * ones are read back out of the cascade, with each var() resolved to the
 * colour the current theme gives it.
 */
function svgStyles() {
  const root = getComputedStyle(document.documentElement);
  const value = (name) => root.getPropertyValue(name).trim();
  const KEEP = /^(text|\.node-|\.lane-|\.chip|\.rpc|\.marker|\.note)/;
  let out = `text { font-family: ${getComputedStyle(document.body).fontFamily};` +
            ` fill: ${value("--text") || "#e6e8ea"}; }\n`;
  for (const sheet of document.styleSheets) {
    let rules;
    try { rules = sheet.cssRules; } catch { continue; }   // a CDN sheet is opaque
    for (const rule of rules) {
      if (!rule.selectorText || !KEEP.test(rule.selectorText)) continue;
      out += rule.cssText.replace(/var\((--[\w-]+)\)/g,
        (_, name) => value(name) || "currentColor") + "\n";
    }
  }
  return out;
}

/** The stage as it stands, as a self-contained SVG document. */
function stageSvgText(width, height) {
  const clone = stage().cloneNode(true);
  clone.setAttribute("xmlns", SVGNS);
  clone.setAttribute("width", width);
  clone.setAttribute("height", height);
  const css = document.createElementNS(SVGNS, "style");
  css.textContent = svgStyles();
  clone.insertBefore(css, clone.firstChild);
  return new XMLSerializer().serializeToString(clone);
}

function rasterise(text) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("this frame could not be rasterised"));
    img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(text);
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, Math.max(0, ms)));

async function recordVideo() {
  if (!frame) { toast("run something first: there is no dataflow to record"); return; }
  if (recording) return;
  const type = videoType();
  if (!type) { toast("this browser cannot record video. Try Chrome or Safari"); return; }

  recording = true;
  pause();
  const btn = $("record"), label = btn.textContent, held = clock;
  btn.disabled = true;

  // 720p, in the panel's own proportions, so the recording is framed exactly
  // as the student saw it. Heights are kept even; some encoders reject odd.
  const box = stage().getBoundingClientRect();
  const W = 1280;
  const H = Math.max(2, Math.round(W / (box.width / box.height || 16 / 9) / 2) * 2);
  const canvas = document.createElement("canvas");
  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext("2d");
  const bg = getComputedStyle(document.documentElement)
    .getPropertyValue("--bg").trim() || "#0f1114";

  const rec = new MediaRecorder(canvas.captureStream(VIDEO_FPS),
                                { mimeType: type, videoBitsPerSecond: 6000000 });
  const chunks = [];
  rec.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
  const stopped = new Promise((r) => (rec.onstop = r));

  const total = (frame.duration ?? 0) + 0.8;
  try {
    rec.start();
    const started = performance.now();
    for (;;) {
      const at = (performance.now() - started) / 1000;
      clock = Math.min(at, total);
      draw();
      const img = await rasterise(stageSvgText(W, H));
      ctx.fillStyle = bg;
      ctx.fillRect(0, 0, W, H);
      ctx.drawImage(img, 0, 0, W, H);
      btn.textContent = `${Math.min(99, Math.round((at / total) * 100))}%`;
      if (at >= total) break;
      await sleep(1000 / VIDEO_FPS - (performance.now() - started - at * 1000));
    }
    // One frame's worth of tail, so the encoder keeps the final image.
    await sleep(1000 / VIDEO_FPS);
    rec.stop();
    await stopped;

    const ext = type.startsWith("video/mp4") ? "mp4" : "webm";
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob(chunks, { type }));
    const named = currentAssignment ? currentAssignment.name : "dsviz";
    a.download = `${named}-dataflow.${ext}`;
    a.click();
    URL.revokeObjectURL(a.href);
    toast(`saved ${a.download}`);
  } catch (err) {
    if (rec.state !== "inactive") rec.stop();
    toast(`could not record: ${err.message}`);
  } finally {
    recording = false;
    btn.disabled = false;
    btn.textContent = label;
    clock = held;
    draw();
  }
}

// --- docs ---------------------------------------------------------------

/* Grouped by what a construct is, never by which exercise uses it: the
 * grouping comes from `langserver.GROUPS`, which the docs site reads too, so
 * the panel and the site cannot describe the language differently. */
let docEntries = [];             // everything ctrl/cmd + K searches

function buildDocs() {
  const ref = JSON.parse(pyodide.globals.get("reference")());
  docEntries = [];
  let html = "";
  for (const g of ref.groups) {
    html += `<h3>${escapeHtml(g.title)}</h3>`;
    for (const d of g.items) {
      docEntries.push({ ...d, group: g.title });
      html += `<div class="doc-item">
        <code class="sig">${escapeHtml(d.signature)}</code>
        <div class="sum">${escapeHtml(d.summary)}</div>
        ${d.detail ? `<div class="det">${escapeHtml(d.detail)}</div>` : ""}
        ${d.example ? `<pre class="ex">${escapeHtml(d.example)}</pre>` : ""}
      </div>`;
    }
  }
  html += `<h3>Built-in functions</h3><div class="doc-item">` +
    Object.entries(ref.builtins).map(([name, sig]) => {
      docEntries.push({ name, signature: sig, summary: "",
                        detail: "", example: "", group: "Built-in functions" });
      return `<div class="sum"><code>${escapeHtml(sig)}</code></div>`;
    }).join("") + `</div>`;
  $("docsBody").innerHTML = html;
}

// --- the language search ------------------------------------------------
/* One shortcut, available wherever the cursor is, including inside the
 * editor. Monaco claims ctrl/cmd + K for its own chords, so the key is caught
 * in the capture phase before Monaco sees it, and again as a Monaco command
 * for the case where it gets there first. */
let paletteAt = 0;

function paletteMatches(query) {
  const q = query.trim().toLowerCase();
  if (!q) return docEntries.slice(0, 40);
  const words = q.split(/\s+/);
  const scored = [];
  for (const d of docEntries) {
    const hay = `${d.name} ${d.signature} ${d.summary} ${d.detail} ${d.group}`
      .toLowerCase();
    if (!words.every((w) => hay.includes(w))) continue;
    // A name that starts with what was typed is what the reader meant.
    const name = d.name.toLowerCase();
    scored.push([name === q ? 0 : name.startsWith(q) ? 1
                 : name.includes(q) ? 2 : 3, d]);
  }
  scored.sort((a, b) => a[0] - b[0]);
  return scored.slice(0, 40).map((s) => s[1]);
}

function renderPalette() {
  const hits = paletteMatches($("paletteInput").value);
  if (paletteAt >= hits.length) paletteAt = 0;
  const list = $("paletteList");
  if (!hits.length) {
    list.innerHTML = `<div class="palette-empty">Nothing matches that.</div>`;
    return;
  }
  list.innerHTML = hits.map((d, i) => `
    <div class="palette-item" role="option" data-i="${i}"
         aria-selected="${i === paletteAt}">
      <code class="sig">${escapeHtml(d.signature)}</code>
      <div class="grp">${escapeHtml(d.group)}</div>
      ${d.summary ? `<div class="sum">${escapeHtml(d.summary)}</div>` : ""}
      ${i === paletteAt && d.detail
        ? `<div class="det">${escapeHtml(d.detail)}</div>` : ""}
      ${i === paletteAt && d.example
        ? `<pre class="ex">${escapeHtml(d.example)}</pre>` : ""}
    </div>`).join("");
  list.querySelector('[aria-selected="true"]')
    ?.scrollIntoView({ block: "nearest" });
}

function openPalette() {
  if (!docEntries.length) { toast("the language reference is still loading"); return; }
  paletteAt = 0;
  $("palette").hidden = false;
  $("paletteScrim").hidden = false;
  const box = $("paletteInput");
  box.value = "";
  renderPalette();
  box.focus();
}

function closePalette() {
  $("palette").hidden = true;
  $("paletteScrim").hidden = true;
  editor?.focus();
}

// --- saving -------------------------------------------------------------
/* A submission is the program plus how it was produced. Paste attempts travel
 * with it, so the viva can ask about them. */

/* Hand in: the same code, run against held-out input. The student sees only
 * whether each case passed, never the expected values. */
async function handIn() {
  if (!currentAssignment) {
    toast("pick a task before handing in");
    return;
  }
  const btn = $("handin");
  btn.disabled = true;
  btn.textContent = "running…";
  try {
    const fn = pyodide.globals.get("judge_assignment");
    const res = JSON.parse(fn(currentAssignment.name, editor.getValue(), true));
    fn.destroy();
    showHandIn(res);
  } finally {
    btn.disabled = false;
    btn.textContent = "hand in";
  }
}

// What the hand-in dialog may honestly claim.
//
// The held-out input is stripped from the copy students run, so in the browser
// there is nothing unseen to grade against and `judge_assignment` reports
// `graded_on_holdout: false`. Claiming otherwise here told every student their
// code had been re-run on input they had not seen when it had just been re-run
// on the example in front of them. The real hold-out grading happens in CI, on
// push, and that is what the wording now says.
function handInNote(res, passed) {
  if (res.graded_on_holdout) {
    return passed
      ? "Your code was re-run on input you have not seen, and produced the right answers. That is the point: the logic generalises."
      : "Your code was re-run on input you have not seen. These cases did not pass, and the expected values stay hidden.";
  }
  return passed
    ? "These checks passed on the example you can see. The graded run happens when you push: your code is re-run there on input you have not seen, and only that decides whether the logic generalises."
    : "These checks did not pass, on the example you can see. Fix them before you push. The graded run uses input you have not seen, so anything failing here fails there too.";
}

function showHandIn(res) {
  const passed = res.verdict === "AC";
  const body = `
    <div class="handin-verdict ${res.verdict}">
      ${passed ? "Passed" : escapeHtml(res.label || res.verdict)} ·
      ${res.score}/${res.max_score} checks
    </div>
    <p class="handin-note">${handInNote(res, passed)}</p>
    <ul class="handin-cases">${res.cases.map((c) =>
      `<li class="${c.verdict}"><span>${c.verdict === "AC" ? "pass" : "fail"}</span> ${escapeHtml(c.name)}${
        c.message ? " \u2014 " + escapeHtml(c.message) : ""}</li>`).join("")}</ul>
    ${passed ? `<button id="handinSave" class="primary">hand in to my repo</button>` : ""}`;
  $("handinBody").innerHTML = body;
  $("handinDialog").hidden = false;
  $("menuScrim").hidden = false;

  // Handing in goes through the server, which runs the code again and writes
  // `solutions/<task>.ds` itself. That is deliberate: the file grading reads
  // is written by something that has seen the code run, so a submission
  // cannot be a file somebody copied into the folder.
  const local = $("handinSave");
  if (local) local.addEventListener("click", handInToRepo);
}

/**
 * Ask the server to write this submission into `solutions/`.
 *
 * The page could write the file itself — it did, once — but then the only
 * thing standing between `solutions/` and code nobody ever ran was etiquette.
 * The server runs it, records what it did, and writes both together.
 */
async function handInToRepo() {
  const task = currentAssignment ? currentAssignment.name : "";
  if (!task) { toast("pick a task before handing in"); return; }
  const btn = $("handinSave");
  btn.disabled = true;
  try {
    const res = await fetch(`/api/handin/${encodeURIComponent(task)}`, {
      method: "POST",
      headers: { "content-type": "text/plain" },
      body: sources()[entryFile()] ?? "",
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      toast(body.error ?? "the server refused this hand-in");
      return;
    }
    toast(`handed in to ${body.handed_in}. Commit and push`);
  } catch {
    // Opened from a static server or a file:// URL. Say which thing is
    // missing rather than "failed", because the fix is to start the server.
    toast("no editor server. Start it with .devcontainer/serve.py, then hand in");
  } finally {
    btn.disabled = false;
  }
}

function submission() {
  const { owner, repo } = originRepo();
  return {
    saved_at: new Date().toISOString(),
    task: currentAssignment ? currentAssignment.name : "",
    dialect: $("dialect").textContent,
    repo: owner && repo ? `${owner}/${repo}` : "",
    source: sources()[entryFile()] ?? "",
    files: sources(),
    paste_attempts: pasteAttempts,
  };
}

/*
 * Saving.
 *
 * There is nothing to write to disk any more. Files are the workspace's, the
 * workspace is the server's, and every keystroke already schedules a save into
 * it — so this button exists to answer "is my work safe", not to move bytes
 * that would otherwise be lost. It flushes whatever is still pending and says
 * where the work went.
 *
 * The one thing that does become a file is a hand-in, and the server writes
 * that itself after running the code. See handInToRepo.
 */
async function saveNow() {
  await saveWorkspace();
  toast(workspaceServed
    ? "saved to your workspace. Hand in when a task passes"
    : "saved in this browser. Start the editor server to hand in");
}

/** Download each file as itself — for taking work somewhere else. */
function downloadSources(texts) {
  for (const [name, text] of Object.entries(texts)) {
    const blob = new Blob([text], { type: "text/plain" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${name}.ds`;
    a.click();
    URL.revokeObjectURL(a.href);
  }
}

function download() {
  const data = submission();
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `submission-${data.dialect}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
}

/*
 * Which repository this checkout belongs to.
 *
 * The page is served from the student's own checkout — in a codespace, or by
 * `python -m http.server` locally — so the URL cannot say which repo it is.
 * The setup step writes the slug into a meta tag, read from `git remote`, and
 * that is what the commit button uses. A copy of this page opened outside a
 * checkout has no tag and cannot commit, which is the intended check.
 */
function originRepo() {
  const tag = document.querySelector('meta[name="dsviz-repo"]');
  const slug = (tag?.content ?? "").trim();
  const m = slug.match(/^([\w.-]+)\/([\w.-]+)$/);
  if (m) return { owner: m[1], repo: m[2], hosted: true };

  // A Pages deployment still names the repo in its URL.
  const host = location.hostname;              // owner.github.io
  const p = host.match(/^([\w-]+)\.github\.io$/i);
  if (p) {
    const seg = location.pathname.split("/").filter(Boolean);
    return { owner: p[1], repo: seg[0] || `${p[1]}.github.io`, hosted: true };
  }
  return { owner: "", repo: "", hosted: false };   // not in a checkout
}

/*
 * Commit the submission to the repository this page came from.
 *
 * A static page has no server and must never hold a student's token, so the
 * commit goes through GitHub's own editor: we prefill the file and the message,
 * the student presses "Commit changes", and GitHub does the authenticating.
 * That also means the commit is genuinely theirs, which is what the viva gate
 * later checks against.
 */
/*
 * Committing.
 *
 * This used to open GitHub's web editor with the file name prefilled and the
 * code on the clipboard, for anyone without a clone. It cannot stay: a file
 * created that way carries no run record, so grading refuses it — the button
 * would be a route to a red build that looked like a hand-in.
 *
 * The hand-in itself is the same act as before; what changed is that only the
 * server performs it. So this does that, and then says the one thing left to
 * do, which git does better than a browser tab.
 */
async function commitToRepo() {
  if (!currentAssignment) { toast("pick a task before committing"); return; }
  await handInToRepo();
  toast("now commit and push from your checkout: git add solutions && git commit");
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

let assignments = [];
let currentAssignment = null;

/** Assignments first, then the free-play examples. */
function loadCatalogue() {
  const fn = pyodide.globals.get("catalogue");
  assignments = JSON.parse(fn());
  fn.destroy();

  // One package ships every task the course has; an exercise offers a few of
  // them. The server injects the list when it serves this page, so the Spark
  // exercise does not present a word-count MapReduce. No tag means no
  // exercise around us — show everything, which is what this repo wants.
  const only = document.querySelector('meta[name="dsviz-tasks"]')?.content ?? "";
  if (only.trim()) {
    const wanted = only.split(",").map((s) => s.trim()).filter(Boolean);
    assignments = wanted
      .map((n) => assignments.find((a) => a.name === n))
      .filter(Boolean);
  }

  // A task keeps one id across the course and is numbered by the exercise
  // that uses it, so the heading comes from the exercise when it says one.
  const renamed = document.querySelector('meta[name="dsviz-titles"]')?.content;
  if (renamed) {
    try {
      const byName = JSON.parse(renamed);
      for (const a of assignments) if (byName[a.name]) a.title = byName[a.name];
    } catch { /* a malformed override leaves the package's own titles */ }
  }

  const sel = $("examples");
  sel.innerHTML = "";
  const asg = document.createElement("optgroup");
  asg.label = "Assignments";
  for (const a of assignments) {
    const o = document.createElement("option");
    o.value = "assignment:" + a.name;
    o.textContent = a.title;
    asg.appendChild(o);
  }
  sel.appendChild(asg);

  // No MapReduce demos here: they would give away the graded tasks.
  const groups = {
    Spark: ["spark", "lineage"],
    "gRPC": ["grpc", "failure"],
    "Vector clocks": ["clocks"],
  };
  for (const [label, names] of Object.entries(groups)) {
    const g = document.createElement("optgroup");
    g.label = label + " (free play)";
    for (const n of names) {
      const o = document.createElement("option");
      o.value = "example:" + n;
      o.textContent = n;
      g.appendChild(o);
    }
    sel.appendChild(g);
  }
}

function chooseItem(value) {
  const [kind, name] = value.split(":");
  if (kind === "assignment") {
    currentAssignment = assignments.find((a) => a.name === name) || null;
    showBrief(currentAssignment);
    showSetup(currentAssignment);
    openFiles(kept(currentAssignment.files ?? {[name]: currentAssignment.starter}),
              name);
  } else {
    currentAssignment = null;
    showBrief(null);
    showSetup(null);
    openFiles(kept({ [name]: EXAMPLES[name] }), name);
  }
  run();
}

/**
 * The task's files, with the student's own version of each preferred.
 *
 * Switching tasks and coming back used to hand back the starter, quietly
 * discarding an afternoon's work. What the workspace holds is what the student
 * last wrote, so that is what opens; the starter is only the first draft.
 */
function kept(given) {
  const out = {};
  for (const [name, text] of Object.entries(given)) out[name] = remembered(name) ?? text;
  return out;
}

/* The assignment's brief and its criteria. Nothing about the judging is
 * concealed except the tests marked hidden, which are shown as such. */
function showBrief(a) {
  const el = $("brief");
  if (!a) { el.hidden = true; return; }
  el.hidden = false;
  el.innerHTML = `<div class="brief-title">${escapeHtml(a.title)}</div>
    <div class="brief-text">${escapeHtml(a.brief)}</div>
    ${a.goals && a.goals.length ? `<details class="goals"><summary>What this task is for</summary><ul>${
      a.goals.map((g) => `<li><span class="lvl">${escapeHtml(g.level)}</span>${
        escapeHtml(g.title)}</li>`).join("")}</ul></details>` : ""}
    ${a.steps && a.steps.length ? `<ol class="steps">${
      a.steps.map((s) => `<li>${escapeHtml(s)}</li>`).join("")}</ol>` : ""}
    <div class="criteria">${a.criteria.map((c) =>
      `<span class="crit ${c.kind}${c.hidden ? " hidden-crit" : ""}"
             title="${escapeHtml(c.why || "")}">${escapeHtml(c.text)}</span>`
    ).join("")}</div>`;
}

function showSetup(a) {
  const body = $("setupBody");
  body.textContent = a && a.setup ? a.setup
    : "No extra setup — the program in the editor is the whole program.";
}

// --- wiring -------------------------------------------------------------

/*
 * Run once the DOM is parsed — including when it already is.
 *
 * This file is loaded by a plain <script> at the end of <body>, so by the time
 * it executes DOMContentLoaded has usually already fired. Listening for it
 * then waits for an event that will never come again, and every control stays
 * dead while the page looks perfectly fine.
 */
function onReady(fn) {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", fn, { once: true });
  } else {
    fn();
  }
}

onReady(() => {
  applyTheme(currentTheme());

  /* The remaining one-gesture routes out of the page: the browser context
   * menu, and select-all outside the editor. Closing them means the cheapest
   * way to hand this task to an AI is a screenshot — which is the point.
   * None of this is security; the viva is. */
  document.addEventListener("contextmenu", (e) => {
    if (!e.target.closest(".editor")) e.preventDefault();
  });
  document.addEventListener("keydown", (e) => {
    const meta = e.metaKey || e.ctrlKey;
    if (!meta) return;
    const inEditor = e.target.closest(".editor");
    const k = e.key.toLowerCase();
    if (!inEditor && (k === "a" || k === "c" || k === "x" || k === "s")) {
      e.preventDefault();
    }
  });
  $("examples").addEventListener("change", (e) => chooseItem(e.target.value));
  $("play").addEventListener("click", () =>
    playing ? pause() : play(clock >= (frame?.duration ?? 0)));
  $("scrub").addEventListener("input", (e) => {
    pause();
    clock = (e.target.value / 1000) * (frame?.duration ?? 0);
    draw();
  });
  $("record").addEventListener("click", recordVideo);
  // The frame is cut to the panel's shape, so a window that changes shape has
  // to re-cut it — otherwise the diagram keeps the proportions of a panel that
  // is no longer there.
  window.addEventListener("resize", () => { if (frame && !playing) draw(); });
  $("commit").addEventListener("click", commitToRepo);
  $("handin").addEventListener("click", handIn);
  $("handinClose").addEventListener("click", () => {
    $("handinDialog").hidden = true;
    $("menuScrim").hidden = true;
  });
  $("theme").addEventListener("click", () =>
    applyTheme(document.documentElement.getAttribute("data-theme") === "light"
      ? "dark" : "light"));
  $("download").addEventListener("click", download);
  $("save").addEventListener("click", saveNow);
  const setMenu = (open) => {
    $("menu").hidden = !open;
    $("menuScrim").hidden = !open;
  };
  $("menuToggle").addEventListener("click", () => setMenu($("menu").hidden));

  // Caught in the capture phase so it works with the cursor in the editor.
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      e.stopPropagation();
      if ($("palette").hidden) openPalette(); else closePalette();
    }
  }, true);
  $("paletteScrim").addEventListener("click", closePalette);
  $("paletteInput").addEventListener("input", () => { paletteAt = 0; renderPalette(); });
  $("paletteInput").addEventListener("keydown", (e) => {
    const n = $("paletteList").querySelectorAll(".palette-item").length;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!n) return;
      paletteAt = (paletteAt + (e.key === "ArrowDown" ? 1 : n - 1)) % n;
      renderPalette();
    } else if (e.key === "Escape") {
      e.preventDefault();
      closePalette();
    }
  });
  $("paletteList").addEventListener("click", (e) => {
    const item = e.target.closest(".palette-item");
    if (!item) return;
    paletteAt = Number(item.dataset.i);
    renderPalette();
  });
  $("menuClose").addEventListener("click", () => setMenu(false));
  $("menuScrim").addEventListener("click", () => setMenu(false));
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    setMenu(false);
    if (!$("palette").hidden) closePalette();
  });
  $("setupToggle").addEventListener("click", () => {
    const b = $("setupBody");
    b.hidden = !b.hidden;
    $("setupToggle").textContent = b.hidden ? "show" : "hide";
  });

  // Shown once per browser: what this tool is and what to do with it.
  if (!localStorage.getItem("dsviz.seen")) $("welcome").hidden = false;
  $("welcomeStart").addEventListener("click", () => {
    localStorage.setItem("dsviz.seen", "1");
    $("welcome").hidden = true;
    editor?.focus();
  });

  boot().catch((err) => {
    setStatus("failed: " + err.message, "bad");
    console.error(err);
  });
});
