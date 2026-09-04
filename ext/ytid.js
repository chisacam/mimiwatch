/* Pulls the video id out of a YouTube URL. The extension's service worker and
 * its content script share it -- when there were two copies, only one of them
 * checked the host. A URL that is not YouTube's, or one that cannot be read,
 * gives the empty string.
 *
 *     MimiYtId.videoIdOf("https://www.youtube.com/watch?v=abc")   // "abc"
 *     MimiYtId.videoIdOf("https://www.youtube.com/live/abc")      // "abc"
 */
(function (root) {
  "use strict";

  function videoIdOf(url) {
    try {
      const u = new URL(url);
      if (!/(^|\.)youtube\.com$/.test(u.hostname)) return "";
      const v = u.searchParams.get("v");
      if (v) return v;
      const m = u.pathname.match(/^\/(live|shorts|embed)\/([^/?#]+)/);
      return m ? m[2] : "";
    } catch (_) {
      return "";
    }
  }

  root.MimiYtId = { videoIdOf };
})(typeof window !== "undefined" ? window : globalThis);
