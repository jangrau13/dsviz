/*
 * The page, loaded and clicked.
 *
 * Every Python suite here passed while the editor was dead on arrival: the
 * wiring block threw on its first line, so no control had a listener and the
 * page just sat there looking correct. Parsing the file proves nothing, and
 * neither does grepping it — the only evidence that a button works is pressing
 * it against a real DOM.
 *
 * jsdom loads index.html and its scripts the way a browser does, in order,
 * firing DOMContentLoaded and load. Monaco does not survive headless, so the
 * editor itself is out of scope; everything around it is not.
 *
 *     node tests/ui_test.mjs            # serves web/ on a free port itself
 */

import { JSDOM, VirtualConsole } from "jsdom";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join, extname, normalize } from "node:path";

const WEB = join(dirname(fileURLToPath(import.meta.url)), "..", "web");
const TYPES = { ".html": "text/html", ".js": "text/javascript",
                ".css": "text/css", ".json": "application/json" };

let failures = 0;
const ok = (label, pass, detail = "") => {
  if (!pass) failures++;
  console.log(`${pass ? "ok  " : "FAIL"} ${label}${detail ? " — " + detail : ""}`);
};

/** Serve web/ so scripts load over http, as they do in a codespace. */
function serve() {
  const server = createServer(async (req, res) => {
    const rel = normalize(decodeURIComponent(req.url.split("?")[0]))
      .replace(/^(\.\.[/\\])+/, "");
    const file = join(WEB, rel === "/" ? "index.html" : rel);
    try {
      const body = await readFile(file);
      res.writeHead(200, { "content-type": TYPES[extname(file)] ?? "application/octet-stream" });
      res.end(body);
    } catch {
      res.writeHead(404).end("not found");
    }
  });
  return new Promise((r) => server.listen(0, "127.0.0.1", () => r(server)));
}

const server = await serve();
const url = `http://127.0.0.1:${server.address().port}/`;

// Uncaught exceptions are the failure mode under test: one throw in the wiring
// block silently disables every listener registered after it.
const thrown = [];
const vc = new VirtualConsole();
vc.on("jsdomError", (e) => thrown.push(e.message.split("\n")[0]));

const dom = await JSDOM.fromURL(url, {
  runScripts: "dangerously", resources: "usable", virtualConsole: vc,
  pretendToBeVisual: true,
});
const { window } = dom;
await new Promise((r) => window.addEventListener("load", r, { once: true }));
await new Promise((r) => setTimeout(r, 500));

const doc = window.document;
const $ = (id) => doc.getElementById(id);
const click = (el) => el.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));

// Monaco cannot run headless; its failures are the harness, not the page.
const MONACO = /Emitter|queryCommandSupported|reading 'register'|monaco/i;
const real = thrown.filter((m) => !MONACO.test(m));
ok("no uncaught error from the page's own code", real.length === 0, real[0] ?? "");

// --- the wiring block, from its first line to its last -------------------
// applyTheme() is the first statement; the welcome handler is nearly the last.
// Both ends passing is what says the block ran to completion.

ok("theme helpers are defined", typeof window.applyTheme === "function"
   && typeof window.currentTheme === "function");

const start = $("welcomeStart"), welcome = $("welcome");
window.localStorage.removeItem("dsviz.seen");
welcome.hidden = false;
click(start);
ok("Start sets the hidden attribute", welcome.hidden === true);
// The attribute alone proves nothing: `hidden` only supplies a default
// display, so any rule with its own display outranks it and the modal stays
// on screen. Ask the cascade what actually renders.
const shown = (el) => window.getComputedStyle(el).display !== "none";
ok("Start actually removes the dialog from the page", !shown(welcome),
   `computed display: ${window.getComputedStyle(welcome).display}`);
ok("Start records that it was seen", window.localStorage.getItem("dsviz.seen") === "1");

const before = doc.documentElement.getAttribute("data-theme");
click($("theme"));
const after = doc.documentElement.getAttribute("data-theme");
ok("theme toggle switches the document", before !== after, `${before} -> ${after}`);
ok("theme is one of light/dark", ["light", "dark"].includes(after), after);

click($("menuToggle"));
ok("hamburger opens", shown($("menu")));
click($("menuClose"));
ok("hamburger closes", !shown($("menu")));

