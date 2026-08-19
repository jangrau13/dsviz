/*
 * The dsviz language, taught to Monaco.
 *
 * Highlighting lives here because Monarch is a JavaScript table and there is
 * nowhere else to put it. Everything a student *reads* — what a name means,
 * what it looks like written out, what completes after `budget ` — comes from
 * `dsviz.langserver`, the same table the documentation site is generated from.
 *
 * It used to be copied into this file, and the copy drifted badly. The editor
 * went on offering `service NAME: Method takes T` and `C calls S.Method with
 * X` long after the language became Python-shaped, so every completion
 * inserted syntax the parser refuses and every hover explained a statement
 * that no longer existed — while the page had `completions` and `hover`
 * imported from Python and never called them. The rule now is that no word a
 * student can be shown is written twice: the fixed grammar (blocks, types,
 * decorators, operators) is below, and the vocabulary is asked for.
 *
 * Diagnostics come from the Python linter (see app.js) and are pushed in as
 * model markers, so the editor shows exactly what the compiler saw.
 */

const LANG_ID = "dsviz";

/* The engine's own documentation, once Pyodide has it — see
 * `useLanguageService`. Null until then, and the providers answer with
 * nothing rather than with something invented here. */
let language = null;
let tokensDisposable = null;
let monacoRef_ = null;

/* The grammar's own words: blocks, types, and the line-oriented statements.
 * These are in `grammar.py` as keyword tokens, so they change only when the
 * grammar does. Anything that is a *name* — a job kind, an RDD operation, a
 * builtin, a machine setting — is deliberately not here; it arrives with the
 * language service, because those are the lists that grew and drifted. */
const BLOCKS = [
  "def", "class", "return", "pass", "for", "in", "if", "with", "parallel",
  "emit", "and", "or", "not", "mod",
];
const TYPES = ["int", "string", "pair", "void"];
const STATEMENTS = [
  "assert", "expect", "budget", "note", "lose", "use", "input", "process",
  "event", "on", "off", "clock", "speed", "combiner",
  "mappers", "reducers", "executors", "partitions", "capacity",
];

/* Entries in the documentation that are not words anyone types: `instance`
 * documents `name = Kind(...)` and `class` documents the decorator above one.
 * Colouring the prose name would be colouring a coincidence. */
const NOT_TYPED = new Set(["instance", "class", "def", "parallel", "emit"]);

/** Hand the editor the engine's documentation. Called once Pyodide is up. */
function useLanguageService(fns) {
  language = fns;
  if (monacoRef_) applyHighlighting(monacoRef_);
}

/* The vocabulary the service knows about, as plain names. Empty before it
 * arrives, which costs a little colour on the first paint and never shows a
 * word the engine has dropped. */
function vocabulary() {
  if (!language) return [];
  try {
    return language.completions()
      .map((d) => d.name)
      .filter((n) => /^[A-Za-z_]\w*$/.test(n) && !NOT_TYPED.has(n));
  } catch {
    return [];
  }
}

