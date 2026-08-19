/*
 * The dsviz language, taught to Monaco.
 *
 * Syntax highlighting, completions, hovers and inline documentation for the
 * DSL. Diagnostics come from the Python linter (see app.js) and are pushed in
 * as model markers, so the editor shows exactly what the compiler saw.
 */

const LANG_ID = "dsviz";

/* Keywords, each with the documentation shown on hover and in completions.
 * Kept as one table so the editor and the docs page cannot drift apart. */
const KEYWORDS = {
  mappers:  { sig: "mappers N",
    doc: "How many map workers to run. Defaults to one per split; with fewer, splits are shared round-robin." },
  reducers: { sig: "reducers N",
    doc: "How many reduce partitions. Every key is hashed to exactly one, which is what lets reducers run independently." },
  split:    { sig: 'split NAME: "text"',
    doc: "One unit of input. In a real cluster each split can be processed on a different machine." },
  combiner: { sig: "combiner on|off",
    doc: "Aggregate each mapper's output locally *before* the shuffle. Does not change the answer — only how much crosses the network." },
  speed:    { sig: "MACHINE speed N",
    doc: "Relative speed. 1.0 is nominal, 0.25 takes four times as long — this is how you make a straggler." },
  capacity: { sig: "capacity N",
    doc: "How many items a machine can hold before it is flagged as overloaded. Reveals data skew." },
  crashes:  { sig: "MACHINE crashes [at T]",
    doc: "Take a machine down. In-memory state is lost, and messages already in flight to it are dropped." },
  restarts: { sig: "MACHINE restarts [at T]",
    doc: "Bring a machine back up. It returns with no state." },
  expect:   { sig: "expect KEY = N",
    doc: "Assert a word's final count. This is the correctness check." },
  budget:   { sig: "budget METRIC < N",
    doc: "A non-functional limit. Correctness is table stakes; budgets are what separate a good design from a working one." },
  service:  { sig: "service NAME: Method takes T",
    doc: "Declare a gRPC-style server and one method it handles, with how long that method takes." },
  client:   { sig: "client NAME",
    doc: "Declare a machine that makes RPCs." },
  calls:    { sig: "C calls S.Method [with X] [deadline T] [retries N]",
    doc: "A synchronous RPC. The caller's clock advances past the round trip, so a slow server shows up as caller idle time." },
  note:     { sig: "note TEXT",
    doc: "A caption shown on the diagram at this point in the run." },
};

const BUDGETS = {
  network:   "Messages crossing the network. A combiner reduces this sharply.",
  makespan:  "Wall-clock seconds to finish the whole job.",
  imbalance: "Busiest machine's work divided by the mean. 1.0 is perfect balance.",
  tail:      "Slowest task divided by the median. High means stragglers.",
  memory:    "Most items held by any one machine — the skew measure.",
  faults:    "Work lost and redone because something failed.",
};

