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
 *     MimiYtId.videoIdOf("https://chzzk.naver.com/video/1518")    // "chzzk-1518"
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
      // Where the subtitle log goes when it is turned on: the right-hand
      // column, and the chat whose place it takes. A site with neither named
      // simply does not offer the log (panel.js).
      column: "#secondary-inner, #secondary",
      chat: "ytd-live-chat-frame#chat, #chat",
    },
    {
      // chzzk plays through NAVER's own player. The <video> carries a class of
      // its own, and taking it rather than the first <video> on the page
      // matters: the page also holds an ad player (`video#midPlayer`, 0x0 and
      // paused), and which of the two comes first in the document is not
      // something to rely on. The id in a live address is the channel's, which
      // is the same value the server keys a glossary by (live.site_of); a
      // recording's is a different space, and `id` below says how the two are
      // kept apart.
      site: "chzzk",
      host: (h) => h === "chzzk.naver.com",
      // Two id spaces, kept apart on purpose. siteOf() matches on the host
      // alone, so a recording page (`/video/15186552`) counted as supported
      // while this returned "" for it: content.js compared "" against "" on
      // every navigation and so could never take the overlay down between two
      // recordings -- its ask() branch was unreachable as well -- and
      // background.js wrote no `vid:` record for a tab-captured one. A
      // broadcast id is the channel's hex (`/live/c0d9723c…`) and a recording
      // id is a decimal number, so one bare `[0-9a-f]+` over both would fold
      // them into a single string with nothing left to tell which was meant:
      // `15186552` is legal hex too.
      //
      // The prefix is not invented here. The server already names a chzzk
      // recording `chzzk-<number>` (transcribe_vod.probe_chzzk, the way a local
      // file is `file-<hash>`), that is the id the popup's picker carries, and
      // content.js measures the picked id against this one -- a bare number
      // here would never equal the server's and would take the overlay down the
      // moment the page moved. A broadcast stays bare for the same reason: it
      // is yt-dlp's id, which is what the server reports as `video_id`.
      id: (u) => {
        const live = (u.pathname.match(/^\/live\/([0-9a-f]+)/) || [])[1];
        if (live) return live;
        const vod = (u.pathname.match(/^\/video\/(\d+)/) || [])[1];
        return vod ? "chzzk-" + vod : "";
      },
      video: "video.webplayer-internal-video",
      player: ".pzp-pc__video",
      // The chat column, and the chat inside it. Everything else on this page is
      // named by a CSS-module hash (`_container_b8csn_2`, `_container_8lqsk_1`)
      // which is rebuilt on every deploy, so `#aside-chatting` is the one handle
      // worth holding -- and the chat is reached through it rather than by a
      // class of its own. The chat is not in an iframe; the only frame on the
      // page is a banner, and it is 548x0.
      column: "#aside-chatting",
      chat: "#aside-chatting > div",
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
      return { site: s.site, id: s.id(u), video: s.video, player: s.player,
               column: s.column || "", chat: s.chat || "" };
    }
    return null;
  }

  function videoIdOf(url) {
    const s = siteOf(url);
    return s ? s.id : "";
  }

  root.MimiYtId = { SITES, siteOf, videoIdOf };
})(typeof window !== "undefined" ? window : globalThis);