const setupBody = $("setupBody"), sBefore = setupBody.hidden;
click($("setupToggle"));
ok("setup section toggles", setupBody.hidden !== sBefore);

// --- what is on screen, and what folds away ------------------------------
/*
 * Two panels are read and then in the way: the task description above the
 * code, and the prose inside the file. Both fold, and the results panel takes
 * the whole right-hand side when a run has more to say than a strip can hold.
 * Monaco cannot run headless, so the line-hiding itself is out of scope —
 * which lines it would hide is not, and that is the part with a rule in it.
 */
window.showBrief({
  title: "Getting started 1", brief: "a world of machines", criteria: [],
});
const brief = $("brief"), toggle = $("briefToggle");
ok("the brief renders a fold button", toggle !== null);
ok("the brief starts open", !brief.classList.contains("collapsed"));
click(toggle);
ok("the brief folds", brief.classList.contains("collapsed"));
ok("folding the brief hides the description", !shown(doc.querySelector(".brief-body")),
   "the body is what folds away");
ok("folding the brief keeps the title", shown(doc.querySelector(".brief-title")),
   "which task this is has to stay on screen");
ok("the fold is remembered", window.localStorage.getItem("dsviz.brief") === "folded");
ok("the button says how to get it back", /show/i.test(toggle.textContent));
click(toggle);
ok("the brief unfolds", !brief.classList.contains("collapsed"));
// A new task redraws the brief; the fold must survive that redraw.
click(toggle);
window.showBrief({ title: "Getting started 2", brief: "…", criteria: [] });
ok("a new task keeps the fold", $("brief").classList.contains("collapsed"));
click($("briefToggle"));

const view = doc.querySelector(".view-pane");
ok("the results start beside the diagram", !view.classList.contains("results-full"));
click($("expand"));
ok("the results can take the whole panel", view.classList.contains("results-full"));
ok("the expanded panel is remembered",
   window.localStorage.getItem("dsviz.results") === "full");
ok("the button offers the diagram back", /diagram/i.test($("expand").textContent));
// Playing an animation that is not on screen shows nothing, so it comes back.
click($("play"));
ok("pressing play brings the diagram back", !view.classList.contains("results-full"));

const comments = $("comments");
ok("the comments toggle exists", comments !== null);
click(comments);
ok("hiding comments is remembered",
   window.localStorage.getItem("dsviz.comments") === "hidden");
ok("the button offers them back", /show/i.test(comments.textContent));
click(comments);
ok("showing comments is remembered",
   window.localStorage.getItem("dsviz.comments") === "shown");

/* Which lines fold away. A header block owns the blank line under it — left
 * behind, folding turns the top of a file into a column of nothing — while a
 * comment sitting against code does not, because there the blank belongs to
 * the code below it. */
const model = (text) => {
  const lines = text.split("\n");
  return {
    getLineCount: () => lines.length,
    getLineContent: (i) => lines[i - 1],
    getLineMaxColumn: (i) => lines[i - 1].length + 1,
  };
};
const runs = (text) => JSON.stringify(window.commentRuns(model(text)));
ok("a header block takes its blank line with it",
   runs("# one\n# two\n\n@machine") === "[[1,3]]", runs("# one\n# two\n\n@machine"));
ok("a comment against code leaves the blank alone",
   runs("x = 1\n# why\n\ny = 2") === "[[2,2]]", runs("x = 1\n# why\n\ny = 2"));
ok("code with no comments folds nothing",
   runs("x = 1\n\ny = 2") === "[]", runs("x = 1\n\ny = 2"));
ok("a comment after code on the same line is code",
   runs("x = 1  # why") === "[]", runs("x = 1  # why"));