function registerLanguage(monaco) {
  // The extension students see on disk, so a model created with a .ds
  // uri picks up this language without being told.
  monaco.languages.register({ id: LANG_ID, extensions: [".ds"],
                              aliases: ["dsviz", "ds"] });

  // --- syntax highlighting ---
  monaco.languages.setMonarchTokensProvider(LANG_ID, {
    keywords: Object.keys(KEYWORDS),
    budgets: Object.keys(BUDGETS),
    tokenizer: {
      root: [
        [/#.*$/, "comment"],
        [/"[^"]*"/, "string"],
        [/\b(mappers|reducers|split|combiner|capacity|expect|budget|note|service|client|speed)\b/, "keyword"],
        [/\b(crashes|restarts|calls|takes|with|deadline|retries|on|off|at)\b/, "keyword.control"],
        [/\b(network|makespan|imbalance|tail|memory|faults)\b/, "type"],
        [/\b\d+(\.\d+)?\b/, "number"],
        [/->>|->|\|\||[<>=]=?/, "operator"],
        [/\b[A-Za-z_][\w-]*\b/, "identifier"],
      ],
    },
  });

  monaco.languages.setLanguageConfiguration(LANG_ID, {
    comments: { lineComment: "#" },
    brackets: [["[", "]"]],
    autoClosingPairs: [{ open: '"', close: '"' }, { open: "[", close: "]" }],
  });

  // A theme tuned to the page's palette.
  monaco.editor.defineTheme("dsviz-dark", {
    base: "vs-dark",
    inherit: true,
    rules: [
      { token: "comment", foreground: "5f6771", fontStyle: "italic" },
      { token: "keyword", foreground: "4C9BE8", fontStyle: "bold" },
      { token: "keyword.control", foreground: "D96A9E" },
      { token: "type", foreground: "63C77A" },
      { token: "string", foreground: "E8B44C" },
      { token: "number", foreground: "9B7BE8" },
      { token: "operator", foreground: "9aa0a6" },
    ],
    colors: { "editor.background": "#16191d" },
  });

  monaco.editor.defineTheme("dsviz-light", {
    base: "vs",
    inherit: true,
    rules: [
      { token: "comment", foreground: "6a737d", fontStyle: "italic" },
      { token: "keyword", foreground: "1f6fd0", fontStyle: "bold" },
      { token: "keyword.control", foreground: "b4318f" },
      { token: "type", foreground: "2e9e52" },
      { token: "string", foreground: "a06000" },
      { token: "number", foreground: "6f42c1" },
      { token: "operator", foreground: "5f6771" },
    ],
    colors: { "editor.background": "#f6f7f9" },
  });

  // --- completions ---
  monaco.languages.registerCompletionItemProvider(LANG_ID, {
    triggerCharacters: [" ", "\n"],
    provideCompletionItems(model, position) {
      const word = model.getWordUntilPosition(position);
      const range = {
        startLineNumber: position.lineNumber, endLineNumber: position.lineNumber,
        startColumn: word.startColumn, endColumn: word.endColumn,
      };
      const line = model.getLineContent(position.lineNumber);
      const K = monaco.languages.CompletionItemKind;
      const R = monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet;

      // After `budget `, offer the metric names instead of keywords.
      if (/\bbudget\s+\w*$/.test(line.slice(0, position.column - 1))) {
        return { suggestions: Object.entries(BUDGETS).map(([name, doc]) => ({
          label: name, kind: K.EnumMember, insertText: name, range,
          documentation: { value: doc },
        })) };
      }

      const snippets = {
        split: 'split ${1:doc1}: "${2:the cat sat}"',
        mappers: "mappers ${1:3}",
        reducers: "reducers ${1:2}",
        combiner: "combiner ${1|on,off|}",
        capacity: "capacity ${1:8}",
        expect: "expect ${1:the} = ${2:3}",
        budget: "budget ${1|network,makespan,imbalance,tail,memory,faults|} < ${2:40}",
        note: "note ${1:what is happening}",
        service: "service ${1:MrMapServer}: ${2:Map} takes ${3:0.5}",
        client: "client ${1:MrClient}",
        calls: "${1:MrClient} calls ${2:MrMapServer}.${3:Map} with ${4:chunk001.txt}",
        speed: "${1:mapper-2} speed ${2:0.25}",
        crashes: "${1:mapper-2} crashes",
        restarts: "${1:mapper-2} restarts",
      };

      return { suggestions: Object.entries(KEYWORDS).map(([name, info]) => ({
        label: name,
        kind: K.Keyword,
        insertText: snippets[name] || name,
        insertTextRules: R,
        range,
        detail: info.sig,
        documentation: { value: `**${info.sig}**\n\n${info.doc}` },
      })) };
    },
  });

  // --- hover docs ---
  monaco.languages.registerHoverProvider(LANG_ID, {
    provideHover(model, position) {
      const word = model.getWordAtPosition(position);
      if (!word) return null;
      const w = word.word.toLowerCase();
      const info = KEYWORDS[w];
      if (info) {
        return { contents: [{ value: `**${info.sig}**` }, { value: info.doc }] };
      }
      if (BUDGETS[w]) {
        return { contents: [{ value: `**budget ${w}**` }, { value: BUDGETS[w] }] };
      }
      return null;
    },
  });
}

/** Push linter diagnostics into the editor as squiggles. */
function setDiagnostics(monaco, model, diags) {
  const markers = diags.map((d) => {
    const line = Math.min(Math.max(d.line, 1), model.getLineCount());
    return {
      message: d.hint ? `${d.message}\n\n${d.hint}` : d.message,
      severity: d.severity === "error"
        ? monaco.MarkerSeverity.Error
        : monaco.MarkerSeverity.Warning,
      startLineNumber: line,
      startColumn: 1,
      endLineNumber: line,
      endColumn: model.getLineLength(line) + 1,
    };
  });
  monaco.editor.setModelMarkers(model, "dsviz", markers);
}
