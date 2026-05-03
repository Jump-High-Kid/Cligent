/**
 * chat_sse.js — SSE/EventSource 저수준 transport (v10 plan E3)
 *
 * 두 가지 transport 제공:
 *   1) postSSE(url, body, on)
 *      절충안 A: 단일 POST + text/event-stream 응답.
 *      본문 streaming 단계용 (Phase 1D에서 본격 사용).
 *   2) subscribeJob(url, on)
 *      절충안 B: GET EventSource. 이미지 큐 진행 표시용.
 *
 * SSE 프레이밍 규약 (서버·클라이언트 공통):
 *   data: {JSON}\n\n
 *     - 항상 한 줄 JSON. event: 라벨은 JSON.type 필드로 대체.
 *   네트워크 끊김은 fetch reject → on.onError 호출.
 *   서버 200 + 비-스트리밍 응답(JSON)은 on.onJson으로 패스스루.
 */

(function (global) {
  'use strict';

  /**
   * POST + ReadableStream 파서.
   * 응답 Content-Type을 보고 자동 분기:
   *   - application/json → on.onJson(obj) 1회
   *   - text/event-stream → on.onChunk(parsed) 여러 번
   *   - 그 외 → on.onError(err)
   * 어떤 경우든 종료 시 on.onDone() 호출.
   *
   * @param {string} url
   * @param {object} body
   * @param {{onChunk?:(o:object)=>void, onJson?:(o:object)=>void, onError?:(e:Error)=>void, onDone?:()=>void}} on
   * @returns {{abort:()=>void}}
   */
  async function postSSE(url, body, on) {
    on = on || {};
    const controller = new AbortController();
    const handle = { abort: () => controller.abort() };

    try {
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream, application/json' },
        body: JSON.stringify(body || {}),
        signal: controller.signal,
        credentials: 'same-origin',
      });

      if (res.status === 401) {
        window.location.href = '/login';
        return handle;
      }
      if (!res.ok) {
        let detail = '';
        try { const j = await res.json(); detail = j.detail || ''; } catch (_) {}
        throw new Error(detail || `HTTP ${res.status}`);
      }

      const ctype = (res.headers.get('content-type') || '').toLowerCase();

      // JSON 분기 (옵션 단계 응답)
      if (ctype.includes('application/json')) {
        const obj = await res.json();
        if (on.onJson) on.onJson(obj);
        if (on.onDone) on.onDone();
        return handle;
      }

      // SSE 분기 (본문 streaming)
      if (!ctype.includes('text/event-stream')) {
        throw new Error(`예상치 못한 응답 형식: ${ctype}`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buffer = '';
      // read() timeout — 서버 keepalive(15s)가 살아있으면 절대 발화 안 함.
      // 모바일 백그라운드에서 fetch가 stalled 상태로 영원히 await하는 케이스 방어.
      // timeout 발화 시 controller.abort() → catch → onError → onDone 정상 루트로 빠짐.
      const READ_TIMEOUT_MS = 30000;
      while (true) {
        let timeoutId;
        const timeoutPromise = new Promise((_, reject) => {
          timeoutId = setTimeout(() => reject(new Error('SSE read timeout')), READ_TIMEOUT_MS);
        });
        let result;
        try {
          result = await Promise.race([reader.read(), timeoutPromise]);
        } finally {
          clearTimeout(timeoutId);
        }
        const { value, done } = result;
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        // SSE 이벤트 경계: '\n\n'
        let idx;
        while ((idx = buffer.indexOf('\n\n')) >= 0) {
          const raw = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          const parsed = parseSSEFrame(raw);
          if (parsed && on.onChunk) on.onChunk(parsed);
        }
      }
      if (on.onDone) on.onDone();
    } catch (err) {
      if (err.name === 'AbortError') return handle;
      // SSE read timeout 또는 네트워크 오류 — 명시적 abort로 stream 정리
      try { controller.abort(); } catch (_) {}
      if (on.onError) on.onError(err);
      if (on.onDone) on.onDone();
    }
    return handle;
  }

  /**
   * SSE 프레임 1건 파싱. data: 줄만 추출 → JSON.parse.
   * 멀티라인 data: 도 지원 (RFC 호환).
   */
  function parseSSEFrame(raw) {
    const lines = raw.split('\n');
    const dataParts = [];
    for (const line of lines) {
      if (line.startsWith('data:')) {
        dataParts.push(line.slice(5).trimStart());
      }
      // event: / id: / retry: 는 현재 미사용 (type 필드로 대체)
    }
    if (dataParts.length === 0) return null;
    const dataStr = dataParts.join('\n');
    try { return JSON.parse(dataStr); }
    catch (_) { return null; }  // 파싱 실패는 조용히 skip
  }

  /**
   * GET EventSource — 이미지 큐 진행 표시용 (Phase 1D 진입 시 사용).
   * 자동 재연결은 브라우저 기본동작에 위임 (Last-Event-ID 헤더).
   *
   * @param {string} url
   * @param {{onMessage?:(o:object)=>void, onError?:(e:Event)=>void, onOpen?:()=>void}} on
   * @returns {{close:()=>void}}
   */
  function subscribeJob(url, on) {
    on = on || {};
    const es = new EventSource(url, { withCredentials: true });
    if (on.onOpen) es.addEventListener('open', on.onOpen);
    es.addEventListener('message', (ev) => {
      try {
        const obj = JSON.parse(ev.data);
        if (on.onMessage) on.onMessage(obj);
      } catch (_) { /* skip 파싱 실패 */ }
    });
    if (on.onError) es.addEventListener('error', on.onError);
    return { close: () => es.close() };
  }

  global.ChatSSE = { postSSE, subscribeJob, parseSSEFrame };
})(window);
