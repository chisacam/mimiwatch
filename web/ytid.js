/* 유튜브 주소에서 영상 id를 뽑습니다. 확장의 배경 워커와 content script가 함께
 * 씁니다 -- 두 벌이었을 때 한쪽만 호스트를 확인했습니다. 유튜브가 아닌 주소,
 * 못 읽는 주소는 빈 문자열입니다.
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
