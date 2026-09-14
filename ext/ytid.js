/* Which site a page belongs to, what identifies the thing being watched there,
 * and where that site keeps its player. The extension's service worker, its
 * content script and its popup all read this one table.
 *
 * It used to be a single function that pulled a video id out of a YouTube URL,
 * and the rest of the knowledge was spread by hand: the popup matched a YouTube
 * address with a regular expression of its own, and the content script carried
 * YouTube's player selectors inline. Adding a second site meant finding every
 * one of those places, so they live here instead. The file keeps its old name.
 *
 *     MimiYtId.siteOf("https://chzzk.naver.com/live/c0d9").site   // "chzzk"
 *     MimiYtId.videoIdOf("https://www.youtube.com/live/abc")      // "abc"
 *     MimiYtId.videoIdOf("https://example.com/")                  // ""
 */
(function (root) {
  "use strict";

  const SITES = [
    {
      site: "youtube",
      host: (h) => /(^|\.)youtube\.com$/.test(h),
      id: (u) => u.searchParams.get("v") ||
                 (u.pathname.match(/^\/(?:live|shorts|embed)\/([^/?#]+)/) || [])[1] || "",
      // The player element is made afresh on every video change (an SPA), so
      // these are looked up each time rather than held on to.
      video: "video.html5-main-video",
      player: "#movie_player, .html5-video-player",
    },
    {
      // chzzk plays through NAVER's own player. The <video> carries a class of
      // its own, and taking it rather than the first <video> on the page
      // matters: the page also holds an ad player (`video#midPlayer`, 0x0 and
      // paused), and which of the two comes first in the document is not
      // something to rely on. The id in the address is the channel's, which is
      // the same value the server keys a glossary by (live.site_of).
      site: "chzzk",
      host: (h) => h === "chzzk.naver.com",
      id: (u) => (u.pathname.match(/^\/live\/([0-9a-f]+)/) || [])[1] || "",
      video: "video.webplayer-internal-video",
      player: ".pzp-pc__video",
    },
  ];

  /* The row for a URL, with the id already pulled out of it, or null where the
   * address belongs to no site we lay subtitles on. */
  function siteOf(url) {
    let u;
    try {
      u = new URL(url);
    } catch (_) {
      return null;                  // not an address we can read
    }
    for (const s of SITES) {
      if (!s.host(u.hostname)) continue;
      return { site: s.site, id: s.id(u), video: s.video, player: s.player };
    }
    return null;
  }

  function videoIdOf(url) {
    const s = siteOf(url);
    return s ? s.id : "";
  }

  root.MimiYtId = { SITES, siteOf, videoIdOf };
})(typeof window !== "undefined" ? window : globalThis);
