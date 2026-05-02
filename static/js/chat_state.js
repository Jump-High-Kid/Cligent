/**
 * chat_state.js — 클라 측 상태 관리 + 메시지 렌더 (v10 plan E1)
 *
 * 책임:
 *   - session_id 보관 (sessionStorage, 탭 단위)
 *   - turn 호출 (POST /api/blog-chat/turn) 후 messages/stage/quota 적용
 *   - 메시지 DOM 렌더 (assistant/user/system + 옵션 칩 + 태극)
 *   - 빈 첫 화면 칩 데이터 로드 (recent + recommend)
 *   - 헤더 stage / quota 갱신
 *
 * 클라가 상태를 "보관"하는 게 아니라 "표시"만 하도록 설계.
 * 진실의 원본은 서버 (blog_chat_sessions). 새로고침 = session_id로 GET 복구.
 */

(function (global) {
  'use strict';

  const SESSION_KEY = 'cligent_blog_chat_session';
  const SERIES_TOPICS_KEY = 'cligent_series_topics';
  const TURN_URL = '/api/blog-chat/turn';
  const SESSION_GET_URL = (sid) => `/api/blog-chat/session/${encodeURIComponent(sid)}`;

  // 도메인 기본값 6종 (v9 명세 — 최근 글이 3개 미만일 때 추천 칩 6개로 노출)
  const DEFAULT_SERIES_TOPICS = [
    '허리디스크', '경항통', '견비통', '추나치료', '사상체질', '침구치료',
  ];

  // ── 상태 ──────────────────────────────────────────────────────
  const state = {
    session_id: null,
    stage: 'topic',
    stage_text: '주제 입력 중',
    quota: {},
    sending: false,
    pendingOptions: [],  // 마지막 assistant 메시지의 옵션 (단축키 1~9용)
    isAdmin: false,      // 비용·디버그 메타 표시 권한 (turn 응답에서 갱신)
  };

  // 본문 streaming 중 갱신 대상 (1D-3 SSE 통합)
  const streamRef = {
    bubble: null,   // 현재 streaming bubble element
    taegeuk: null,  // active 회전 SVG ref
    text: '',       // 누적 텍스트
  };

  // ── DOM 헬퍼 ──────────────────────────────────────────────────
  const $ = (id) => document.getElementById(id);

  function escapeHTML(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  function setStageText(text) {
    state.stage_text = text || '';
    const el = $('stageText');
    if (el) el.textContent = state.stage_text;
  }

  // ── 이미지 생성 취소 버튼 (2026-05-02 강화) ────────────────────
  // 트리거: stage_change → 'image' 즉시 표시. image_session_started frame은 backup.
  // cancel: image_session_id가 있으면 직접 cancel API, 없으면 chat session 기반 pending cancel.
  function showImageCancelBtn() {
    let btn = $('imageCancelBtn');
    if (!btn) {
      btn = document.createElement('button');
      btn.id = 'imageCancelBtn';
      btn.type = 'button';
      btn.style.cssText = [
        'background:#fef2f2',
        'color:#dc2626',
        'border:1px solid #fca5a5',
        'border-radius:8px',
        'padding:0 12px',
        'font-size:12px',
        'font-weight:700',
        'height:30px',
        'flex-shrink:0',
        'cursor:pointer',
        'display:inline-flex',
        'align-items:center',
        'gap:4px',
      ].join(';');
      btn.title = '이미지 생성 취소';
      btn.innerHTML = '<span class="material-symbols-outlined" style="font-size:16px">close</span>중단';
      // 헤더 우측에 추가 (feedbackBtn 앞)
      const fb = $('feedbackBtn');
      const header = document.querySelector('.chat-header');
      if (fb && fb.parentNode) {
        fb.parentNode.insertBefore(btn, fb);
      } else if (header) {
        header.appendChild(btn);
      }
    }
    btn.style.display = 'inline-flex';
    btn.disabled = false;
    btn.innerHTML = '<span class="material-symbols-outlined" style="font-size:16px">close</span>중단';
    btn.onclick = async () => {
      if (!confirm('이미지 생성을 취소하시겠습니까? 진행 중인 장은 폐기됩니다.')) return;
      btn.disabled = true;
      btn.innerHTML = '<span class="material-symbols-outlined" style="font-size:16px">hourglass_empty</span>취소 중';
      try {
        const imgSid = state.image_session_id;
        const chatSid = state.session_id;
        if (imgSid) {
          // 정확한 image_session_id 알고 있으면 직접 취소
          await fetch(`/api/image/session/${encodeURIComponent(imgSid)}/cancel`, { method: 'POST' });
        } else if (chatSid) {
          // image_session 아직 생성 전 → chat session 기반 pending 취소
          await fetch(`/api/blog-chat/${encodeURIComponent(chatSid)}/cancel-image`, { method: 'POST' });
        }
      } catch (_) { /* silent — 서버가 다음 SSE에서 image_cancelled 보낼 것 */ }
    };
  }

  function hideImageCancelBtn() {
    const btn = $('imageCancelBtn');
    if (btn) {
      btn.style.display = 'none';
      btn.disabled = false;
      btn.innerHTML = '<span class="material-symbols-outlined" style="font-size:16px">close</span>중단';
    }
  }

  // stage별 입력창 placeholder (이슈 6, 2026-05-02 EMPHASIS + confirm_image 추가)
  // 흐름: TOPIC → LENGTH → QUESTIONS → SEO → EMPHASIS → CONFIRM_IMAGE → GENERATING → IMAGE → FEEDBACK → DONE
  const PLACEHOLDER_BY_STAGE = {
    topic:         '오늘 쓸 주제를 입력하세요...',
    length:        '번호 (1~4) 또는 직접 글자 수 입력...',
    questions:     '답변을 입력하거나 옵션을 선택하세요...',
    seo:           '쉼표 구분 키워드 또는 [넘김]...',
    emphasis:      '강조하고 싶은 치료법·사례·증상 (선택, [건너뛰기])',
    confirm_image: '번호 (1·2) 또는 옵션 선택',
    generating:    '본문 작성 중...',
    image:         '"전체 만들기" 또는 "이미지 없이 종료"',
    feedback:      '의견을 자유롭게 남겨주세요 (또는 [넘김])',
    done:          '완성됐어요. 새 글 시작 버튼을 눌러주세요.',
  };

  function updatePlaceholder() {
    const input = $('chatInput');
    if (!input) return;
    const ph = PLACEHOLDER_BY_STAGE[state.stage] || PLACEHOLDER_BY_STAGE.topic;
    input.placeholder = ph;
    // DONE에선 입력창 비활성
    input.disabled = (state.stage === 'done');
  }

  function setQuota(q) {
    // 양쪽 형식 모두 허용:
    //   1) flat: {regen_used, regen_limit, edit_used, edit_limit}
    //   2) nested: {regen: {used, limit}, edit: {used, limit}}  ← image_generator.get_quota_status
    let regen = null, edit = null;
    if (q) {
      if (q.regen && typeof q.regen === 'object') regen = q.regen;
      else if (q.regen_used != null && q.regen_limit != null) regen = {used: q.regen_used, limit: q.regen_limit};
      if (q.edit && typeof q.edit === 'object') edit = q.edit;
      else if (q.edit_used != null && q.edit_limit != null) edit = {used: q.edit_used, limit: q.edit_limit};
    }
    state.quota = q || {};
    const area = $('quotaArea');
    if (!area) return;
    if (regen || edit) {
      area.classList.add('show');
      if (regen) $('quotaRegen').textContent = `재생성 ${regen.used}/${regen.limit}`;
      if (edit)  $('quotaEdit').textContent  = `수정 ${edit.used}/${edit.limit}`;
    } else {
      area.classList.remove('show');
    }
  }

  // ── 메시지 렌더 ───────────────────────────────────────────────
  function hideEmptyState() {
    const es = $('emptyState');
    if (es) es.hidden = true;
  }

  function makeBubbleRow(msg) {
    const row = document.createElement('div');
    row.className = `msg-row ${msg.role}`;
    if (msg.role === 'assistant') {
      // 태극 액센트
      const tpl = $('taegeukTemplate');
      if (tpl && tpl.content) {
        const svg = tpl.content.firstElementChild.cloneNode(true);
        // 활성(생성 중) 표식 — meta.active=true면 회전. 1D에서 사용.
        if (msg.meta && msg.meta.active) svg.classList.add('active');
        row.appendChild(svg);
      }
    }
    const bubble = document.createElement('div');
    bubble.className = `bubble ${msg.role}`;
    bubble.setAttribute('role', msg.role === 'system' ? 'note' : 'article');
    bubble.textContent = msg.text || '';

    // 옵션 칩
    if (msg.role === 'assistant' && Array.isArray(msg.options) && msg.options.length) {
      const chips = document.createElement('div');
      chips.className = 'option-chips';
      chips.setAttribute('role', 'group');
      chips.setAttribute('aria-label', '옵션 선택');
      const isNewSession = !!(msg.meta && msg.meta.new_session_action);
      msg.options.forEach((opt, idx) => {
        const btn = document.createElement('button');
        btn.className = 'chip';
        btn.type = 'button';
        // 단축키 라벨은 일반 옵션에만
        const sc = (!isNewSession && idx < 9)
          ? `<span class="chip-shortcut">${idx + 1}</span>` : '';
        btn.innerHTML = `${sc}<span>${escapeHTML(opt.label || opt.id || '')}</span>`;
        btn.dataset.optionId = opt.id || '';
        btn.dataset.optionLabel = opt.label || '';
        btn.addEventListener('click', () => {
          // 새 글 시작 액션 — sessionStorage clear + reload (이슈 9, 11)
          if (isNewSession) {
            try { sessionStorage.removeItem(SESSION_KEY); } catch (_) {}
            window.location.reload();
            return;
          }
          if (global.ChatInput && global.ChatInput.fillAndSend) {
            global.ChatInput.fillAndSend(opt.label || opt.id || '');
          }
        });
        chips.appendChild(btn);
      });
      bubble.appendChild(chips);
    }

    row.appendChild(bubble);
    return row;
  }

  function appendMessage(msg) {
    hideEmptyState();
    const inner = $('messagesInner');
    const m = $('chatMessages');
    // append 전 nearBottom 상태를 캡처 — append 후엔 이미 scrollHeight가 늘어 false가 됨
    const wasNearBottom = !m
      || (m.scrollHeight - m.scrollTop - m.clientHeight < 200);
    const row = makeBubbleRow(msg);
    inner.appendChild(row);
    // 이미지 갤러리 메시지면 카드/액션/카운터 부착
    if (msg && msg.role === 'assistant' && msg.meta && msg.meta.kind === 'image_gallery') {
      const bubble = row.querySelector('.bubble');
      if (bubble) attachImageGallery(bubble, msg);
      if (msg.meta.quota) setQuota(msg.meta.quota);
    }
    // 완료 안내(글+이미지 모두 출력) — 본문 복사·전체 다운로드·발행 확인 3 버튼 부착
    if (msg && msg.role === 'assistant' && msg.meta && msg.meta.kind === 'completion_summary') {
      const bubble = row.querySelector('.bubble');
      if (bubble) attachCompletionActions(bubble, msg);
    }
    // 이미지 자동 시작 카운트다운 — N초 후 server가 지정한 auto_action을 자동 전송.
    // 사용자가 옵션 클릭(또는 입력)하면 sendTurn 시작 시점에 cancelAutoImageStart로 취소.
    if (msg && msg.role === 'assistant' && msg.meta && msg.meta.kind === 'auto_image_countdown') {
      scheduleAutoImageStart(
        msg.meta.countdown_sec || 3,
        msg.meta.auto_action || '전체 만들기',
      );
    }
    if (wasNearBottom && m) {
      m.scrollTop = m.scrollHeight;
    }
    // 마지막 assistant 옵션 보관 (단축키용)
    if (msg.role === 'assistant' && Array.isArray(msg.options)) {
      state.pendingOptions = msg.options.slice(0, 9);
    } else if (msg.role === 'user') {
      state.pendingOptions = [];
    }
  }

  function appendMessages(messages) {
    if (!Array.isArray(messages)) return;
    messages.forEach(appendMessage);
  }

  function scrollToBottom() {
    const m = $('chatMessages');
    if (!m) return;
    // streaming 중에는 임계값을 크게 — 빠른 token 도착 + layout 흔들림 방어.
    // 사용자가 명백히 위로 스크롤(>500px 위)할 때만 자동 스크롤 멈춤.
    const isStreaming = !!streamRef.bubble;
    const threshold = isStreaming ? 500 : 240;
    const nearBottom = m.scrollHeight - m.scrollTop - m.clientHeight < threshold;
    if (nearBottom) m.scrollTop = m.scrollHeight;
  }

  // ── streaming 메시지 (1D-3 SSE) ──────────────────────────────

  function startStreamingMessage(msgObj) {
    hideEmptyState();
    const inner = $('messagesInner');
    if (!inner) return;
    const row = makeBubbleRow(msgObj || { role: 'assistant', text: '', options: [], meta: { active: true } });
    inner.appendChild(row);
    streamRef.bubble = row.querySelector('.bubble');
    streamRef.taegeuk = row.querySelector('.taegeuk');
    streamRef.row = row;
    // progress_only placeholder 표식 — next_message 도착 시 row 통째 제거
    if (msgObj && msgObj.meta && msgObj.meta.progress_only && streamRef.bubble) {
      streamRef.bubble.dataset.progressOnly = '1';
    }
    streamRef.text = (msgObj && msgObj.text) || '';
    if (streamRef.bubble) {
      streamRef.bubble.classList.add('streaming');
      // 진행 텍스트 영역(.stage-progress)을 헤드에 둠 — 단계 텍스트 갱신용
      const progress = document.createElement('span');
      progress.className = 'stage-progress';
      progress.textContent = '';
      streamRef.bubble.appendChild(progress);
      streamRef.progress = progress;
      // 본문 텍스트 영역
      const textNode = document.createElement('span');
      textNode.className = 'stream-text';
      textNode.textContent = streamRef.text;
      streamRef.bubble.appendChild(textNode);
      streamRef.textNode = textNode;
    }
    scrollToBottom();
  }

  function appendStreamToken(text) {
    if (!streamRef.textNode || !text) return;
    streamRef.text += text;
    streamRef.textNode.textContent = streamRef.text;
    scrollToBottom();
  }

  function replaceStreamText(text) {
    if (!streamRef.textNode || text == null) return;
    streamRef.text = String(text);
    streamRef.textNode.textContent = streamRef.text;
    scrollToBottom();
  }

  // 단계 텍스트 — streaming 중인 메시지의 .stage-progress에 표시 (이슈 4)
  function updateStreamStageProgress(text) {
    if (!streamRef.progress || !text) return;
    streamRef.progress.textContent = text;
    scrollToBottom();
  }

  function finalizeStreamingMessage(msgObj) {
    if (streamRef.taegeuk) streamRef.taegeuk.classList.remove('active');
    if (streamRef.bubble) streamRef.bubble.classList.remove('streaming');
    // 진행 텍스트 영역 제거
    if (streamRef.progress) streamRef.progress.remove();
    // msgObj.text가 있으면 최종 텍스트로 교체 + 액션 버튼 부착 (이슈 3)
    if (msgObj && streamRef.bubble) {
      if (msgObj.text != null && streamRef.textNode) {
        streamRef.textNode.textContent = msgObj.text;
      }
      attachBlogActions(streamRef.bubble, msgObj);
    }
    streamRef.bubble = null;
    streamRef.taegeuk = null;
    streamRef.text = '';
    streamRef.textNode = null;
    streamRef.progress = null;
  }

  // ── 이미지 갤러리 (5b/5c) ─────────────────────────────────────
  // meta.kind === 'image_gallery' 메시지의 bubble에 5장 카드 + 액션 + ZIP + 재생성 부착.
  // 갤러리는 closure로 자체 상태(images, msgObj.meta)를 보유 → 재생성·수정 시 in-place 갱신.
  function attachImageGallery(bubbleEl, msgObj) {
    if (!bubbleEl || !msgObj || !msgObj.meta) return;
    const meta = msgObj.meta;
    if (!Array.isArray(meta.images) || !meta.images.length) return;
    const filenameBase = sanitizeFilename(meta.filename_base || 'image');

    const gallery = document.createElement('div');
    gallery.className = 'image-gallery';
    gallery.setAttribute('role', 'group');
    gallery.setAttribute('aria-label', '생성된 이미지 5장');
    bubbleEl.appendChild(gallery);

    function renderCards() {
      gallery.innerHTML = '';
      meta.images.forEach((b64, idx) => {
        gallery.appendChild(makeImageCard(b64, idx, filenameBase, meta, rerenderCard));
      });
    }
    function rerenderCard(idx, newB64) {
      meta.images[idx] = newB64;
      const old = gallery.children[idx];
      const fresh = makeImageCard(newB64, idx, filenameBase, meta, rerenderCard);
      if (old) gallery.replaceChild(fresh, old);
    }
    renderCards();

    // footer — [전체 ZIP]만. 재생성은 카드별 [↺]로 처리.
    const footer = document.createElement('div');
    footer.className = 'image-gallery-footer';

    const zipBtn = document.createElement('button');
    zipBtn.type = 'button';
    zipBtn.className = 'bubble-action-btn primary';
    zipBtn.innerHTML = '<span class="material-symbols-outlined" style="font-size:16px">archive</span>전체 ZIP';
    zipBtn.addEventListener('click', async () => {
      const prev = zipBtn.innerHTML;
      zipBtn.disabled = true;
      zipBtn.textContent = '압축 중...';
      try {
        await downloadZip(meta.images, filenameBase);
        zipBtn.textContent = '저장됨';
      } catch (err) {
        zipBtn.textContent = '실패 — 개별 다운로드 사용';
      } finally {
        setTimeout(() => { zipBtn.innerHTML = prev; zipBtn.disabled = false; }, 1500);
      }
    });
    footer.appendChild(zipBtn);
    bubbleEl.appendChild(footer);
  }

  // 완료 안내 메시지에 3 버튼 부착: 본문 복사 · 이미지 전체 다운로드 · 발행 확인 등록
  function attachCompletionActions(bubbleEl, msg) {
    const meta = msg.meta || {};
    const blogText = meta.blog_text || '';
    const blogHistoryId = meta.blog_history_id || null;
    const filenameBase = meta.filename_base || 'image';

    const actions = document.createElement('div');
    actions.className = 'bubble-actions';
    actions.style.marginTop = '12px';

    // 1) 본문 복사
    const copyBtn = document.createElement('button');
    copyBtn.type = 'button';
    copyBtn.className = 'bubble-action-btn primary';
    copyBtn.innerHTML = '<span class="material-symbols-outlined" style="font-size:16px">content_copy</span>본문 복사';
    copyBtn.addEventListener('click', () => {
      if (typeof copyBlogToClipboard === 'function' && blogText) {
        copyBlogToClipboard(blogText, copyBtn);
      } else {
        flashButton(copyBtn, '본문 없음');
      }
    });
    actions.appendChild(copyBtn);

    // 2) 이미지 전체 다운로드 — 직전 갤러리 메시지의 ZIP 버튼 클릭 위임
    const dlBtn = document.createElement('button');
    dlBtn.type = 'button';
    dlBtn.className = 'bubble-action-btn';
    dlBtn.innerHTML = '<span class="material-symbols-outlined" style="font-size:16px">download</span>이미지 전체 다운로드';
    dlBtn.addEventListener('click', () => {
      // 가장 최근 갤러리의 ZIP 버튼 트리거
      const galleries = document.querySelectorAll('.image-gallery-footer .image-action-btn');
      const target = Array.from(galleries).reverse().find(b => /전체.*ZIP|ZIP/i.test(b.textContent || ''));
      if (target) {
        target.click();
        flashButton(dlBtn, '다운로드 시작');
      } else {
        flashButton(dlBtn, '이미지 없음');
      }
    });
    actions.appendChild(dlBtn);

    // 3) 발행 확인 등록 — 네이버 검색 인덱싱 폴링 시작
    const checkBtn = document.createElement('button');
    checkBtn.type = 'button';
    checkBtn.className = 'bubble-action-btn';
    checkBtn.innerHTML = '<span class="material-symbols-outlined" style="font-size:16px">search</span>발행 확인';
    if (!blogHistoryId) {
      checkBtn.disabled = true;
      checkBtn.title = '블로그 이력 ID가 없어요.';
    } else {
      checkBtn.addEventListener('click', async () => {
        const prev = checkBtn.innerHTML;
        checkBtn.disabled = true;
        checkBtn.textContent = '등록 중...';
        try {
          const res = await fetch(`/api/blog/history/${blogHistoryId}/publish-check`, {
            method: 'POST', credentials: 'include',
          });
          if (!res.ok) {
            throw new Error('publish-check failed');
          }
          flashButton(checkBtn, '등록됨');
          setTimeout(() => { checkBtn.innerHTML = prev; checkBtn.disabled = false; }, 2200);
        } catch (_e) {
          flashButton(checkBtn, '실패');
          setTimeout(() => { checkBtn.innerHTML = prev; checkBtn.disabled = false; }, 2200);
        }
      });
    }
    actions.appendChild(checkBtn);

    bubbleEl.appendChild(actions);
  }

  // 카드 1장 — 이미지 + 번호 + [✎ 수정][⬇ 다운로드]. 재생성은 footer.
  function makeImageCard(b64, idx, filenameBase, meta, rerenderCard) {
    const card = document.createElement('div');
    card.className = 'image-card';
    card.dataset.imageIndex = String(idx);

    const num = document.createElement('span');
    num.className = 'image-card-num';
    num.textContent = String(idx + 1);
    card.appendChild(num);

    const img = document.createElement('img');
    img.alt = `${filenameBase} ${idx + 1}번`;
    img.src = `data:image/png;base64,${b64}`;
    img.loading = 'lazy';
    img.style.cursor = 'zoom-in';
    img.title = '클릭하면 원본 크기로 확대됩니다';
    img.addEventListener('click', () => openImageLightbox(b64, `${filenameBase} ${idx + 1}번`));
    card.appendChild(img);

    const actions = document.createElement('div');
    actions.className = 'image-card-actions';

    const regenBtn = document.createElement('button');
    regenBtn.type = 'button';
    regenBtn.className = 'image-action-btn ghost';
    regenBtn.innerHTML = '<span class="material-symbols-outlined" style="font-size:16px">refresh</span>재생성';
    regenBtn.title = '이 1장만 다시 그리기 (재생성 한도 1회 차감)';
    regenBtn.addEventListener('click', async () => {
      if (!confirm(`${idx + 1}번 이미지를 다시 그립니다. 한도 1회 차감됩니다. 계속할까요?`)) return;
      const prev = regenBtn.innerHTML;
      regenBtn.disabled = true;
      regenBtn.textContent = '생성 중...';
      try {
        // 카드별 [↺]는 그 카드의 모듈 prompt로 재생성 — meta.prompts[idx] 우선,
        // 구버전 호환은 meta.primary_prompt fallback.
        const cardPrompt = (Array.isArray(meta.prompts) && meta.prompts[idx])
          ? meta.prompts[idx]
          : meta.primary_prompt;
        const data = await callRegenerateApi(meta.image_session_id, cardPrompt, 1);
        const newB64 = (data.images && data.images[0]) || null;
        if (!newB64) throw new Error('빈 응답');
        rerenderCard(idx, newB64);
        if (data.quota) { meta.quota = data.quota; setQuota(data.quota); }
      } catch (err) {
        regenBtn.textContent = err.userMessage || '재생성 실패';
        setTimeout(() => { regenBtn.innerHTML = prev; regenBtn.disabled = false; }, 2200);
      }
    });
    actions.appendChild(regenBtn);

    const editBtn = document.createElement('button');
    editBtn.type = 'button';
    editBtn.className = 'image-action-btn ghost';
    editBtn.innerHTML = '<span class="material-symbols-outlined" style="font-size:16px">edit</span>수정';
    actions.appendChild(editBtn);

    const dlBtn = document.createElement('button');
    dlBtn.type = 'button';
    dlBtn.className = 'image-action-btn';
    dlBtn.innerHTML = '<span class="material-symbols-outlined" style="font-size:16px">download</span>다운로드';
    dlBtn.addEventListener('click', () => {
      downloadBase64Png(b64, `${filenameBase}_${String(idx + 1).padStart(2, '0')}.png`);
      flashButton(dlBtn, '저장됨');
    });
    actions.appendChild(dlBtn);

    card.appendChild(actions);

    // 인라인 수정 인풋 (펼침)
    const editPanel = document.createElement('div');
    editPanel.className = 'image-edit-panel';
    editPanel.hidden = true;
    editPanel.innerHTML = `
      <textarea rows="2" placeholder="어떤 부분을 수정할까요? 예: 배경을 흰색으로, 텍스트 라벨 제거"></textarea>
      <div class="image-edit-actions">
        <button type="button" class="image-action-btn ghost" data-act="cancel">취소</button>
        <button type="button" class="image-action-btn" data-act="apply">수정 적용</button>
      </div>
      <p class="image-edit-status" hidden></p>
    `;
    card.appendChild(editPanel);
    const ta = editPanel.querySelector('textarea');
    const statusEl = editPanel.querySelector('.image-edit-status');
    editBtn.addEventListener('click', () => {
      const willOpen = editPanel.hidden;
      editPanel.hidden = !willOpen;
      if (willOpen) ta && ta.focus();
    });
    editPanel.querySelector('[data-act="cancel"]').addEventListener('click', () => {
      editPanel.hidden = true;
    });
    editPanel.querySelector('[data-act="apply"]').addEventListener('click', async () => {
      const prompt = (ta && ta.value || '').trim();
      if (!prompt) { statusEl.hidden = false; statusEl.textContent = '수정 내용을 적어주세요.'; return; }
      statusEl.hidden = false; statusEl.textContent = '수정 중... (~30초)';
      try {
        const data = await callEditApi(meta.image_session_id, b64, prompt);
        const newB64 = (data.images && data.images[0]) || null;
        if (!newB64) throw new Error('빈 응답');
        rerenderCard(idx, newB64);
        if (data.quota) { meta.quota = data.quota; setQuota(data.quota); }
      } catch (err) {
        statusEl.textContent = err.userMessage || '수정 실패';
      }
    });
    return card;
  }

  async function callRegenerateApi(imageSessionId, prompt, n) {
    const payload = { session_id: imageSessionId, prompt: prompt || '' };
    if (n != null) payload.n = n;
    const res = await fetch('/api/image/regenerate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw await imageApiError(res);
    return await res.json();
  }

  async function callEditApi(imageSessionId, b64, prompt) {
    const blob = await base64ToBlob(b64, 'image/png');
    const fd = new FormData();
    fd.append('session_id', imageSessionId);
    fd.append('prompt', prompt);
    fd.append('image', blob, 'source.png');
    const res = await fetch('/api/image/edit', {
      method: 'POST',
      credentials: 'same-origin',
      body: fd,
    });
    if (!res.ok) throw await imageApiError(res);
    return await res.json();
  }

  async function imageApiError(res) {
    let body = null;
    try { body = await res.json(); } catch (_) {}
    const detail = body && (body.detail || body);
    const err = new Error('image api failed');
    if (res.status === 429 && detail && detail.kind === 'quota_exceeded') {
      err.userMessage = detail.message
        || `무료 한도(${detail.limit}회)에 도달했어요.`;
    } else {
      err.userMessage = (detail && detail.message)
        || (typeof detail === 'string' ? detail : '이미지 작업 실패');
    }
    return err;
  }

  async function base64ToBlob(b64, mime) {
    // small enough (b64 < 5MB) → fetch data URL trick
    const resp = await fetch(`data:${mime};base64,${b64}`);
    return await resp.blob();
  }

  function sanitizeFilename(name) {
    // 한글·영문·숫자·_·- 만 허용 (파일시스템 안전), 빈 문자열은 'image'
    const cleaned = String(name || '').replace(/[^\wㄱ-힝-]+/g, '_').replace(/^_+|_+$/g, '');
    return cleaned || 'image';
  }

  function downloadBase64Png(b64, filename) {
    const a = document.createElement('a');
    a.href = `data:image/png;base64,${b64}`;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  // ── 이미지 확대 lightbox (2026-05-01) ─────────────────────
  function openImageLightbox(b64, alt) {
    // 이미 열려 있으면 무시
    if (document.getElementById('img-lightbox')) return;

    const overlay = document.createElement('div');
    overlay.id = 'img-lightbox';
    overlay.style.cssText = (
      'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.85);' +
      'display:flex;align-items:center;justify-content:center;cursor:zoom-out;' +
      'padding:24px;'
    );
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-label', alt || '이미지 확대 보기');

    const img = document.createElement('img');
    img.src = `data:image/png;base64,${b64}`;
    img.alt = alt || '';
    img.style.cssText = (
      'max-width:100%;max-height:100%;object-fit:contain;' +
      'border-radius:8px;box-shadow:0 8px 32px rgba(0,0,0,0.5);'
    );
    overlay.appendChild(img);

    const close = () => {
      overlay.remove();
      document.removeEventListener('keydown', onKey);
    };
    const onKey = (e) => { if (e.key === 'Escape') close(); };
    overlay.addEventListener('click', close);
    document.addEventListener('keydown', onKey);

    document.body.appendChild(overlay);
  }

  async function downloadZip(images, filenameBase) {
    if (typeof JSZip !== 'function') throw new Error('JSZip not loaded');
    const zip = new JSZip();
    images.forEach((b64, idx) => {
      zip.file(`${filenameBase}_${String(idx + 1).padStart(2, '0')}.png`, b64, { base64: true });
    });
    const blob = await zip.generateAsync({ type: 'blob' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${filenameBase}.zip`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1500);
  }

  // 본문 완료 메시지에 [복사][복사(HTML)] 버튼 + 글자수/비용 메타 표시 (이슈 3)
  function attachBlogActions(bubbleEl, msgObj) {
    if (!bubbleEl || !msgObj) return;
    const meta = msgObj.meta || {};
    // streaming 메시지(본문)인지 식별 — char_count가 있으면 본문 완료 메시지
    if (meta.char_count == null) return;
    const blogText = msgObj.text || '';
    if (!blogText) return;

    const actions = document.createElement('div');
    actions.className = 'bubble-actions';

    // [복사] — ClipboardItem (HTML + 텍스트, 네이버 서식 유지)
    const copyBtn = document.createElement('button');
    copyBtn.type = 'button';
    copyBtn.className = 'bubble-action-btn primary';
    copyBtn.innerHTML = '<span class="material-symbols-outlined" style="font-size:16px">content_copy</span>본문 복사';
    copyBtn.addEventListener('click', () => copyBlogToClipboard(blogText, copyBtn));
    actions.appendChild(copyBtn);

    bubbleEl.appendChild(actions);

    const metaEl = document.createElement('div');
    metaEl.className = 'bubble-meta';
    // 비용 표시는 어드민에게만 (2026-05-01)
    const cost = (state.isAdmin && meta.cost_krw)
      ? ` · 비용 ₩${Math.round(meta.cost_krw)}`
      : '';
    metaEl.textContent = `${meta.char_count}자 작성됐어요${cost}`;
    bubbleEl.appendChild(metaEl);
  }

  async function copyBlogToClipboard(text, btn) {
    // HTML(굵기·헤더 보존) + 텍스트 동시 복사 — 네이버 붙여넣기 시 서식 유지
    const html = textToHtml(text);
    try {
      if (navigator.clipboard && window.ClipboardItem) {
        const item = new ClipboardItem({
          'text/html': new Blob([html], { type: 'text/html' }),
          'text/plain': new Blob([text], { type: 'text/plain' }),
        });
        await navigator.clipboard.write([item]);
      } else {
        await navigator.clipboard.writeText(text);
      }
      flashButton(btn, '복사됨');
    } catch (_) {
      // fallback — textarea 폴백
      const ta = document.createElement('textarea');
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand('copy'); flashButton(btn, '복사됨'); }
      catch (_e) { flashButton(btn, '복사 실패'); }
      ta.remove();
    }
  }

  function flashButton(btn, msg) {
    if (!btn) return;
    const prev = btn.innerHTML;
    btn.innerHTML = msg;
    setTimeout(() => { btn.innerHTML = prev; }, 1500);
  }

  function textToHtml(text) {
    // 마크다운 헤더/볼드/가로선 → HTML 변환 (네이버 서식 유지)
    const lines = String(text || '').split('\n');
    const out = [];
    for (const line of lines) {
      let l = line;
      l = l.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
      // **bold**
      l = l.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
      const trimmed = l.trim();
      // 가로선: ---, ***, ___ (3자 이상) — 네이버 hr 변환
      if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
        out.push('<hr>');
      } else if (l.startsWith('### ')) out.push('<h3>' + l.slice(4) + '</h3>');
      else if (l.startsWith('## ')) out.push('<h2>' + l.slice(3) + '</h2>');
      else if (l.startsWith('# ')) out.push('<h1>' + l.slice(2) + '</h1>');
      else if (trimmed === '') out.push('<br>');
      else out.push('<p>' + l + '</p>');
    }
    return out.join('');
  }

  // ── turn 호출 ──────────────────────────────────────────────────

  /**
   * 사용자 입력 또는 빈 입력(첫 진입)으로 1턴 진행.
   * @param {string} userInput
   */
  // 자동 이미지 시작 카운트다운 timer (2026-05-01 추가)
  let _autoImageTimerId = null;
  function scheduleAutoImageStart(sec, action) {
    if (_autoImageTimerId) clearTimeout(_autoImageTimerId);
    const delay = Math.max(1, sec | 0) * 1000;
    const payload = action || '전체 만들기';
    _autoImageTimerId = setTimeout(function fire() {
      _autoImageTimerId = null;
      // SSE가 늦게 끝났을 가능성 — sending 풀릴 때까지 250ms 간격 짧게 polling (최대 5초)
      let waited = 0;
      const tick = () => {
        if (!state.sending) {
          sendTurn(payload);
          return;
        }
        waited += 250;
        if (waited >= 5000) return;  // 5초 넘게 sending이면 포기
        setTimeout(tick, 250);
      };
      tick();
    }, delay);
  }
  function cancelAutoImageStart() {
    if (_autoImageTimerId) {
      clearTimeout(_autoImageTimerId);
      _autoImageTimerId = null;
    }
  }

  async function sendTurn(userInput) {
    if (state.sending) return;
    state.sending = true;
    // 새 입력 시작 — 진행 중이던 자동 이미지 timer는 취소 (사용자가 직접 입력했으므로)
    cancelAutoImageStart();
    setSendButton(false);

    try {
      await global.ChatSSE.postSSE(TURN_URL, {
        session_id: state.session_id || null,
        user_input: userInput || '',
      }, {
        onJson: handleTurnResponse,
        onChunk: handleSSEFrame,
        onError: (err) => {
          finalizeStreamingMessage();
          appendMessage({
            role: 'system',
            text: `오류가 발생했어요. 다시 시도해주세요. (${err.message || '연결 실패'})`,
            options: [], meta: {},
          });
        },
      });
    } finally {
      state.sending = false;
      setSendButton(true);
    }
  }

  function handleTurnResponse(resp) {
    if (!resp) return;
    if (resp.detail && resp.kind === 'quota_exceeded') {
      appendMessage({
        role: 'system',
        text: `이번 베타 한도에 도달했어요. (${resp.detail})`,
        options: [], meta: {},
      });
      return;
    }
    if (resp.session_id) {
      state.session_id = resp.session_id;
      try { sessionStorage.setItem(SESSION_KEY, resp.session_id); } catch (_) {}
    }
    if (resp.stage) state.stage = resp.stage;
    if (resp.stage_text) setStageText(resp.stage_text);
    setQuota(resp.quota || {});
    // 비용 표시 권한 — 어드민(chief_director + ADMIN_CLINIC_ID)만 (2026-05-01)
    if (typeof resp.is_admin === 'boolean') state.isAdmin = resp.is_admin;
    appendMessages(resp.messages || []);
    updatePlaceholder();
  }

  // SSE 프레임 type별 분기 (1D-3 본문 streaming)
  function handleSSEFrame(frame) {
    if (!frame || !frame.type) return;
    switch (frame.type) {
      case 'user_message':
      case 'next_message':
        // 이전에 stage_text 진행용으로 만든 streaming bubble이 있으면 처리
        if (streamRef.bubble && streamRef.bubble.classList.contains('streaming')) {
          // progress_only placeholder는 빈 채로 남으므로 row 통째 제거
          if (streamRef.bubble.dataset && streamRef.bubble.dataset.progressOnly === '1') {
            const row = streamRef.row || streamRef.bubble.closest('.msg-row');
            if (row && row.parentNode) row.parentNode.removeChild(row);
            streamRef.bubble = null;
            streamRef.taegeuk = null;
            streamRef.row = null;
            streamRef.text = '';
            streamRef.textNode = null;
            streamRef.progress = null;
          } else {
            finalizeStreamingMessage();
          }
        }
        if (frame.message) appendMessage(frame.message);
        break;
      case 'message_start':
        startStreamingMessage(frame.message);
        break;
      case 'token':
        if (frame.text) appendStreamToken(frame.text);
        break;
      case 'replace':
        replaceStreamText(frame.text);
        break;
      case 'message_done':
        finalizeStreamingMessage(frame.message);
        // 이미지 갤러리 메시지면 cancel 버튼 자동 숨김 (정상 완료)
        if (frame.message && frame.message.meta && frame.message.meta.kind === 'image_gallery') {
          hideImageCancelBtn();
        }
        break;
      case 'stage_text':
        if (frame.text) {
          setStageText(frame.text);
          // streaming 중이면 메시지 영역의 진행 텍스트도 갱신 (이슈 4 — 사용자 시선)
          updateStreamStageProgress(frame.text);
          // streaming 메시지가 아직 시작되지 않았다면 진행 표시용 placeholder bubble 시작
          if (!streamRef.bubble) {
            startStreamingMessage({
              role: 'assistant', text: '',
              options: [], meta: { active: true, progress_only: true },
            });
            updateStreamStageProgress(frame.text);
          }
        }
        break;
      case 'stage_change':
        if (frame.stage) state.stage = frame.stage;
        if (frame.stage_text) setStageText(frame.stage_text);
        updatePlaceholder();
        // 2026-05-02: image stage 진입 즉시 취소 버튼 노출 (image_session_id 없어도 chat session 기반 pending 취소 가능)
        if (frame.stage === 'image') {
          showImageCancelBtn();
        }
        break;
      case 'image_session_started':
        // backup 트리거 — image_session_id를 state에 저장 (이후 정확한 직접 취소 가능)
        if (frame.image_session_id) {
          state.image_session_id = frame.image_session_id;
        }
        showImageCancelBtn();
        break;
      case 'image_cancelled':
        hideImageCancelBtn();
        finalizeStreamingMessage();
        appendMessage({
          role: 'system',
          text: frame.message || '이미지 생성이 취소됐어요.',
          options: [], meta: {},
        });
        break;
      case 'error':
        hideImageCancelBtn();
        finalizeStreamingMessage();
        appendMessage({
          role: 'system',
          text: `오류: ${frame.message || '본문 생성 실패'}`,
          options: [], meta: {},
        });
        break;
      case 'done':
        // 전체 turn 종료. 이미지 SSE는 quota·image_session_id를 같이 보냄 — 헤더 카운터 갱신
        if (frame.quota) setQuota(frame.quota);
        if (frame.image_session_id) state.imageSessionId = frame.image_session_id;
        break;
      default:
        break;
    }
  }

  // ── 빈 화면 칩 데이터 ──────────────────────────────────────────

  /**
   * GET /api/blog/stats → recent_keywords / total
   * total < 3 이면 추천 6개, total >= 3 이면 추천 3개 + 최근 3개 노출.
   */
  async function loadEmptyChips() {
    let stats = { total: 0, recent_keywords: [] };
    try {
      const res = await fetch('/api/blog/stats', { credentials: 'same-origin' });
      if (res.status === 401) { window.location.href = '/login'; return; }
      if (res.ok) stats = await res.json();
    } catch (_) { /* 네트워크 오류는 도메인 기본값으로 fallback */ }

    const recent = Array.isArray(stats.recent_keywords) ? stats.recent_keywords.slice(0, 3) : [];
    const total = stats.total || 0;

    // 최근 칩
    if (recent.length > 0) {
      const sec = $('recentSection');
      const row = $('recentChips');
      sec.hidden = false;
      recent.forEach((kw) => row.appendChild(makeChip(kw, false)));
    }

    // 추천 칩 — localStorage 우선, 없으면 도메인 기본값
    let recommend = [];
    try {
      const raw = localStorage.getItem(SERIES_TOPICS_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed)) recommend = parsed.filter((s) => typeof s === 'string');
      }
    } catch (_) {}
    if (recommend.length === 0) recommend = DEFAULT_SERIES_TOPICS.slice();
    // 최근 카운트 < 3이면 6개 노출, 아니면 3개
    const count = total < 3 ? 6 : 3;
    recommend = recommend.slice(0, count);
    const recRow = $('recommendChips');
    recommend.forEach((kw) => recRow.appendChild(makeChip(kw, true)));
  }

  function makeChip(label, isRecommended) {
    const btn = document.createElement('button');
    btn.className = 'chip' + (isRecommended ? ' recommended' : '');
    btn.type = 'button';
    btn.textContent = label;
    btn.addEventListener('click', () => {
      if (global.ChatInput && global.ChatInput.fillAndSend) {
        global.ChatInput.fillAndSend(label);
      }
    });
    return btn;
  }

  // ── 세션 복구 ─────────────────────────────────────────────────

  async function restoreSession() {
    let sid = null;
    try { sid = sessionStorage.getItem(SESSION_KEY); } catch (_) {}
    if (!sid) return false;
    try {
      const res = await fetch(SESSION_GET_URL(sid), { credentials: 'same-origin' });
      if (res.status === 401) { window.location.href = '/login'; return false; }
      if (!res.ok) {
        // 세션 만료/없음 → 신규로 시작
        try { sessionStorage.removeItem(SESSION_KEY); } catch (_) {}
        return false;
      }
      const data = await res.json();
      state.session_id = data.session_id;
      state.stage = data.stage;
      setStageText(data.stage_text || '');
      setQuota(data.quota || {});
      appendMessages(data.messages || []);
      updatePlaceholder();
      return true;
    } catch (_) {
      return false;
    }
  }

  function setSendButton(enabled) {
    const btn = $('sendBtn');
    if (btn) btn.disabled = !enabled;
  }

  // ── 외부 입력 (chat_input.js에서 호출) ──────────────────────────

  function getSessionId() { return state.session_id; }
  function isSending() { return state.sending; }
  function getPendingOptions() { return state.pendingOptions.slice(); }

  global.ChatState = {
    sendTurn,
    restoreSession,
    loadEmptyChips,
    setSendButton,
    getSessionId,
    isSending,
    getPendingOptions,
  };
})(window);
