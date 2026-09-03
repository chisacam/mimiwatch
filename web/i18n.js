/* The string table and the lookup around it.
 *
 * Every user-visible string used to sit inline in the markup and in the app
 * modules, which meant the UI could only ever be Korean -- and the docs, now
 * English, quote labels a reader would never find on screen. Strings live here
 * instead, keyed, with an English and a Korean entry.
 *
 * This file is shared byte-identically with the extension (MV3 forbids remote
 * code), so it must run in three places: the page, the popup, and the service
 * worker. That rules out `window` -- a service worker has `self` and no DOM --
 * so the global goes on `globalThis` and every DOM call is guarded.
 *
 * Areas register their own strings by calling `MW_I18N.add({...})` from a
 * `strings/<area>.js` file. Nothing here needs editing to add an area; keeping
 * one giant table in one file is how two people editing two features end up in
 * the same merge conflict.
 */
(function (g) {
  "use strict";

  var TABLE = { en: {}, ko: {} };
  var LANGS = ["en", "ko"];
  var lang = "en";
  var listeners = [];

  /* Register strings: { "key": { en: "…", ko: "…" } }.
   * A key already present is overwritten, so an area can override a core
   * string deliberately; a duplicate by accident shows up as the wrong word on
   * screen rather than as an error, which is why keys are namespaced by area. */
  function add(entries) {
    Object.keys(entries).forEach(function (key) {
      var row = entries[key] || {};
      LANGS.forEach(function (code) {
        if (row[code] != null) TABLE[code][key] = row[code];
      });
    });
  }

  /* Look a key up. Falls back English, then to the key itself: a missing
   * string should read as a visible key, not as an empty element -- an empty
   * button is invisible and gets reported as "the button disappeared".
   * `vars` fills `{name}` placeholders. */
  function t(key, vars) {
    var s = TABLE[lang][key];
    if (s == null) s = TABLE.en[key];
    if (s == null) s = key;
    if (!vars) return s;
    return String(s).replace(/\{(\w+)\}/g, function (whole, name) {
      return Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : whole;
    });
  }

  function current() { return lang; }
  function langs() { return LANGS.slice(); }

  /* The browser's guess, for a config written before this setting existed. */
  function fromNavigator() {
    var nav = g.navigator;
    var tags = (nav && (nav.languages || (nav.language ? [nav.language] : []))) || [];
    for (var i = 0; i < tags.length; i++) {
      var code = String(tags[i]).toLowerCase().split("-")[0];
      if (LANGS.indexOf(code) >= 0) return code;
    }
    return "en";
  }

  /* Set the language and redraw. Callers that draw their own strings subscribe
   * with onChange -- re-running the static pass only fixes the markup. */
  function setLang(code, opts) {
    var next = LANGS.indexOf(code) >= 0 ? code : fromNavigator();
    var changed = next !== lang;
    lang = next;
    applyStatic();
    if (g.document && g.document.documentElement) g.document.documentElement.lang = lang;
    if (changed || (opts && opts.force)) {
      listeners.forEach(function (fn) {
        try { fn(lang); } catch (e) { console.warn("[i18n] listener failed", e); }
      });
    }
    return lang;
  }

  function onChange(fn) { if (typeof fn === "function") listeners.push(fn); }

  /* Fill the markup. `data-i18n="key"` sets the element's text;
   * `data-i18n-attr="placeholder:key; title:key"` sets attributes.
   *
   * textContent, never innerHTML: the extension's content script runs on a page
   * that enforces Trusted Types, and a string table is exactly the kind of
   * thing that would smuggle markup in there. An element that needs mixed
   * content gets a child span with its own key. */
  function applyStatic(root) {
    var doc = g.document;
    if (!doc) return;
    var scope = root || doc;
    if (!scope.querySelectorAll) return;
    var nodes = scope.querySelectorAll("[data-i18n]");
    for (var i = 0; i < nodes.length; i++) {
      nodes[i].textContent = t(nodes[i].getAttribute("data-i18n"));
    }
    var attrNodes = scope.querySelectorAll("[data-i18n-attr]");
    for (var j = 0; j < attrNodes.length; j++) {
      var el = attrNodes[j];
      el.getAttribute("data-i18n-attr").split(";").forEach(function (pair) {
        var bits = pair.split(":");
        if (bits.length !== 2) return;
        var name = bits[0].trim(), key = bits[1].trim();
        if (name && key) el.setAttribute(name, t(key));
      });
    }
  }

  g.MW_I18N = { add: add, t: t, setLang: setLang, current: current, langs: langs,
                onChange: onChange, applyStatic: applyStatic,
                fromNavigator: fromNavigator };
  /* The app modules are plain scripts sharing one global scope, so the short
   * name is the one they actually call. */
  g.t = t;
})(typeof globalThis !== "undefined" ? globalThis : self);
