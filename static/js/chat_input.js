/**
 * chat_input.js — 입력창 + 단축키 + 진입점 (v10 plan E2')
 *
 * 책임:
 *   - 입력창 자동 높이 조절 (max 4줄 = 144px)
 *   - Enter = 전송 / Shift+Enter = 줄바꿈
 *   - 단축키 1~4: 입력창 비어있을 때 해당 옵션 칩 자동 클릭 (claude.ai 패턴)
 *   - 칩 클릭 = 입력창에 텍스트 채우고 전송 (E2' 결정)
 *   - 피드백 아이콘 클릭 = 입력창 모드 전환 + system 메시지
 *   - 페이지 로드 시 세션 복구 시도 → 실패하면 빈 화면 칩 노출
 */

(function (global) {
  'use strict';

  const FEEDBACK_HINT = '피드백을 입력해주세요. 불편했던 점이나 개선 의견을 자유롭게 남겨주세요.';
  let feedbackMode = false;  // 피드백 입력 모드 토글

  function $(id) { return document.getElementById(id); }

  // ── 입력창 자동 높이 ──────────────────────────────────────────
  function autoResize(el) {
    el.style.height = 'auto';
    const max = 144;  // CSS --input-max-height
    el.style.height = Math.min(el.scrollHeight, max) + 'px';
  }

  // ── 전송 ──────────────────────────────────────────────────────
  function fillAndSend(text) {
    const input = $('chatInput');
    if (!input) return;
    input.value = text || '';
    autoResize(input);
    submitInput();
  }

  function submitInput() {
    const input = $('chatInput');
    const text = (input.value || '').trim();
    if (!text) return;
    if (text.length > 2000) {
      alert('메시지는 2,000자 이내로 입력해주세요.');
      return;
    }
    if (global.ChatState && global.ChatState.isSending()) return;

    // 피드백 모드 분기
    if (feedbackMode) {
      submitFeedback(text);
      input.value = '';
      autoResize(input);
      return;
    }

    input.value = '';
    autoResize(input);
    if (global.ChatState) global.ChatState.sendTurn(text);
  }

  // ── 피드백 ────────────────────────────────────────────────────
  function enterFeedbackMode() {
    if (feedbackMode) return;
    feedbackMode = true;
    const input = $('chatInput');
    if (input) {
      input.placeholder = '피드백을 입력해주세요...';
      input.focus();
    }
    // system 메시지로 안내 (메시지 영역에 인라인 노출)
    showSystemMessage(FEEDBACK_HINT);
  }

  function exitFeedbackMode() {
    feedbackMode = false;
    const input = $('chatInput');
    if (input) input.placeholder = '오늘 쓸 주제를 입력하세요...';
  }

  async function submitFeedback(message) {
    const sid = global.ChatState ? global.ChatState.getSessionId() : null;
    const stage = $('stageText') ? $('stageText').textContent : '';
    try {
      const res = await fetch('/api/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({
          message: message,
          page: 'blog_chat',
          context: {
            session_id: sid || null,
            stage_text: stage,
            ts_client: new Date().toISOString(),
          },
        }),
      });
      if (res.status === 401) { window.location.href = '/login'; return; }
      if (res.ok) {
        showSystemMessage('피드백 감사합니다. 더 나은 Cligent로 개선하겠습니다.');
      } else {
        showSystemMessage('피드백 전송에 실패했어요. 잠시 후 다시 시도해주세요.');
      }
    } catch (_) {
      showSystemMessage('피드백 전송에 실패했어요. 네트워크를 확인해주세요.');
    }
    exitFeedbackMode();
  }

  function showSystemMessage(text) {
    const inner = $('messagesInner');
    if (!inner) return;
    const empty = $('emptyState');
    if (empty) empty.hidden = true;
    const row = document.createElement('div');
    row.className = 'msg-row system';
    const bubble = document.createElement('div');
    bubble.className = 'bubble system';
    bubble.setAttribute('role', 'note');
    bubble.textContent = text;
    row.appendChild(bubble);
    inner.appendChild(row);
    const m = $('chatMessages');
    if (m) m.scrollTop = m.scrollHeight;
  }

  // ── 단축키 1~4 ────────────────────────────────────────────────
  function handleShortcutKey(e) {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const input = $('chatInput');
    if (!input) return;
    // 입력창에 텍스트가 있으면 단축키 무시 (사용자가 직접 입력 중)
    if ((input.value || '').length > 0) return;
    // 입력창에 포커스 없을 때만 (포커스 있으면 정상 타이핑이 우선)
    if (document.activeElement === input) return;

    const key = e.key;
    if (!/^[1-9]$/.test(key)) return;
    const opts = global.ChatState ? global.ChatState.getPendingOptions() : [];
    const idx = parseInt(key, 10) - 1;
    if (idx < 0 || idx >= opts.length) return;
    e.preventDefault();
    fillAndSend(opts[idx].label || opts[idx].id || '');
  }

  // ── 진입점 ────────────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', async () => {
    const input = $('chatInput');
    const send = $('sendBtn');
    const fb = $('feedbackBtn');

    if (input) {
      // IME 조합 상태 추적 — 한글 입력 중 Enter 처리 방어 (이슈 7)
      let composing = false;
      input.addEventListener('compositionstart', () => { composing = true; });
      input.addEventListener('compositionend', () => { composing = false; });

      input.addEventListener('input', () => {
        autoResize(input);
        if (send) send.disabled = !(input.value || '').trim() || global.ChatState.isSending();
      });
      input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          // IME 조합 중 Enter — 한글 마지막 글자가 다음 turn으로 새지 않도록 차단
          if (composing || e.isComposing || e.keyCode === 229) return;
          e.preventDefault();
          submitInput();
        }
      });
    }
    if (send) {
      send.addEventListener('click', submitInput);
    }
    if (fb) {
      fb.addEventListener('click', enterFeedbackMode);
    }
    // 헤더 뒤로가기 버튼은 2026-05-02 삭제 — 사이드바·하단 nav로 대체

    document.addEventListener('keydown', handleShortcutKey);

    // 백그라운드 → foreground 복귀 시 서버 진실 동기화 (2026-05-04)
    // 모바일 OS·캐리어가 SSE를 끊더라도 서버는 작업을 끝까지 수행함.
    // 복귀 즉시 GET session으로 진행 상태 확인 → 완료면 결과 표시, 진행 중이면 폴링 시작.
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible' && global.ChatState && global.ChatState.syncOnResume) {
        global.ChatState.syncOnResume();
      }
    });

    // 세션 복구 시도 → 실패하면 빈 화면 칩 노출 (인사는 빈 화면에 정적 표시)
    // 첫 turn 호출은 사용자 첫 액션(칩 클릭/입력) 시점 — v9 명세 Pass 7 D7
    let restored = false;
    if (global.ChatState) {
      restored = await global.ChatState.restoreSession();
    }
    if (!restored && global.ChatState) {
      await global.ChatState.loadEmptyChips();
    }
  });

  global.ChatInput = { fillAndSend };
})(window);
