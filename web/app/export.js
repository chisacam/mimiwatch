/* mimiwatch 화면 — 자막 내보내기 대화상자.
 *
 * web/app.js 를 관심사별로 나눈 파일입니다. 전부 일반 <script> 로 index.html 이
 * 적는 순서대로 읽히며 전역 범위를 함께 씁니다 -- 모듈 문법을 쓰지 않는 것은
 * 확장과 공유하는 overlay.js 와 같은 이유입니다. 서로 부르는 것은 전부
 * 실행 시점의 함수 호출이라 파일 순서는 main.js 가 마지막이기만 하면 됩니다. */

/* ---------- 내보내기 -------------------------------------------------
 *
 * 서버가 파일을 만들어 `Content-Disposition` 으로 내려보냅니다. 브라우저에서
 * 만들지 않는 이유가 있습니다 -- 자막은 이미 서버에 있고, 화면에 그려 둔 것은
 * 지금 보고 있는 세션 하나뿐입니다. 목록의 아무 줄이나 내보내려면 어차피
 * 서버가 읽어야 합니다. */
function exportTarget() {
  if (state.live) return { value: "live:" + state.live.id, title: nowTitleText() };
  if (state.doc && !isLiveDoc()) return { value: state.doc.id, title: state.doc.title };
  return null;
}

function nowTitleText() {
  return $("now-title").textContent.trim();
}

function openExport() {
  const tgt = exportTarget();
  if (!tgt) { alert(t("panel.needTarget")); return; }
  const n = state.cues.length;
  $("export-what").textContent = state.live
    ? t("export.what.live", { title: tgt.title, n })
    : t("export.what", { title: tgt.title, n });
  syncExportHint();
  $("export-dialog").showModal();
}

/* 형식마다 걸리는 것이 다릅니다. 고르고 나서 알게 되는 것보다 고르기 전에
 * 읽는 편이 낫습니다. */
function syncExportHint() {
  const f = document.querySelector('#export-form select[name="fmt"]').value;
  const live = !!state.live;
  const key = {
    srt: live ? "export.hint.srt.live" : "export.hint.srt",
    vtt: live ? "export.hint.vtt.live" : "export.hint.vtt",
    txt: "export.hint.txt",
    json: "export.hint.json",
  }[f];
  $("export-hint").textContent = key ? t(key) : "";
}

function submitExport(e) {
  if (e.submitter && e.submitter.value === "cancel") return;
  const tgt = exportTarget();
  if (!tgt) return;
  const f = e.target;
  // 지금 보고 있는 번역 엔진의 것을 담습니다. 넘기지 않으면 서버가 있는 것
  // 중에서 고르는데, 그것이 화면과 다른 엔진일 수 있습니다.
  const url = `/api/export?id=${encodeURIComponent(tgt.value)}`
            + `&fmt=${encodeURIComponent(f.fmt.value)}`
            + `&view=${encodeURIComponent(f.view.value)}`
            + `&backend=${encodeURIComponent(state.backend || "")}`;
  // 서버가 Content-Disposition 을 붙여 주므로 그냥 가면 내려받습니다.
  // 우리 서버는 같은 출처이고 로컬이라 여기서 막힐 것이 없습니다.
  window.location.href = url;
}