// --- every id the script reaches for must exist --------------------------
// A renamed id is invisible until the control is pressed; this catches it at
// the point the rename happens instead.
const source = await readFile(join(WEB, "app.js"), "utf8");
// Strip comments first: prose about `$("x")` is not a lookup.
const code = source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
const ids = [...new Set([...code.matchAll(/\$\("([^"]+)"\)/g)].map((m) => m[1]))];
const created = new Set([...code.matchAll(/id="([^"]+)"/g)].map((m) => m[1]));
const missing = ids.filter((id) => !$(id) && !created.has(id));
ok("every $() id resolves", missing.length === 0, missing.join(", "));

// --- every function the code calls must exist ---------------------------
/*
 * Three separate bugs have been a function that was called and never written:
 * applyTheme, then setJourney. Each threw at run time and left the page dead
 * while every file parsed and every Python suite passed. Clicking cannot find
 * these — the crash is inside run(), which needs Pyodide — so the call graph is
 * checked directly instead.
 */
// Strings hold CSS and prose that look like calls (`url(#ah)`, "attempt(s)"),
// so they are removed before anything is counted.
const noStrings = code
  .replace(/`(?:[^`\\]|\\.)*`/g, "``")
  .replace(/"(?:[^"\\]|\\.)*"/g, '""')
  .replace(/'(?:[^'\\]|\\.)*'/g, "''");
// Parameters are in scope inside their function, so they are declarations too.
const param = (p) => p.trim().replace(/[=:].*$/, "").replace(/[^\w$]/g, "");
const declared = new Set([
  ...[...noStrings.matchAll(
    /(?:function\s+([A-Za-z_$][\w$]*)|(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=)/g)]
    .map((m) => m[1] || m[2]),
  // `new Promise((resolve) => …` opens two parens, and `[^)]*` happily eats the
  // inner one, so the first parameter arrives as "(resolve". Trim whatever is
  // not part of the name — that false positive costs a real name its
  // declaration and reports it as undefined.
  ...[...noStrings.matchAll(/\(([^)]*)\)\s*=>/g)]
    .flatMap((m) => m[1].split(",").map(param)),
  ...[...noStrings.matchAll(/function[^(]*\(([^)]*)\)/g)]
    .flatMap((m) => m[1].split(",").map(param)),
].filter(Boolean));
// Bare calls only: `foo(` at a statement or expression position, never `.foo(`.
const bare = [...noStrings.matchAll(/(^|[^.\w$])([a-z_$][\w$]*)\s*\(/g)]
  .map((m) => m[2]);
const KEYWORDS = new Set(["if", "for", "while", "switch", "catch", "return",
  "typeof", "function", "await", "new", "do", "else", "try", "in", "of",
  // `async () => …` reads as a call to this scanner, but async is a keyword.
  "delete", "void", "yield", "throw", "case", "async"]);
const GLOBALS = new Set(["require", "fetch", "setTimeout", "clearTimeout",
  "setInterval", "parseInt", "parseFloat", "isNaN", "alert", "confirm",
  "loadPyodide", "structuredClone", "requestAnimationFrame", "queueMicrotask",
  "encodeURIComponent", "decodeURIComponent", "getComputedStyle"]);
const undef = [...new Set(bare)].filter(
  (n) => !declared.has(n) && !KEYWORDS.has(n) && !GLOBALS.has(n)
         && typeof window[n] !== "function");
ok("every function the page calls is defined", undef.length === 0,
   undef.join(", "));

// --- the video export ----------------------------------------------------
/*
 * MediaRecorder does not exist headless, so the encoding cannot be exercised
 * here. What can is the part that has actually broken twice in other pages:
 * the standalone SVG handed to the rasteriser. It must carry its own styles —
 * an external stylesheet does not follow an image into a canvas, and text
 * would come out unstyled black at the browser's default size — and it must
 * carry no unresolved var(), which paints nothing at all.
 */
ok("the recorder is wired to a button", $("record") !== null);
const standalone = window.stageSvgText(640, 360);
ok("the recorded frame is a self-contained SVG",
   /<svg[^>]*xmlns=/.test(standalone) && /<style/.test(standalone));
ok("the recorded frame carries the diagram's own text rules",
   /\.node-label/.test(standalone) && /\.chip-label/.test(standalone));
ok("no var() survives into the recorded frame", !/var\(--/.test(standalone),
   "an unresolved custom property paints nothing outside the document");
ok("recording without a run says so rather than throwing",
   (() => { try { window.recordVideo(); return true; } catch { return false; } })());

// --- the served files must be valid in their own language ---------------
/*
 * A banner comment added by sync.py used `//` in the stylesheet. CSS has no
 * such comment, so the browser discarded the rules after it — including the
 * one that closes the welcome dialog. Every check below still passed: the file
 * was served, the rule was present in the text, and jsdom does not parse CSS.
 * What was missing was asking whether the bytes are valid at all.
 */
const cssText = await readFile(join(WEB, "style.css"), "utf8");
ok("the stylesheet has no // comments", !/^\s*\/\//m.test(cssText),
   "CSS has no // comment; the browser drops everything after one");
const braces = (cssText.match(/{/g) || []).length - (cssText.match(/}/g) || []).length;
ok("the stylesheet's braces balance", braces === 0, `off by ${braces}`);

const htmlText = await readFile(join(WEB, "index.html"), "utf8");
ok("nothing precedes the doctype", /^\s*<!doctype/i.test(htmlText),
   "anything before it puts the browser into quirks mode");

// The rule that closes every dialog must survive the cascade, which means it
// has to be inside no block and after nothing that breaks parsing.
const beforeHidden = cssText.slice(0, cssText.indexOf("[hidden]"));
ok("[hidden] is reachable — no unclosed block before it",
   (beforeHidden.match(/{/g) || []).length ===
   (beforeHidden.match(/}/g) || []).length,
   "an unclosed rule above it would swallow it");

// --- the cascade, checked in the stylesheet ------------------------------
/*
 * jsdom resolves `hidden` against a rule's own display incorrectly — it
 * answers "none" where a browser paints the element — so no amount of
 * clicking here can catch a modal that refuses to close. The stylesheet is
 * checked directly instead: one authoritative [hidden] rule, which is what
 * makes toggling the attribute enough.
 */
const css = await readFile(join(WEB, "style.css"), "utf8");
const authoritative = /\[hidden\][^{]*\{[^}]*display:\s*none\s*!important/.test(css);
ok("[hidden] is declared authoritative in the stylesheet", authoritative,
   "without it, any rule with its own display keeps a hidden element on screen");

// Anything the script toggles must not out-rank that rule.
const toggled = [...new Set([...code.matchAll(/\$\("([^"]+)"\)\.hidden/g)]
  .map((m) => m[1]))];
const classesOf = (id) => {
  const el = $(id);
  return el ? [...el.classList] : [];
};
const risky = toggled.flatMap(classesOf)
  .filter((c) => new RegExp(`\\.${c}\\b[^{]*\\{[^}]*display:\\s*[^n]`, "s").test(css))
  .filter(() => !authoritative);
ok("no toggled element is pinned visible by its own display", risky.length === 0,
   risky.join(", "));

// --- the repo binding the commit button depends on -----------------------
const tag = doc.querySelector('meta[name="dsviz-repo"]');
ok("the dsviz-repo meta tag exists", tag !== null,
   "setup.sh rewrites this; without it the commit button has no target");

/*
 * --- the hand-in dialog must not claim a grading that did not happen ------
 *
 * The held-out input is stripped from the published copy, so in the browser
 * `judge_assignment` reports `graded_on_holdout: false`. The dialog used to
 * announce "re-run on input you have not seen" either way, which is the tool
 * lying about how the student was graded.
 */
const note = window.handInNote;
ok("handInNote is defined", typeof note === "function");

if (typeof note === "function") {
  const browser = { graded_on_holdout: false };
  const ci = { graded_on_holdout: true };

  // The defect was the past-tense claim: that the code *had already* been run
  // on unseen input. Naming a future graded run that will use unseen input is
  // both true and the thing the student needs to know, so it must stay.
  const claimsItHappened = (s) => /(was|were) re-run on input you have not seen/.test(s);

  ok("a browser pass does not claim it already ran on unseen input",
     !claimsItHappened(note(browser, true)), note(browser, true));
  ok("a browser failure does not claim it already ran on unseen input",
     !claimsItHappened(note(browser, false)), note(browser, false));
  ok("a browser pass says which input it did use",
     /example you can see/.test(note(browser, true)));
  ok("a browser pass points at the graded run on push",
     /push/.test(note(browser, true)));
  ok("the hold-out wording is the one that claims it happened",
     claimsItHappened(note(ci, true)));
  ok("a real hold-out pass still says so",
     /have not seen/.test(note(ci, true)));
  ok("a real hold-out failure still says so",
     /have not seen/.test(note(ci, false)));
}

dom.window.close();
server.close();

console.log(failures ? `\n${failures} UI CHECK(S) FAILED` : "\nALL UI TESTS PASSED");
process.exit(failures ? 1 : 0);
