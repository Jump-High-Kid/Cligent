// ensure_app_shell.js — iframe 안 페이지를 top frame 으로 직접 접근 시 app shell 로 wrap.
//
// 배경 (2026-05-04):
//   사이드바·관리자 메뉴는 templates/app.html (app shell) 에만 존재. iframe 안 페이지가
//   F5 새로고침 또는 직접 URL 진입 시 top frame 으로 그려지면서 사이드바·admin 메뉴 사라짐.
//   본 스크립트는 top frame 진입을 감지해 sessionStorage 에 원래 path 박고 /app 으로
//   redirect. app.html init() 이 sessionStorage 를 읽어 iframe.src 로 복원.
//
// 사용:
//   iframe 안에서 동작해야 하는 모든 페이지의 <head> 안 첫 번째 <script> 로 추가:
//     <script src="/static/js/ensure_app_shell.js"></script>
//   defer/async 금지 — 가능한 한 빠르게 redirect 해야 깜빡임 최소화.
//
// 안전망:
//   - login, join, onboard, landing, forgot 등 app shell 외부 페이지는 본 스크립트 미포함.
//   - top frame 이 아닌 iframe 안에서는 즉시 noop (정상 동작).
(function ensureAppShell() {
  try {
    if (window.self !== window.top) return;  // iframe 안: noop
    var target = window.location.pathname + window.location.search + window.location.hash;
    // 이미 /app 이면 redirect 불필요
    if (window.location.pathname === '/app' || window.location.pathname === '/') return;
    sessionStorage.setItem('cligent_target_path', target);
    window.location.replace('/app');
  } catch (_) {
    // sessionStorage 차단 등 예외 시 silent — top frame 그대로 표시 (깨끗한 fallback)
  }
})();