function applyHighlighting(monaco) {
  if (tokensDisposable) tokensDisposable.dispose();
  tokensDisposable = monaco.languages.setMonarchTokensProvider(LANG_ID, {
    blocks: BLOCKS,
    statements: STATEMENTS,
    types: TYPES,
    vocabulary: vocabulary(),
    tokenizer: {
      root: [
        [/#.*$/, "comment"],
        [/"[^"]*"/, "string"],
        // `@machine`, `@duration(0.4)` — what a class is to the simulator.
        [/@[a-zA-Z_]\w*/, "annotation"],
        // A list type is one token, so `[int]` does not read as a bracket
        // with a type loose inside it.
        [/\[\s*(?:int|string|pair)\s*\]/, "type"],
        [/->>|->|\|\|/, "operator"],
        [/[<>=!]=|[<>]/, "operator"],
        [/\b\d+(?:\.\d+)?\b/, "number"],
        // Names allow hyphens, as they do in the grammar.
        [/[a-zA-Z_][\w-]*/, {
          cases: {
            "@types": "type",
            "@blocks": "keyword",
            "@statements": "keyword.control",
            "@vocabulary": "predefined",
            "@default": "identifier",
          },
        }],
        [/[=+\-*/]/, "operator"],
      ],
    },
  });
}

function registerLanguage(monaco) {
  monacoRef_ = monaco;

  // The extension students see on disk, so a model created with a .ds
  // uri picks up this language without being told.
  monaco.languages.register({ id: LANG_ID, extensions: [".ds"],
                              aliases: ["dsviz", "ds"] });

  applyHighlighting(monaco);

  monaco.languages.setLanguageConfiguration(LANG_ID, {
    comments: { lineComment: "#" },
    brackets: [["[", "]"], ["(", ")"]],
    autoClosingPairs: [
      { open: '"', close: '"' },
      { open: "[", close: "]" },
      { open: "(", close: ")" },
    ],
    surroundingPairs: [
      { open: '"', close: '"' },
      { open: "[", close: "]" },
      { open: "(", close: ")" },
    ],
    // Indentation delimits a block here as it does in Python, so a line
    // ending in a colon indents the next one. Typing the body of a `def`,
    // a `for` or a `with parallel():` at the wrong depth is a syntax error,
    // and the editor is the cheapest place to not make it.
    onEnterRules: [{
      beforeText: /:\s*$/,
      action: { indentAction: monaco.languages.IndentAction.Indent },
    }],
  });

  // A theme tuned to the page's palette.
  monaco.editor.defineTheme("dsviz-dark", {
    base: "vs-dark",
    inherit: true,
    rules: [
      { token: "comment", foreground: "5f6771", fontStyle: "italic" },
      { token: "keyword", foreground: "4C9BE8", fontStyle: "bold" },
      { token: "keyword.control", foreground: "D96A9E" },
      { token: "annotation", foreground: "D96A9E" },
      { token: "predefined", foreground: "4FC1C9" },
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
      { token: "annotation", foreground: "b4318f" },
      { token: "predefined", foreground: "0b7a83" },
      { token: "type", foreground: "2e9e52" },
      { token: "string", foreground: "a06000" },
      { token: "number", foreground: "6f42c1" },
      { token: "operator", foreground: "5f6771" },
    ],
    colors: { "editor.background": "#f6f7f9" },
  });

  // --- completions ---
  // Everything offered is something the engine documents, filtered to the
  // exercise this file is in: a Spark task is not offered `emit`, and an RPC
  // task is not offered `reduceByKey`.
  monaco.languages.registerCompletionItemProvider(LANG_ID, {
    triggerCharacters: [" ", "\n", "."],
    provideCompletionItems(model, position) {
      if (!language) return { suggestions: [] };
      const word = model.getWordUntilPosition(position);
      const range = {
        startLineNumber: position.lineNumber, endLineNumber: position.lineNumber,
        startColumn: word.startColumn, endColumn: word.endColumn,
      };
      const line = model.getLineContent(position.lineNumber);
      const K = monaco.languages.CompletionItemKind;
      const R = monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet;

      // After `budget `, the answer is a metric, not a keyword.
      if (/\bbudget\s+\w*$/.test(line.slice(0, position.column - 1))) {
        return { suggestions: Object.entries(language.budgets())
          .map(([name, doc]) => ({
            label: name, kind: K.EnumMember, insertText: name, range,
            documentation: { value: doc },
          })) };
      }

      return { suggestions: language.completions().map((d) => {
        // A name written as a call is completed as one, with the cursor
        // between the brackets. Everything else inserts the word itself:
        // guessing at a body would be putting an answer in the editor.
        const isCall = /^\w+\s*\(/.test(d.signature || "");
        return {
          label: d.name,
          kind: isCall ? K.Function : K.Keyword,
          insertText: isCall ? `${d.name}(\${1})` : d.name,
          insertTextRules: R,
          range,
          detail: d.signature,
          documentation: { value: markdown(d) },
        };
      }) };
    },
  });

  // --- hover docs ---
  monaco.languages.registerHoverProvider(LANG_ID, {
    provideHover(model, position) {
      if (!language) return null;
      const word = model.getWordAtPosition(position);
      if (!word) return null;
      const doc = language.hover(word.word);
      if (doc && doc.name) return { contents: [{ value: markdown(doc) }] };
      const budgets = language.budgets();
      if (budgets[word.word]) {
        return { contents: [{ value: `**budget ${word.word}**` },
                            { value: budgets[word.word] }] };
      }
      return null;
    },
  });
}

/** One documented symbol as markdown: what it looks like, then what it is. */
function markdown(d) {
  const parts = [`**${d.signature || d.name}**`];
  if (d.summary) parts.push(d.summary);
  if (d.detail) parts.push(d.detail);
  if (d.example) parts.push("```python\n" + d.example + "\n```");
  return parts.join("\n\n");
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
