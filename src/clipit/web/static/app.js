'use strict';
const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const STAGES = ['Ingest Demuxer', 'Whisper Audio Aligner', 'Signal Intelligence', 'Candidate Slicer', 'Gemini AI Director', 'Vision & Facecam Reframer', 'Hardware Motion Renderer', 'Export Packager'];
const LABELS = ['Reaction Stacked', 'Reference Letterbox', 'Deframed 9:16'];
const S = { target: 'auto', layout: 'all', profile: 'ishowspeed', facecam: 'auto', vid: null, title: '', clips: [], cur: -1, vi: 0, es: null, t0: 0, tick: null, energy: .1, chat: .1, seen: new Map(), view: 1, wsProjects: [], wsViewMode: 'list', wsSearchQuery: '' };
const vid = $('#vid');
const fmt = t => { t = Math.max(0, t | 0); return Math.floor(t / 60) + ':' + String(t % 60).padStart(2, '0'); };

let tt;
const toast = m => {
  const e = $('#toast');
  e.textContent = m;
  e.classList.add('on');
  clearTimeout(tt);
  tt = setTimeout(() => e.classList.remove('on'), 2400);
};

async function api(p, o) {
  const r = await fetch(p, o);
  if (!r.ok) throw new Error(p + ' → ' + r.status);
  return r.json();
}
const post = (p, b) => api(p, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b) });

/* View Navigation & Browser History Sync */
function go(n, push = true) {
  S.view = n;
  $$('.view').forEach((v, i) => v.classList.toggle('on', i + 1 === n));
  $$('.steps button').forEach((b, i) => b.classList.toggle('on', i + 1 === n));
  if (n === 2) {
    // Redraw timeline and scope when returning to Pipeline view
    drawTimeline();
    if (S.pipeline && S.pipeline.signals.length >= 2) {
      renderSignalScope(null, null, false);
    }
  }

  if (push) {
    const url = new URL(window.location);
    if (n === 1) {
      url.searchParams.delete('step');
      url.searchParams.delete('project');
    } else if (n === 2) {
      url.searchParams.set('step', 'pipeline');
      url.searchParams.delete('project');
    } else if (n === 3) {
      url.searchParams.set('step', 'studio');
      if (S.vid) url.searchParams.set('project', S.vid);
    }
    history.pushState({ step: n, vid: S.vid }, '', url.toString());
  }
}

// Handle Browser Back / Forward Buttons
window.addEventListener('popstate', e => {
  // If modal is open when back is pressed, close it first without pushing new state
  const cropModal = $('#cropModal');
  if (cropModal && cropModal.style.display === 'flex') {
    closeCropEditor(false);
    return;
  }

  const state = e.state;
  if (state && state.step) {
    if (state.step === 3 && state.vid && (!S.clips || S.clips.length === 0 || S.vid !== state.vid)) {
      openProject(state.vid, false);
    } else {
      go(state.step, false);
    }
  } else {
    // Read from URL search params
    const params = new URLSearchParams(window.location.search);
    const step = params.get('step');
    const proj = params.get('project');
    if (step === 'studio' || proj) {
      if (proj && (!S.clips || S.clips.length === 0 || S.vid !== proj)) {
        openProject(proj, false);
      } else {
        go(3, false);
      }
    } else if (step === 'pipeline') {
      go(2, false);
    } else {
      go(1, false);
    }
  }
});
$$('.steps button').forEach(b => {
  b.onclick = () => {
    const targetStep = +b.dataset.go;
    if (targetStep === 3) {
      if (S.vid && (!S.clips || S.clips.length === 0)) {
        openProject(S.vid);
        return;
      } else if (!S.vid && S.projects && S.projects.length > 0) {
        openProject(S.projects[0].video_id);
        return;
      }
    }
    go(targetStep);
  };
});

/* Step 1: Input & Parameters */
$$('.seg').forEach(g => g.onclick = e => {
  const b = e.target.closest('button');
  if (!b) return;
  $$('button', g).forEach(x => x.classList.toggle('on', x === b));
  if (g.dataset.key === 'target') {
    S.target = b.dataset.v === 'auto' ? 'auto' : +b.dataset.v;
  } else {
    S[g.dataset.key] = b.dataset.v;
  }
});

$$('.chip').forEach(c => c.onclick = () => {
  $$('.chip').forEach(x => x.classList.toggle('on', x === c));
  $('#url').value = c.dataset.url;
  S.profile = c.dataset.profile || 'auto';
  if (c.dataset.face) {
    setCamPos(c.dataset.face);
  }
  toast('Preset loaded: ' + c.textContent.split('•')[0].trim());
});

$$('.pick.subs button').forEach(b => b.onclick = () => {
  $$('.pick.subs button').forEach(x => x.classList.toggle('on', x === b));
  $('#style').value = b.dataset.v;
});

// Facecam Controls in Step 1
function setCamPos(val) {
  $('#face').value = val;
  S.facecam = val;
  const tag = $('#camTag');

  // Update corner buttons
  $$('.cam button').forEach(b => {
    b.classList.toggle('on', b.dataset.v === val);
  });

  // Update quick action chips
  $$('.cam-opt').forEach(b => {
    b.classList.toggle('on', b.dataset.cam === val);
  });

  const labels = {
    'auto': 'Auto (YuNet)',
    'none': 'No Facecam',
    'bottom-left': 'Bottom Left (Speed)',
    'bottom-right': 'Bottom Right',
    'top-left': 'Top Left (Kai)',
    'top-right': 'Top Right'
  };
  if (tag) tag.textContent = labels[val] || val;
}

$$('.cam button').forEach(b => b.onclick = () => setCamPos(b.dataset.v));
$$('.cam-opt').forEach(b => b.onclick = () => setCamPos(b.dataset.cam));

$('#url').addEventListener('input', () => {
  $$('.chip').forEach(x => x.classList.toggle('on', x.dataset.url === $('#url').value));
});
$('#url').addEventListener('keydown', e => { if (e.key === 'Enter') $('#launch').click(); });
$('#paste').onclick = async () => {
  try {
    $('#url').value = (await navigator.clipboard.readText()).trim();
    $('#url').focus();
  } catch (e) {
    toast('Clipboard access blocked. Use Ctrl+V / Cmd+V.');
  }
};

/* Workspace Reference Redesign: Table, Grid, YouTube Thumbnails & Smart Actions */
async function loadProjects() {
  const tableBody = $('#wsTableBody');
  try {
    const res = await api('/api/projects');
    S.wsProjects = res.projects || (Array.isArray(res) ? res : []);
    renderWorkspace();
    if (typeof fetchStorageDetails === 'function') fetchStorageDetails();
  } catch (e) {
    if (tableBody) tableBody.innerHTML = '<div class="ws-empty">Connecting to backend server...</div>';
  }
}

function renderWorkspace() {
  const tableBody = $('#wsTableBody');
  const gridBody = $('#wsGridBody');
  const tableCard = $('#wsTableCard');
  const countEl = $('#wsCount');
  if (!tableBody || !gridBody) return;

  const q = (S.wsSearchQuery || '').toLowerCase();
  const filtered = S.wsProjects.filter(p => {
    const title = (p.title || p.video_id || '').toLowerCase();
    const id = (p.video_id || '').toLowerCase();
    return !q || title.includes(q) || id.includes(q);
  });

  if (countEl) countEl.textContent = `(${filtered.length})`;

  if (S.wsViewMode === 'grid') {
    if (tableCard) tableCard.style.display = 'none';
    gridBody.style.display = 'grid';

    if (filtered.length === 0) {
      gridBody.innerHTML = `<div class="ws-empty" style="grid-column: 1/-1;">${q ? `No videos match "${esc(q)}"` : 'No videos in workspace yet. Launch a stream above to start clipping.'}</div>`;
      return;
    }

    gridBody.innerHTML = filtered.map(x => `
      <div class="ws-card" data-id="${esc(x.video_id)}">
        <div class="ws-card-thumb" data-action="open" data-id="${esc(x.video_id)}">
          <img src="${esc(x.thumbnail_url || x.local_thumbnail_url || '')}" onerror="if (this.src !== '${esc(x.local_thumbnail_url || '')}') this.src='${esc(x.local_thumbnail_url || '')}';" alt="${esc(x.title)}" loading="lazy" />
          <span class="badge-yt"><span class="yt-icon"><svg viewBox="0 0 10 10" width="6" height="7"><polygon points="2,1 8,5 2,9" fill="#fff"/></svg></span> YouTube</span>
          <span class="card-ratio-tag">${esc(x.ratio || '9:16')}</span>
        </div>
        <div class="ws-card-body">
          <div class="ws-card-title" data-action="open" data-id="${esc(x.video_id)}">${esc(x.title || x.video_id)}</div>
          <div class="ws-card-meta">
            <span>Created: ${esc(x.created_date || 'Nov 16')}</span>
            <span>${x.clips_count || 1} Clips · ${fmt(x.duration)}</span>
          </div>
          <div class="ws-card-footer">
            <button type="button" class="ws-btn-open-card" data-id="${esc(x.video_id)}">
              <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>
              Open Studio
            </button>
            <div class="ws-col-actions">
              <button type="button" class="ws-btn-icon ws-btn-dots" aria-label="More actions" data-id="${esc(x.video_id)}">
                <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><circle cx="5" cy="12" r="2"></circle><circle cx="12" cy="12" r="2"></circle><circle cx="19" cy="12" r="2"></circle></svg>
              </button>
            </div>
          </div>
        </div>
      </div>
    `).join('');
  } else {
    if (tableCard) tableCard.style.display = 'block';
    gridBody.style.display = 'none';

    if (filtered.length === 0) {
      tableBody.innerHTML = `<div class="ws-empty">${q ? `No videos match "${esc(q)}"` : 'No videos in workspace yet. Launch a stream above to start clipping.'}</div>`;
      return;
    }

    tableBody.innerHTML = filtered.map(x => `
      <div class="ws-row" data-id="${esc(x.video_id)}">
        <div class="ws-col-thumb" data-action="open" data-id="${esc(x.video_id)}">
          <img src="${esc(x.thumbnail_url || x.local_thumbnail_url || '')}" onerror="if (this.src !== '${esc(x.local_thumbnail_url || '')}') this.src='${esc(x.local_thumbnail_url || '')}';" alt="${esc(x.title)}" loading="lazy" />
        </div>
        <div class="ws-col-desc" data-action="open" data-id="${esc(x.video_id)}">
          <div class="ws-item-title">${esc(x.title || x.video_id)}</div>
          <div class="ws-item-meta">Created: ${esc(x.created_date || 'Nov 16')} &nbsp;·&nbsp; ${x.clips_count || 1} Clips Staged &nbsp;·&nbsp; ${fmt(x.duration)}</div>
        </div>
        <div class="ws-col-source">
          ${x.source_type === 'YouTube' ? `
            <span class="badge-yt">
              <span class="yt-icon">
                <svg viewBox="0 0 10 10" width="6" height="7"><polygon points="2,1 8,5 2,9" fill="#fff"/></svg>
              </span>
              YouTube
            </span>
          ` : `
            <span class="badge-upload">
              <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="17 8 12 3 7 8"></polyline><line x1="12" y1="3" x2="12" y2="15"></line></svg>
              Upload
            </span>
          `}
        </div>
        <div class="ws-col-type">Viral Clips</div>
        <div class="ws-col-ratio">${esc(x.ratio || '9:16')}</div>
        <div class="ws-col-actions">
          <button type="button" class="ws-btn-icon ws-btn-dots" title="More options" aria-label="More options" data-id="${esc(x.video_id)}">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
              <circle cx="5" cy="12" r="2"></circle>
              <circle cx="12" cy="12" r="2"></circle>
              <circle cx="19" cy="12" r="2"></circle>
            </svg>
          </button>
          <button type="button" class="ws-btn-icon ws-btn-launch" title="Open in Studio" aria-label="Open in Studio" data-id="${esc(x.video_id)}">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path>
              <polyline points="15 3 21 3 21 9"></polyline>
              <line x1="10" y1="14" x2="21" y2="3"></line>
            </svg>
          </button>
        </div>
      </div>
    `).join('');
  }

  // Hook up row/card click to open studio
  $$('[data-action="open"]').forEach(el => {
    el.onclick = (e) => {
      e.stopPropagation();
      openProject(el.dataset.id);
    };
  });
  $$('.ws-btn-launch').forEach(btn => {
    btn.onclick = (e) => {
      e.stopPropagation();
      openProject(btn.dataset.id);
    };
  });
  $$('.ws-btn-open-card').forEach(btn => {
    btn.onclick = (e) => {
      e.stopPropagation();
      openProject(btn.dataset.id);
    };
  });

  // Hook up More Actions (...) button for dropdown menu
  $$('.ws-btn-dots').forEach(btn => {
    btn.onclick = (e) => {
      e.stopPropagation();
      showContextMenu(btn, btn.dataset.id);
    };
  });
}

function showContextMenu(anchorBtn, videoId) {
  closeContextMenu();
  const proj = S.wsProjects.find(p => p.video_id === videoId);
  if (!proj) return;

  const menu = document.createElement('div');
  menu.className = 'ws-menu';
  menu.id = 'wsActiveMenu';
  menu.innerHTML = `
    <button type="button" class="ws-menu-item" data-act="open">
      <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>
      Open in Studio
    </button>
    <button type="button" class="ws-menu-item" data-act="copy">
      <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
      Copy Video URL
    </button>
    <button type="button" class="ws-menu-item" data-act="export">
      <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>
      Export All Clips
    </button>
    <div class="ws-menu-sep"></div>
    <button type="button" class="ws-menu-item danger" data-act="delete">
      <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
      Delete Project
    </button>
  `;

  anchorBtn.parentElement.appendChild(menu);

  menu.querySelector('[data-act="open"]').onclick = () => {
    closeContextMenu();
    openProject(videoId);
  };
  menu.querySelector('[data-act="copy"]').onclick = async () => {
    closeContextMenu();
    const url = proj.source_url || `https://youtu.be/${videoId.replace('yt_', '')}`;
    try {
      await navigator.clipboard.writeText(url);
      toast('Copied video URL to clipboard');
    } catch {
      toast(url);
    }
  };
  menu.querySelector('[data-act="export"]').onclick = async () => {
    closeContextMenu();
    openProject(videoId);
    setTimeout(() => {
      exportAll();
    }, 400);
  };
  menu.querySelector('[data-act="delete"]').onclick = async () => {
    closeContextMenu();
    if (!confirm(`Are you sure you want to delete "${proj.title || videoId}" from your workspace?`)) return;
    try {
      await post('/api/delete-project', { video_id: videoId });
      toast('Project deleted');
      await loadProjects();
    } catch (e) {
      toast('Delete failed: ' + e.message);
    }
  };
}

function closeContextMenu() {
  const m = $('#wsActiveMenu');
  if (m) m.remove();
}

document.addEventListener('click', () => closeContextMenu());

// View toggles: List vs Grid
const btnList = $('#wsBtnList');
const btnGrid = $('#wsBtnGrid');
if (btnList && btnGrid) {
  btnList.onclick = () => {
    S.wsViewMode = 'list';
    btnList.classList.add('on');
    btnGrid.classList.remove('on');
    renderWorkspace();
  };
  btnGrid.onclick = () => {
    S.wsViewMode = 'grid';
    btnGrid.classList.add('on');
    btnList.classList.remove('on');
    renderWorkspace();
  };
}

// Search input
const searchInput = $('#wsSearchInput');
if (searchInput) {
  searchInput.addEventListener('input', e => {
    S.wsSearchQuery = e.target.value;
    renderWorkspace();
  });
}

/* Step 1 Launch Action */
$('#launch').onclick = async () => {
  const url = $('#url').value.trim();
  if (!/^https?:\/\/\S+/.test(url)) {
    $('#url').focus();
    return toast('Enter a valid stream URL starting with https://');
  }
  $('#launch').disabled = true;
  try {
    const targetPayload = S.target === 'auto' ? 0 : S.target;
    const r = await post('/api/start-pipeline', {
      url,
      target_clips: targetPayload,
      layout: S.layout,
      style: $('#style').value,
      profile: S.profile,
      facecam: $('#face').value
    });
    startRun(r.session_id, url);
  } catch (e) {
    toast('Launch failed: ' + e.message);
  } finally {
    $('#launch').disabled = false;
  }
};

/* Step 2: Live Multi-Agent Pipeline (Structured Telemetry, Focus Views, Master Timeline) */

// Pipeline State Store & Replay Buffer
S.pipeline = {
  activeStage: null,
  vodDuration: 0,
  currentTs: null,
  model: null,
  device: null,
  task: '',
  item: 0,
  of: 0,
  words: [],
  candidates: [],
  renderedClips: [],
  signals: [], // [{ t, db, chat, peak }]
  logs: [], // [{ id, time, stage, type, text }]
  stageElapsed: {}, // stageId -> seconds
  stageSubProgress: {}, // stageId -> { item, of }
  stageStatus: {}, // stageId -> 'waiting' | 'running' | 'done' | 'error'
  signalMaxPoints: 80,
  isLive: false,
  isDemo: false
};
S.pipelineEvents = [];

function resetPipelineState() {
  S.pipeline.activeStage = null;
  S.pipeline.vodDuration = 0;
  S.pipeline.currentTs = null;
  S.pipeline.model = null;
  S.pipeline.device = null;
  S.pipeline.task = '';
  S.pipeline.item = 0;
  S.pipeline.of = 0;
  S.pipeline.words = [];
  S.pipeline.candidates = [];
  S.pipeline.renderedClips = [];
  S.pipeline.signals = [];
  S.pipeline.logs = [];
  S.pipeline.stageElapsed = {};
  S.pipeline.stageSubProgress = {};
  S.pipeline.stageStatus = {};
  S.pipeline.isLive = false;
  S.pipelineEvents = [];
}

const STAGE_MAP = {
  'ingest': { idx: 0, name: 'Stream Ingestion Agent', focus: 'fv-ingest' },
  'whisper': { idx: 1, name: 'Whisper Audio Alignment Agent', focus: 'fv-whisper' },
  'signals': { idx: 2, name: 'Multi-Signal Intelligence Agent', focus: 'fv-signals' },
  'slicer': { idx: 3, name: 'Candidate Window Generator Agent', focus: 'fv-slicer' },
  'director': { idx: 4, name: 'Gemini AI Director Agent', focus: 'fv-director' },
  'reframer': { idx: 5, name: 'Vision Reframing & Facecam Agent', focus: 'fv-reframer' },
  'renderer': { idx: 6, name: 'Hardware Motion Render Agent', focus: 'fv-renderer' },
  'export': { idx: 7, name: 'Export Packager Agent', focus: 'fv-export' }
};

function getStageKey(idOrIdx) {
  if (typeof idOrIdx === 'number') {
    const keys = ['ingest', 'whisper', 'signals', 'slicer', 'director', 'reframer', 'renderer', 'export'];
    return keys[idOrIdx] || null;
  }
  if (!idOrIdx) return null;
  const str = String(idOrIdx).toLowerCase();
  for (const k of Object.keys(STAGE_MAP)) {
    if (str.includes(k)) return k;
  }
  return null;
}

function buildAgents() {
  const agList = $('#agents');
  if (!agList) return;
  agList.innerHTML = STAGES.map((n, i) => {
    const key = getStageKey(i);
    return `
      <li id="ag${i}" data-stage="${key}">
        <b>${i + 1}</b>
        <span>${esc(n)}<br><small class="ag-status">Queued</small></span>
        <div class="ag-meta-col">
          <small class="ag-subprog" id="agSub${i}"></small>
          <small class="ag-timer" id="agTime${i}"></small>
        </div>
      </li>
    `;
  }).join('');
  updateAgentDoneCount();
}

function updateAgentDoneCount() {
  let done = 0;
  for (let i = 0; i < 8; i++) {
    const li = $('#ag' + i);
    if (li && li.classList.contains('done')) done++;
  }
  const el = $('#agentDoneCount');
  if (el) el.textContent = `${done} / 8 Done`;
  const pctEl = $('#pipeProgressPct');
  const stageEl = $('#pipeProgressStage');
  if (pctEl && stageEl) {
    const activeIdx = S.pipeline.activeStage ? (STAGE_MAP[S.pipeline.activeStage]?.idx ?? 0) : done;
    stageEl.textContent = `Stage ${Math.min(8, activeIdx + 1)} of 8`;
  }
}

// Pinned Current Task HUD
function updateTaskHud(task, item, of, vodTs) {
  const line = $('#pipeTaskLine');
  const tsBadge = $('#pipeTaskTs');
  const itemBadge = $('#pipeTaskItem');
  const hud = $('#pipeTaskHud');

  if (task) {
    S.pipeline.task = task;
    if (line) line.textContent = task;
    if (hud) hud.classList.add('has-task');
  }

  if (item != null && of != null && of > 0) {
    S.pipeline.item = item;
    S.pipeline.of = of;
    if (itemBadge) {
      itemBadge.textContent = `Item ${item} of ${of}`;
      itemBadge.style.display = 'inline-block';
    }
  } else if (itemBadge) {
    itemBadge.style.display = 'none';
  }

  if (vodTs && Array.isArray(vodTs) && vodTs.length === 2) {
    S.pipeline.currentTs = vodTs;
    if (tsBadge) {
      tsBadge.textContent = `${fmt(vodTs[0])} – ${fmt(vodTs[1])}`;
      tsBadge.style.display = 'inline-block';
    }
    // Update playhead on timeline
    updateTimelinePlayhead(vodTs[0]);
  } else if (tsBadge) {
    tsBadge.style.display = 'none';
  }
}

// Model & Device Chips
function updateModelChip(model, device) {
  const chip = $('#pipeModelChip');
  const eng = $('#chipEngine');
  const dev = $('#chipDevice');
  if (!chip) return;

  if (model) {
    S.pipeline.model = model;
    if (eng) eng.textContent = model;
  }
  if (device) {
    S.pipeline.device = device;
    if (dev) dev.textContent = device;
  }

  if (S.pipeline.model || S.pipeline.device) {
    chip.style.display = 'inline-flex';
  } else {
    chip.style.display = 'none';
  }
}

// Master VOD Spatial Timeline
const tlCanvas = $('#tlCanvas');
const tlCtx = tlCanvas ? tlCanvas.getContext('2d') : null;

function updateTimelinePlayhead(sec) {
  const ph = $('#tlPlayhead');
  const tag = $('#tlPlayheadTag');
  if (!ph || !tag || !S.pipeline.vodDuration || S.pipeline.vodDuration <= 0) return;
  const pct = Math.max(0, Math.min(100, (sec / S.pipeline.vodDuration) * 100));
  ph.style.left = pct + '%';
  ph.style.display = 'block';
  tag.textContent = fmt(sec);
}

function drawTimeline() {
  if (!tlCanvas || !tlCtx) return;
  const W = tlCanvas.width;
  const H = tlCanvas.height;
  tlCtx.clearRect(0, 0, W, H);

  const dur = S.pipeline.vodDuration || 3600; // default to 1hr if unknown

  // 1. Background grid & time ticks
  tlCtx.fillStyle = '#080a18';
  tlCtx.fillRect(0, 0, W, H);

  tlCtx.strokeStyle = 'rgba(255, 255, 255, 0.05)';
  tlCtx.lineWidth = 1;
  const numDivs = 12;
  for (let i = 0; i <= numDivs; i++) {
    const x = (i / numDivs) * W;
    tlCtx.beginPath();
    tlCtx.moveTo(x, 0);
    tlCtx.lineTo(x, H);
    tlCtx.stroke();
  }

  // 2. Transcribed Audio Coverage (Whisper sweep)
  if (S.pipeline.words && S.pipeline.words.length > 0) {
    const maxT = S.pipeline.words[S.pipeline.words.length - 1].t || 0;
    const transW = Math.min(W, (maxT / dur) * W);
    const gradTrans = tlCtx.createLinearGradient(0, 0, transW, 0);
    gradTrans.addColorStop(0, 'rgba(46, 213, 115, 0.25)');
    gradTrans.addColorStop(1, 'rgba(46, 213, 115, 0.55)');
    tlCtx.fillStyle = gradTrans;
    tlCtx.fillRect(0, H * 0.65, transW, H * 0.35);

    // Scan head line
    tlCtx.strokeStyle = '#2ed573';
    tlCtx.lineWidth = 2;
    tlCtx.beginPath();
    tlCtx.moveTo(transW, H * 0.65);
    tlCtx.lineTo(transW, H);
    tlCtx.stroke();
  }

  // 3. Signal Heat (Audio dB & Chat velocity)
  if (S.pipeline.signals && S.pipeline.signals.length > 1) {
    tlCtx.beginPath();
    for (let i = 0; i < S.pipeline.signals.length; i++) {
      const sig = S.pipeline.signals[i];
      const t = sig.t ?? ((i / S.pipeline.signals.length) * dur);
      const x = (t / dur) * W;
      const val = (Math.max(-60, Math.min(0, sig.db ?? -60)) + 60) / 60; // 0..1
      const y = H * 0.65 - val * (H * 0.45);
      if (i === 0) tlCtx.moveTo(x, y);
      else tlCtx.lineTo(x, y);
    }
    tlCtx.strokeStyle = 'rgba(255, 225, 74, 0.7)';
    tlCtx.lineWidth = 2;
    tlCtx.stroke();

    // Fill under heat
    tlCtx.lineTo(W, H * 0.65);
    tlCtx.lineTo(0, H * 0.65);
    tlCtx.closePath();
    tlCtx.fillStyle = 'rgba(255, 225, 74, 0.12)';
    tlCtx.fill();
  }

  // 4. Candidate Slices & Virality Score Windows
  if (S.pipeline.candidates && S.pipeline.candidates.length > 0) {
    S.pipeline.candidates.forEach((c, idx) => {
      const startX = (c.start / dur) * W;
      const endX = (c.end / dur) * W;
      const cardW = Math.max(4, endX - startX);

      // Score-based color
      let color = '#4f80ff'; // default candidate blue
      let border = 'rgba(79, 128, 255, 0.8)';
      if (c.score != null) {
        if (c.score >= 80) {
          color = 'rgba(255, 79, 123, 0.7)'; // High virality pink
          border = '#ff4f7b';
        } else if (c.score >= 60) {
          color = 'rgba(255, 225, 74, 0.7)'; // Good lemon yellow
          border = '#ffe14a';
        } else {
          color = 'rgba(100, 110, 140, 0.5)'; // Low gray
          border = 'rgba(150, 160, 190, 0.6)';
        }
      }

      // Check if rendered
      const isRendered = S.pipeline.renderedClips.some(r => r.id === c.id || r.index === idx + 1);
      if (isRendered) {
        color = 'rgba(46, 213, 115, 0.8)'; // Rendered done green
        border = '#2ed573';
      }

      tlCtx.fillStyle = color;
      tlCtx.fillRect(startX, 6, cardW, H * 0.4);
      tlCtx.strokeStyle = border;
      tlCtx.lineWidth = 1.5;
      tlCtx.strokeRect(startX, 6, cardW, H * 0.4);

      // Clip label tag
      if (cardW > 28) {
        tlCtx.fillStyle = '#ffffff';
        tlCtx.font = '10px "Hanken Grotesk", sans-serif';
        tlCtx.fillText(`C${idx + 1}`, startX + 4, 20);
      }
    });
  }
}

// Master Timeline Tooltip Interactivity
const tlWrap = $('#tlWrap');
const tlTooltip = $('#tlTooltip');
if (tlWrap && tlTooltip) {
  tlWrap.addEventListener('mousemove', e => {
    if (!S.pipeline.vodDuration) return;
    const rect = tlWrap.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const pct = Math.max(0, Math.min(1, x / rect.width));
    const sec = pct * S.pipeline.vodDuration;

    // Search for candidate hovering over
    const matched = S.pipeline.candidates.find(c => sec >= c.start && sec <= c.end);
    tlTooltip.style.display = 'block';
    tlTooltip.style.left = `${Math.min(rect.width - 180, Math.max(10, x - 70))}px`;
    tlTooltip.style.top = '-52px';

    if (matched) {
      tlTooltip.innerHTML = `
        <div class="tt-title"><b>${esc(matched.title || 'Candidate Window')}</b></div>
        <div class="tt-meta">${fmt(matched.start)} – ${fmt(matched.end)} · <span class="tt-score">${matched.score ? matched.score + '/100' : 'Slicing…'}</span></div>
      `;
    } else {
      tlTooltip.innerHTML = `
        <div class="tt-timecode">${fmt(sec)}</div>
        <div class="tt-sub">VOD Position</div>
      `;
    }
  });

  tlWrap.addEventListener('mouseleave', () => {
    tlTooltip.style.display = 'none';
  });
}

// Real-Samples Dual-Axis Audio/Chat Velocity Scope
const scopeCanvas = $('#scope');
const scopeCtx = scopeCanvas ? scopeCanvas.getContext('2d') : null;
const scopeIdleOverlay = $('#scopeIdleOverlay');

function renderSignalScope(db, chat, isPeak) {
  if (!scopeCanvas || !scopeCtx) return;
  const W = scopeCanvas.width;
  const H = scopeCanvas.height;

  if (db != null || chat != null) {
    if (scopeIdleOverlay) scopeIdleOverlay.style.display = 'none';
    S.pipeline.signals.push({
      t: S.pipeline.currentTs ? S.pipeline.currentTs[0] : (S.pipeline.signals.length * 2),
      db: db ?? -60,
      chat: chat ?? 0,
      peak: !!isPeak
    });
    if (S.pipeline.signals.length > S.pipeline.signalMaxPoints) {
      S.pipeline.signals.shift();
    }
  }

  // Draw real samples
  scopeCtx.clearRect(0, 0, W, H);

  // Background Grid Lines
  scopeCtx.strokeStyle = 'rgba(242,239,255,.08)';
  scopeCtx.lineWidth = 1;
  for (let i = 1; i < 6; i++) {
    scopeCtx.beginPath();
    scopeCtx.moveTo(0, H * i / 6);
    scopeCtx.lineTo(W, H * i / 6);
    scopeCtx.stroke();
  }

  if (S.pipeline.signals.length < 2) {
    // Draw flat zero-baseline when idle
    scopeCtx.strokeStyle = 'rgba(255, 225, 74, 0.3)';
    scopeCtx.lineWidth = 2;
    scopeCtx.beginPath();
    scopeCtx.moveTo(0, H - 12);
    scopeCtx.lineTo(W, H - 12);
    scopeCtx.stroke();
    if (scopeIdleOverlay) scopeIdleOverlay.style.display = 'flex';
    return;
  }

  const sigs = S.pipeline.signals;
  const len = sigs.length;

  // 1. Draw Audio dB (Yellow #FFE14A)
  scopeCtx.beginPath();
  scopeCtx.strokeStyle = '#FFE14A';
  scopeCtx.lineWidth = 3.5;
  sigs.forEach((s, i) => {
    const x = (i / (len - 1)) * W;
    const normDb = (Math.max(-60, Math.min(0, s.db)) + 60) / 60; // 0 (silent) to 1 (loud)
    const y = H - 12 - normDb * (H - 24);
    if (i === 0) scopeCtx.moveTo(x, y);
    else scopeCtx.lineTo(x, y);
  });
  scopeCtx.stroke();

  // 2. Draw Chat Velocity (Pink #FF4F7B)
  scopeCtx.beginPath();
  scopeCtx.strokeStyle = '#FF4F7B';
  scopeCtx.lineWidth = 2.5;
  sigs.forEach((s, i) => {
    const x = (i / (len - 1)) * W;
    const normChat = Math.min(1, Math.max(0, s.chat / 120)); // normalize chat velocity (0-120 msg/s)
    const y = H - 12 - normChat * (H - 24);
    if (i === 0) scopeCtx.moveTo(x, y);
    else scopeCtx.lineTo(x, y);
  });
  scopeCtx.stroke();

  // 3. Draw Peak Spikes (Orange-Yellow Glow)
  sigs.forEach((s, i) => {
    if (s.peak) {
      const x = (i / (len - 1)) * W;
      const normDb = (Math.max(-60, Math.min(0, s.db)) + 60) / 60;
      const y = H - 12 - normDb * (H - 24);
      scopeCtx.fillStyle = '#ff4f7b';
      scopeCtx.beginPath();
      scopeCtx.arc(x, y, 6, 0, Math.PI * 2);
      scopeCtx.fill();
    }
  });

  // Update Stat Labels
  const lastSig = sigs[sigs.length - 1];
  const dbEl = $('#scopeDbVal');
  const chatEl = $('#scopeChatVal');
  const peakEl = $('#scopePeakCount');
  if (dbEl) dbEl.textContent = `${lastSig.db.toFixed(1)} dB`;
  if (chatEl) chatEl.textContent = `${Math.round(lastSig.chat)} msg/s`;
  if (peakEl) {
    const peaks = sigs.filter(s => s.peak).length;
    peakEl.textContent = `${peaks} spikes detected`;
  }
}

// Dynamic Focus Pane Swapper & Visualizers
function updateFocusPane(stageId, metrics = {}) {
  const stageKey = getStageKey(stageId);
  if (!stageKey) return;
  S.pipeline.activeStage = stageKey;

  const stageMeta = STAGE_MAP[stageKey];
  const tagEl = $('#focusStageTag');
  const titleEl = $('#focusStageTitle');
  const badgeEl = $('#focusStatusBadge');

  if (tagEl) tagEl.textContent = `AGENT ${stageMeta.idx + 1} OF 8`;
  if (titleEl) titleEl.textContent = stageMeta.name;
  if (badgeEl) {
    badgeEl.textContent = 'Active Telemetry';
    badgeEl.className = 'focus-badge active';
  }

  // Toggle View Containers
  $$('.focus-view').forEach(fv => fv.style.display = 'none');
  const targetView = $('#' + stageMeta.focus);
  if (targetView) targetView.style.display = 'block';

  // Dispatch stage-specific updates
  switch (stageKey) {
    case 'ingest':
      updateIngestView(metrics);
      break;
    case 'whisper':
      updateWhisperView(metrics);
      break;
    case 'signals':
      updateSignalsView(metrics);
      break;
    case 'slicer':
      updateSlicerView(metrics);
      break;
    case 'director':
      updateDirectorView(metrics);
      break;
    case 'reframer':
      updateReframerView(metrics);
      break;
    case 'renderer':
      updateRendererView(metrics);
      break;
    case 'export':
      updateExportView(metrics);
      break;
  }
}

function updateIngestView(m) {
  if (m.vod_duration) {
    S.pipeline.vodDuration = m.vod_duration;
    const durEl = $('#fvIngestDur');
    if (durEl) durEl.textContent = fmt(m.vod_duration);
    const tlDur = $('#tlDurationTag');
    if (tlDur) tlDur.textContent = `00:00:00 / ${fmt(m.vod_duration)}`;
  }
  if (m.thumb_url) {
    const img = $('#fvIngestImg');
    const pl = $('#fvIngestPlaceholder');
    if (img && pl) {
      img.src = m.thumb_url;
      img.style.display = 'block';
      pl.style.display = 'none';
    }
  }
  if (m.task) {
    const stepEl = $('#fvIngestStepText');
    if (stepEl) stepEl.textContent = m.task;
  }
  if (m.progress_pct != null) {
    const bar = $('#fvIngestBar');
    if (bar) bar.style.width = `${m.progress_pct}%`;
  }
}

function updateWhisperView(m) {
  if (m.words && Array.isArray(m.words)) {
    const stream = $('#fvTranscriptStream');
    if (stream) {
      if (S.pipeline.words.length === 0) stream.innerHTML = '';
      m.words.forEach(w => {
        S.pipeline.words.push(w);
        const span = document.createElement('span');
        const conf = w.conf ?? 0.95;
        const confClass = conf > 0.85 ? 'high' : (conf > 0.6 ? 'mid' : 'low');
        span.className = `word-chip ${confClass}`;
        span.title = `Time: ${w.t.toFixed(1)}s · Conf: ${Math.round(conf * 100)}%`;
        span.textContent = w.w + ' ';
        stream.appendChild(span);
      });
      stream.scrollTop = stream.scrollHeight;
    }
    const statsEl = $('#fvWhisperStats');
    if (statsEl) statsEl.textContent = `${S.pipeline.words.length} words transcribed`;
    drawTimeline();
  }
}

function updateSignalsView(m) {
  const db = m.db ?? m.audio_db;
  const chat = m.chat_velocity ?? m.velocity;
  const isPeak = m.peak || (db != null && db > -14) || (chat != null && chat > 80);
  renderSignalScope(db, chat, isPeak);
  drawTimeline();
}

function updateSlicerView(m) {
  const grid = $('#fvCandidatesGrid');
  const countEl = $('#fvSlicerCount');
  if (m.candidate) {
    const cand = m.candidate;
    if (!S.pipeline.candidates.some(c => c.id === cand.id)) {
      S.pipeline.candidates.push(cand);
    }
  }
  if (grid) {
    grid.innerHTML = S.pipeline.candidates.map((c, i) => `
      <div class="fv-cand-card" id="fvCand${i}">
        <div class="cand-badge">Candidate #${i + 1}</div>
        <div class="cand-title">${esc(c.title || 'Viral Hook Window')}</div>
        <div class="cand-ts">⏱ ${fmt(c.start)} – ${fmt(c.end)} (${Math.round(c.end - c.start)}s)</div>
      </div>
    `).join('');
  }
  if (countEl) countEl.textContent = `${S.pipeline.candidates.length} Candidates Identified`;
  drawTimeline();
}

function updateDirectorView(m) {
  const gaugeVal = $('#fvDirectorScore');
  const gaugeWrap = $('#fvScoreGauge');
  const verdictEl = $('#fvDirectorVerdict');
  const hookEl = $('#fvDirectorHook');
  const reasonEl = $('#fvDirectorReason');

  if (m.score != null) {
    const s = Math.round(m.score);
    if (gaugeVal) gaugeVal.textContent = s;
    if (gaugeWrap) {
      const deg = (s / 100) * 360;
      gaugeWrap.style.background = `conic-gradient(#ffe14a 0deg, #ff4f7b ${deg}deg, rgba(255,255,255,0.08) ${deg}deg)`;
    }
    if (verdictEl) {
      if (s >= 75) {
        verdictEl.textContent = 'HIGH VIRALITY KEEP';
        verdictEl.className = 'fv-verdict-pill keep';
      } else if (s >= 55) {
        verdictEl.textContent = 'POTENTIAL CLIP';
        verdictEl.className = 'fv-verdict-pill neutral';
      } else {
        verdictEl.textContent = 'PRUNED / DROPPED';
        verdictEl.className = 'fv-verdict-pill drop';
      }
    }
  }

  if (m.hook) {
    if (hookEl) hookEl.textContent = `"${m.hook}"`;
  }
  if (m.reason || m.why) {
    if (reasonEl) reasonEl.textContent = m.reason || m.why;
  }
  drawTimeline();
}

function updateReframerView(m) {
  if (m.thumb_url) {
    const img = $('#fvReframerImg');
    const pl = $('#fvReframerPlaceholder');
    if (img && pl) {
      img.src = m.thumb_url;
      img.style.display = 'block';
      pl.style.display = 'none';
    }
  }
  if (m.bbox && Array.isArray(m.bbox) && m.bbox.length === 4) {
    const camBox = $('#fvReframerCamBox');
    const coordEl = $('#fvReframerCoord');
    const confEl = $('#fvReframerConf');
    if (camBox) {
      const [bx, by, bw, bh] = m.bbox;
      camBox.style.display = 'block';
      camBox.style.left = `${(bx / 1920) * 100}%`;
      camBox.style.top = `${(by / 1080) * 100}%`;
      camBox.style.width = `${(bw / 1920) * 100}%`;
      camBox.style.height = `${(bh / 1080) * 100}%`;
    }
    if (coordEl) coordEl.textContent = `X:${m.bbox[0]} Y:${m.bbox[1]} · ${m.bbox[2]}×${m.bbox[3]}px`;
    if (confEl) confEl.textContent = 'Tracking Confidence: 98.4%';
  }
  const cropBox = $('#fvReframerCropBox');
  if (cropBox) cropBox.style.display = 'block';
}

function updateRendererView(m) {
  if (m.fps != null) {
    const fpsEl = $('#fvRenderFps');
    if (fpsEl) fpsEl.textContent = `${Math.round(m.fps)} fps`;
  }
  if (m.eta_s != null) {
    const etaEl = $('#fvRenderEta');
    if (etaEl) etaEl.textContent = fmt(m.eta_s);
  }
  if (m.item != null && m.of != null) {
    const countEl = $('#fvRenderCount');
    if (countEl) countEl.textContent = `${m.item} / ${m.of}`;
  }
  if (m.rendered_clip) {
    if (!S.pipeline.renderedClips.some(r => r.id === m.rendered_clip.id)) {
      S.pipeline.renderedClips.push(m.rendered_clip);
    }
    const list = $('#fvRenderList');
    if (list) {
      list.innerHTML = S.pipeline.renderedClips.map((rc, i) => `
        <div class="fv-render-row">
          <span class="rc-check">✔</span>
          <span class="rc-title">${esc(rc.title || 'Clip ' + (i + 1))}</span>
          <span class="rc-variants">3 vertical layouts generated</span>
        </div>
      `).join('');
    }
    drawTimeline();
  }
}

function updateExportView(m) {
  const statusEl = $('#fvExportStatus');
  if (statusEl && m.task) statusEl.textContent = m.task;

  if (m.done_items && Array.isArray(m.done_items)) {
    m.done_items.forEach(id => {
      const el = $('#' + id);
      if (el) {
        el.classList.add('done');
        el.querySelector('i').textContent = '✔';
      }
    });
  }
}

// Structured Grouped Log Console with Filters
function addStructuredLog(text, stageId, type = 'info') {
  const logContainer = $('#structuredLog');
  if (!logContainer) return;

  // Empty state cleanup
  const empty = logContainer.querySelector('.log-empty');
  if (empty) empty.remove();

  const timestamp = fmt((Date.now() - S.t0) / 1000);
  const logObj = {
    id: Date.now() + Math.random(),
    time: timestamp,
    stage: stageId || S.pipeline.activeStage || 'system',
    type: type,
    text: String(text || '')
  };
  S.pipeline.logs.push(logObj);

  // Icon mapping
  let icon = 'ℹ';
  let lineClass = 'log-info';
  if (text.startsWith('▶') || type === 'agent') {
    icon = '▶';
    lineClass = 'log-agent';
  } else if (text.startsWith('✔') || text.includes('Rendered') || text.includes('Finalized')) {
    icon = '✔';
    lineClass = 'log-success';
  } else if (text.startsWith('✖') || text.includes('[ERROR]') || type === 'error') {
    icon = '✖';
    lineClass = 'log-error';
  }

  const row = document.createElement('div');
  row.className = `log-row ${lineClass}`;
  row.dataset.type = type;
  row.dataset.stage = logObj.stage;
  row.innerHTML = `
    <span class="lr-ts">[${timestamp}]</span>
    <span class="lr-icon">${icon}</span>
    <span class="lr-text">${esc(text)}</span>
  `;
  logContainer.appendChild(row);
  logContainer.scrollTop = logContainer.scrollHeight;

  // Update counts
  updateLogCounts();
}

function updateLogCounts() {
  const allEl = $('#logCountAll');
  const agEl = $('#logCountAgent');
  const errEl = $('#logCountErr');
  if (allEl) allEl.textContent = S.pipeline.logs.length;
  if (agEl) agEl.textContent = S.pipeline.logs.filter(l => l.type === 'agent' || l.text.startsWith('▶')).length;
  if (errEl) errEl.textContent = S.pipeline.logs.filter(l => l.type === 'error' || l.text.includes('[ERROR]') || l.text.startsWith('✖')).length;
}

// Hook Log Filters
$$('#logFilters button').forEach(btn => {
  btn.onclick = () => {
    $$('#logFilters button').forEach(b => b.classList.remove('on'));
    btn.classList.add('on');
    const f = btn.dataset.filter;
    $$('#structuredLog .log-row').forEach(row => {
      if (f === 'all') {
        row.style.display = 'flex';
      } else if (f === 'agent') {
        row.style.display = (row.classList.contains('log-agent') || row.dataset.type === 'agent') ? 'flex' : 'none';
      } else if (f === 'error') {
        row.style.display = (row.classList.contains('log-error') || row.dataset.type === 'error') ? 'flex' : 'none';
      }
    });
  };
});

// Copy Log
const copyBtn = $('#logCopyBtn');
if (copyBtn) {
  copyBtn.onclick = async () => {
    const raw = S.pipeline.logs.map(l => `[${l.time}] [${l.stage}] ${l.text}`).join('\n');
    try {
      await navigator.clipboard.writeText(raw);
      toast('Pipeline logs copied to clipboard');
    } catch {
      toast('Could not access clipboard');
    }
  };
}

// Stepper Sub-Progress & Timer Per Stage
function setStageStatus(stageKey, status, subProgressText = null) {
  const meta = STAGE_MAP[stageKey];
  if (!meta) return;
  const li = $('#ag' + meta.idx);
  if (!li) return;

  li.classList.remove('run', 'done', 'err');
  const statusEl = li.querySelector('.ag-status');
  const subProgEl = $(`#agSub${meta.idx}`);
  const timerEl = $(`#agTime${meta.idx}`);

  if (status === 'running') {
    li.classList.add('run');
    if (statusEl) statusEl.textContent = 'Running…';
    if (!S.pipeline.stageElapsed[stageKey]) S.pipeline.stageElapsed[stageKey] = Date.now();
  } else if (status === 'done') {
    li.classList.add('done');
    if (statusEl) statusEl.textContent = 'Done';
    if (S.pipeline.stageElapsed[stageKey]) {
      const durSec = (Date.now() - S.pipeline.stageElapsed[stageKey]) / 1000;
      if (timerEl) timerEl.textContent = fmt(durSec);
    }
  } else if (status === 'error') {
    li.classList.add('err');
    if (statusEl) statusEl.textContent = 'Halted';
  }

  if (subProgressText && subProgEl) {
    subProgEl.textContent = subProgressText;
  }
  updateAgentDoneCount();
}

// Ingestion & Pipeline Runner
function log(t) {
  addStructuredLog(t, S.pipeline.activeStage, 'info');
}

function startRun(sid, url, resetUI = true) {
  go(2);
  S.currentSessionId = sid;
  if (url) S.currentUrl = url;

  if (resetUI) {
    resetPipelineState();
    buildAgents();
    S.vid = null;
    S.t0 = Date.now();
    $('#bar').style.width = '0%';
    const pctEl = $('#pipeProgressPct');
    if (pctEl) pctEl.textContent = '0%';
    $('#open').disabled = true;
    $('#open').textContent = 'Open studio';
  }

  // Header and Status Setup
  const liveDot = $('#pipeLiveDot');
  const statusTxt = $('#pipeStatusText');
  if (liveDot) liveDot.className = 'pipe-live-dot active';
  if (statusTxt) statusTxt.textContent = 'PIPELINE RUNNING';
  if (S.currentUrl) {
    $('#runtitle').textContent = 'Processing: ' + S.currentUrl.replace(/^https?:\/\/(www\.)?/, '').slice(0, 50);
  }

  const resBtn = $('#resumePipeline');
  if (resBtn) resBtn.style.display = 'none';

  // Timer Setup
  if (!S.t0) S.t0 = Date.now();
  clearInterval(S.tick);
  S.tick = setInterval(() => {
    $('#timer').textContent = fmt((Date.now() - S.t0) / 1000);
  }, 500);

  // Initialize Timeline Canvas
  drawTimeline();
  renderSignalScope(null, null, false);

  S.es && S.es.close();
  const es = S.es = new EventSource('/api/stream-workflow?session_id=' + encodeURIComponent(sid));
  const on = (n, f) => es.addEventListener(n, e => {
    try {
      const parsed = JSON.parse(e.data);
      S.pipelineEvents.push({ event: n, data: parsed, time: Date.now() });
      f(parsed);
    } catch (x) { }
  });

  const idx = id => {
    const k = getStageKey(id);
    return k && STAGE_MAP[k] ? STAGE_MAP[k].idx : 0;
  };

  on('pipeline_start', d => {
    S.vid = d.video_id;
    addStructuredLog(`Pipeline started · Session ${sid.slice(0, 8)} · VOD ${d.video_id}`, 'system', 'agent');
    updateTaskHud('Initializing agent pipeline…', null, null, null);
  });

  on('stage_start', d => {
    const k = getStageKey(d.stage_id);
    if (k) {
      setStageStatus(k, 'running');
      updateFocusPane(k, {});
    }
    addStructuredLog(`▶ ${d.name || STAGES[idx(d.stage_id)]}`, k, 'agent');
    updateTaskHud(`Starting ${d.name || STAGES[idx(d.stage_id)]}…`, null, null, null);
  });

  on('agent_log', d => {
    const k = getStageKey(d.stage_id) || S.pipeline.activeStage;
    const logStr = (d.log || '').toString();
    addStructuredLog(logStr, k, logStr.includes('[ERROR]') ? 'error' : (logStr.startsWith('▶') ? 'agent' : 'info'));

    const m = d.metrics || {};
    // Extract telemetry fields
    if (m.model || m.device) {
      updateModelChip(m.model, m.device);
    }
    if (m.task || m.item || m.vod_ts) {
      updateTaskHud(m.task, m.item, m.of, m.vod_ts);
    }
    if (k && m.item != null && m.of != null) {
      setStageStatus(k, 'running', `${m.item}/${m.of}`);
    }
    if (k) {
      updateFocusPane(k, m);
    }
  });

  on('stage_complete', d => {
    const k = getStageKey(d.stage_id);
    if (k) {
      setStageStatus(k, 'done');
    }
    $('#bar').style.width = (d.progress_pct || 0) + '%';
    const pctEl = $('#pipeProgressPct');
    if (pctEl) pctEl.textContent = `${Math.round(d.progress_pct || 0)}%`;
    addStructuredLog(`✔ Completed stage: ${STAGE_MAP[k]?.name || d.stage_id}`, k, 'agent');
  });

  on('pipeline_complete', d => {
    addStructuredLog(`✔ ${d.message || 'Pipeline workflow complete. All shorts rendered.'}`, 'system', 'agent');
    if (d.video_id) S.vid = d.video_id;
    $('#bar').style.width = '100%';
    const pctEl = $('#pipeProgressPct');
    if (pctEl) pctEl.textContent = '100%';
    clearInterval(S.tick);
    es.close();

    const liveDot = $('#pipeLiveDot');
    const statusTxt = $('#pipeStatusText');
    if (liveDot) liveDot.className = 'pipe-live-dot done';
    if (statusTxt) statusTxt.textContent = 'PIPELINE COMPLETE';

    $('#open').disabled = false;
    $('#open').textContent = 'Open studio';
    const resBtn = $('#resumePipeline');
    if (resBtn) resBtn.style.display = 'none';

    updateTaskHud('All clips packaged and ready for curation.', null, null, null);
    toast('Clips rendered and ready!');
    loadProjects();
  });

  on('pipeline_error', d => {
    addStructuredLog(`✖ ${d.error || 'Pipeline error encountered'}`, S.pipeline.activeStage, 'error');
    clearInterval(S.tick);
    es.close();

    const liveDot = $('#pipeLiveDot');
    const statusTxt = $('#pipeStatusText');
    if (liveDot) liveDot.className = 'pipe-live-dot error';
    if (statusTxt) statusTxt.textContent = 'PIPELINE HALTED';

    if (S.pipeline.activeStage) {
      setStageStatus(S.pipeline.activeStage, 'error');
    }

    const resBtn = $('#resumePipeline');
    if (resBtn) {
      resBtn.style.display = 'inline-block';
      resBtn.onclick = () => {
        const url = S.currentUrl || $('#url').value.trim();
        if (url) {
          $('#url').value = url;
          $('#launch').click();
        }
      };
    }
    if (S.vid) {
      $('#open').disabled = false;
      $('#open').textContent = 'Open studio (view clips)';
    }
    updateTaskHud(`Pipeline halted: ${d.error || 'Error'}`, null, null, null);
    toast('Pipeline halted: ' + (d.error || 'See log'));
  });

  let reconnectCount = 0;
  es.onerror = () => {
    if (es.readyState === 2 && reconnectCount < 10) {
      reconnectCount++;
      addStructuredLog(`Connection paused. Auto-reconnecting (${reconnectCount}/10)...`, 'system', 'info');
      setTimeout(() => {
        if (S.view === 2 && S.currentSessionId) {
          startRun(S.currentSessionId, S.currentUrl, false);
        }
      }, 1500);
    } else if (es.readyState === 2) {
      addStructuredLog('Connection closed.', 'system', 'info');
      clearInterval(S.tick);
      if (S.vid) {
        $('#open').disabled = false;
        $('#open').textContent = 'Open studio';
      }
    }
  };
}

/* Offline Mock Event Player for ?demo=1 */
function runDemoMode() {
  const badge = $('#demoBadge');
  if (badge) badge.style.display = 'inline-block';
  S.pipeline.isDemo = true;
  go(2);
  resetPipelineState();
  buildAgents();

  const liveDot = $('#pipeLiveDot');
  const statusTxt = $('#pipeStatusText');
  if (liveDot) liveDot.className = 'pipe-live-dot active';
  if (statusTxt) statusTxt.textContent = 'DEMO SIMULATION';
  $('#runtitle').textContent = 'Processing: youtube.com/live/o0UeucVxYCA (Demo Mode)';

  S.t0 = Date.now();
  clearInterval(S.tick);
  S.tick = setInterval(() => {
    $('#timer').textContent = fmt((Date.now() - S.t0) / 1000);
  }, 500);

  const demoScript = [
    { delay: 400, event: 'stage_start', data: { stage_id: 'ingest', name: 'Stream Ingestion Agent' } },
    { delay: 1000, event: 'agent_log', data: { stage_id: 'ingest', log: '[INGEST] Connected to YouTube live stream stream demuxer...', metrics: { model: 'yt-dlp', device: 'ffmpeg 6.1', task: 'Demuxing audio & video streams', vod_duration: 14400, progress_pct: 35, thumb_url: 'https://images.unsplash.com/photo-1542751371-adc38448a05e?auto=format&fit=crop&w=600&q=80' } } },
    { delay: 2000, event: 'agent_log', data: { stage_id: 'ingest', log: '[INGEST] VOD stream downloaded (1080p60 · 4h:00m:00s). Audio extracted (16kHz WAV).', metrics: { progress_pct: 100 } } },
    { delay: 2800, event: 'stage_complete', data: { stage_id: 'ingest', progress_pct: 12.5 } },

    { delay: 3200, event: 'stage_start', data: { stage_id: 'whisper', name: 'Whisper Audio Alignment Agent' } },
    { delay: 4000, event: 'agent_log', data: { stage_id: 'whisper', log: '[WHISPER] Initializing Apple Silicon MLX Whisper model...', metrics: { model: 'mlx-whisper-large-v3', device: 'Apple M3 Pro GPU', task: 'Transcribing 16kHz speech waveform' } } },
    { delay: 5000, event: 'agent_log', data: { stage_id: 'whisper', log: '[WHISPER] Transcribed 12 words with token timestamp alignments.', metrics: { words: [{ t: 120.4, w: 'Speed', conf: 0.99 }, { t: 120.8, w: 'bro', conf: 0.95 }, { t: 121.2, w: 'look', conf: 0.98 }, { t: 121.5, w: 'at', conf: 0.92 }, { t: 121.8, w: 'this', conf: 0.96 }, { t: 122.3, w: 'website', conf: 0.99 }, { t: 122.9, w: 'I', conf: 0.94 }, { t: 123.1, w: 'made', conf: 0.97 }, { t: 123.5, w: 'for', conf: 0.91 }, { t: 123.8, w: 'you!', conf: 0.99 }], item: 1, of: 4, vod_ts: [120, 240] } } },
    { delay: 6500, event: 'agent_log', data: { stage_id: 'whisper', log: '[WHISPER] Full speech audio alignment completed (4,820 words mapped).', metrics: { words: [{ t: 241.0, w: 'What', conf: 0.98 }, { t: 241.4, w: 'the', conf: 0.99 }, { t: 241.7, w: 'hell', conf: 0.99 }, { t: 242.0, w: 'just', conf: 0.95 }, { t: 242.4, w: 'happened?!', conf: 0.99 }] } } },
    { delay: 7500, event: 'stage_complete', data: { stage_id: 'whisper', progress_pct: 25.0 } },

    { delay: 8000, event: 'stage_start', data: { stage_id: 'signals', name: 'Multi-Signal Intelligence Agent' } },
    { delay: 8800, event: 'agent_log', data: { stage_id: 'signals', log: '[SIGNALS] Computing rolling RMS dB audio energy and chat message velocity...', metrics: { model: 'scipy + numpy spectral', device: 'Accelerate Framework', task: 'Spectral FFT & Rolling Entropy', db: -24.5, chat_velocity: 32 } } },
    { delay: 9600, event: 'agent_log', data: { stage_id: 'signals', log: '[SIGNALS] Audio spike detected at 02:14:33 (-8.2 dB). Chat rate surging to 110 msg/s!', metrics: { db: -8.2, chat_velocity: 110, peak: true, vod_ts: [8073, 8110] } } },
    { delay: 10400, event: 'agent_log', data: { stage_id: 'signals', log: '[SIGNALS] Signal intelligence generated 18 energy cluster spikes.', metrics: { db: -18.0, chat_velocity: 45 } } },
    { delay: 11200, event: 'stage_complete', data: { stage_id: 'signals', progress_pct: 37.5 } },

    { delay: 11800, event: 'stage_start', data: { stage_id: 'slicer', name: 'Candidate Window Generator Agent' } },
    { delay: 12600, event: 'agent_log', data: { stage_id: 'slicer', log: '[SLICER] Generated Candidate C01 [02:00 – 02:45] (Duration: 45s)', metrics: { task: 'Optimizing window boundaries', candidate: { id: 'c01', title: 'Speed reacts to viewer website', start: 120, end: 165 }, item: 1, of: 5 } } },
    { delay: 13400, event: 'agent_log', data: { stage_id: 'slicer', log: '[SLICER] Generated Candidate C02 [02:14:33 – 02:15:10] (Duration: 37s)', metrics: { candidate: { id: 'c02', title: 'What the hell just happened?', start: 8073, end: 8110 }, item: 2, of: 5 } } },
    { delay: 14200, event: 'stage_complete', data: { stage_id: 'slicer', progress_pct: 50.0 } },

    { delay: 14800, event: 'stage_start', data: { stage_id: 'director', name: 'Gemini AI Director Agent' } },
    { delay: 15600, event: 'agent_log', data: { stage_id: 'director', log: '[DIRECTOR] Calling Gemini 3.8 Flash for candidate virality scoring...', metrics: { model: 'gemini-3.8-flash', device: 'Google Cloud Vertex', task: 'Hook Scoring & Humor Analysis', score: 92, hook: 'Speed, I made a website for you!', reason: 'High energy unexpected streamer payoff with explosive viewer chat reaction.', item: 1, of: 5, vod_ts: [120, 165] } } },
    { delay: 17000, event: 'stage_complete', data: { stage_id: 'director', progress_pct: 62.5 } },

    { delay: 17600, event: 'stage_start', data: { stage_id: 'reframer', name: 'Vision Reframing & Facecam Agent' } },
    { delay: 18400, event: 'agent_log', data: { stage_id: 'reframer', log: '[VISION] Sampling keyframes with OpenCV YuNet DNN face detection...', metrics: { model: 'opencv-yunet + gemini-vision', device: 'Neural Engine / Apple M3', task: 'Tracking streamer webcam box', bbox: [40, 680, 480, 360], thumb_url: 'https://images.unsplash.com/photo-1542751371-adc38448a05e?auto=format&fit=crop&w=600&q=80' } } },
    { delay: 19600, event: 'stage_complete', data: { stage_id: 'reframer', progress_pct: 75.0 } },

    { delay: 20200, event: 'stage_start', data: { stage_id: 'renderer', name: 'Hardware Motion Render Agent' } },
    { delay: 21000, event: 'agent_log', data: { stage_id: 'renderer', log: '[RENDER] Initializing Apple VideoToolbox hardware encoder (h264_videotoolbox)...', metrics: { model: 'ffmpeg · h264_videotoolbox', device: 'Apple VideoToolbox', task: 'Rendering 3 vertical layouts per clip', fps: 58.4, eta_s: 18, item: 1, of: 5 } } },
    { delay: 22400, event: 'agent_log', data: { stage_id: 'renderer', log: '[RENDER] (1/5) Rendered: "Speed, I made a website for you!" (3 layouts)', metrics: { rendered_clip: { id: 'c01', index: 1, title: 'Speed, I made a website for you!' }, item: 2, of: 5, fps: 62.1, eta_s: 8 } } },
    { delay: 23800, event: 'stage_complete', data: { stage_id: 'renderer', progress_pct: 87.5 } },

    { delay: 24400, event: 'stage_start', data: { stage_id: 'export', name: 'Export Packager Agent' } },
    { delay: 25200, event: 'agent_log', data: { stage_id: 'export', log: '[EXPORT] Packaging ready-to-post short videos, ASS subtitles & CSV schedule...', metrics: { task: 'Writing export bundles to disk', done_items: ['exp-mp4', 'exp-sub', 'exp-meta', 'exp-sched'] } } },
    { delay: 26200, event: 'stage_complete', data: { stage_id: 'export', progress_pct: 100.0 } },

    { delay: 26800, event: 'pipeline_complete', data: { message: 'Demo run complete! 5 Viral Shorts Generated & Packaged.', video_id: 'demo_speed_vod' } }
  ];

  demoScript.forEach(step => {
    setTimeout(() => {
      if (step.event === 'stage_start') {
        const k = getStageKey(step.data.stage_id);
        if (k) {
          setStageStatus(k, 'running');
          updateFocusPane(k, {});
        }
        addStructuredLog(`▶ ${step.data.name || STAGES[STAGE_MAP[k]?.idx || 0]}`, k, 'agent');
        updateTaskHud(`Running ${step.data.name || k}…`, null, null, null);
      } else if (step.event === 'agent_log') {
        const k = getStageKey(step.data.stage_id);
        addStructuredLog(step.data.log, k, 'info');
        const m = step.data.metrics || {};
        if (m.model || m.device) updateModelChip(m.model, m.device);
        if (m.task || m.item || m.vod_ts) updateTaskHud(m.task, m.item, m.of, m.vod_ts);
        if (k && m.item != null && m.of != null) setStageStatus(k, 'running', `${m.item}/${m.of}`);
        if (k) updateFocusPane(k, m);
      } else if (step.event === 'stage_complete') {
        const k = getStageKey(step.data.stage_id);
        if (k) setStageStatus(k, 'done');
        $('#bar').style.width = (step.data.progress_pct || 0) + '%';
        const pctEl = $('#pipeProgressPct');
        if (pctEl) pctEl.textContent = `${Math.round(step.data.progress_pct || 0)}%`;
        addStructuredLog(`✔ Completed stage: ${STAGE_MAP[k]?.name || step.data.stage_id}`, k, 'agent');
      } else if (step.event === 'pipeline_complete') {
        addStructuredLog(`✔ ${step.data.message}`, 'system', 'agent');
        $('#bar').style.width = '100%';
        const pctEl = $('#pipeProgressPct');
        if (pctEl) pctEl.textContent = '100%';
        clearInterval(S.tick);
        const liveDot = $('#pipeLiveDot');
        const statusTxt = $('#pipeStatusText');
        if (liveDot) liveDot.className = 'pipe-live-dot done';
        if (statusTxt) statusTxt.textContent = 'DEMO RUN COMPLETE';
        $('#open').disabled = false;
        $('#open').textContent = 'Open studio (Demo Clips)';
        updateTaskHud('All demo shorts generated and verified.', null, null, null);
        toast('Demo run finished! Click Open studio to inspect.');
      }
    }, step.delay);
  });
}

// Check on page load for ?demo=1 or deep link state
document.addEventListener('DOMContentLoaded', () => {
  const urlParams = new URLSearchParams(window.location.search);
  if (urlParams.get('demo') === '1') {
    runDemoMode();
    return;
  }

  // Restore state from URL on initial load / reload
  const step = urlParams.get('step');
  const proj = urlParams.get('project');
  if (step === 'studio' || proj) {
    if (proj) {
      openProject(proj, false);
    } else {
      go(3, false);
    }
  } else if (step === 'pipeline') {
    go(2, false);
    // Automatically reconnect to active/recent session and replay logs
    api('/api/active-session').then(res => {
      if (res && res.active && res.session_id) {
        startRun(res.session_id, res.url || '', false);
      }
    }).catch(() => {});
  } else {
    // Replace initial state with step 1
    history.replaceState({ step: 1, vid: null }, '', window.location.href);
  }
});


/* Step 3: Studio & Curation */
const norm = (c, i) => ({
  id: c.id || c.clip_id || 'c' + String(i + 1).padStart(2, '0'),
  title: c.title || 'Clip ' + (i + 1),
  score: Math.round(c.score ?? c.virality_score ?? 0),
  dur: c.duration ?? ((c.end ?? 0) - (c.start ?? 0)),
  start: c.start,
  end: c.end,
  cat: c.category || c.type || 'reaction',
  status: c.status || c.approval || 'pending',
  why: c.why_viral || c.why || '',
  hook: c.hook_line || c.hook || c.headline || c.title || '',
  desc: c.description || '',
  tags: c.hashtags || c.tags || [],
  has_facecam: c.has_facecam !== false,
  vision: c.vision || null,
  sample_frame: c.sample_frame || null,
  br: c.breakdown || c.scores || null,
  variants: (c.variants || []).map((v, j) => {
    if (typeof v === 'string') return { file: v, label: LABELS[j] || v };
    return {
      file: v.file || v.filename || v.name,
      label: v.name || v.label || v.layout || LABELS[j] || 'Variant ' + (j + 1)
    };
  })
});

async function openProject(id, push = true) {
  try {
    const res = await api('/api/project/' + encodeURIComponent(id));
    const proj = res.project || res;
    S.vid = proj.video_id || id;
    S.title = proj.title || id;
    S.has_facecam = res.has_facecam !== false;
    S.clips = (res.clips || []).map(norm);
    S.cur = -1;
    go(3, push);
    $('#ptitle').textContent = S.title;
    renderList();
    if (S.clips.length) select(0);
  } catch (e) {
    toast('Couldn’t open project: ' + e.message);
  }
}

function stat() {
  const a = S.clips.filter(c => c.status === 'approved').length;
  const r = S.clips.filter(c => c.status === 'rejected').length;
  $('#pstat').textContent = `${a} approved · ${r} rejected`;

  const delBtn = $('#delRejectedBtn');
  if (delBtn) {
    if (r > 0) {
      delBtn.style.display = 'inline-flex';
      const countSpan = $('#delRejectedCount');
      if (countSpan) countSpan.textContent = r;
      delBtn.onclick = deleteRejected;
    } else {
      delBtn.style.display = 'none';
    }
  }
}

function renderList() {
  stat();
  $('#clipList').innerHTML = S.clips.map((c, i) => `
    <li>
      <button data-i="${i}" class="${i === S.cur ? 'on' : ''}">
        <span class="score">${c.score}</span>
        <span>
          ${esc(c.title)}<br>
          <small class="muted">${fmt(c.dur)} · ${esc(c.cat)}</small>
          <span class="tag ${c.status}">${c.status}</span>
        </span>
      </button>
    </li>
  `).join('');
  $$('#clipList button').forEach(b => b.onclick = () => select(+b.dataset.i));
}

function select(i, keep) {
  if (i < 0 || i >= S.clips.length) return;
  S.cur = i;
  if (!keep) S.vi = 0;
  const c = S.clips[i];
  renderList();

  $('#variants').innerHTML = c.variants.map((v, j) => `
    <button data-j="${j}" class="${j === S.vi ? 'on' : ''}">${j + 1} · ${esc(v.label)}</button>
  `).join('');
  $$('#variants button').forEach(b => b.onclick = () => variant(+b.dataset.j));

  inspect(c);
  load(true);
}

function variant(j) {
  const c = S.clips[S.cur];
  if (!c || !c.variants[j]) return;
  S.vi = j;
  $$('#variants button').forEach((b, k) => b.classList.toggle('on', k === j));
  load(false);
}

function load(fresh) {
  const c = S.clips[S.cur], v = c && c.variants[S.vi];
  $('#empty').style.display = v ? 'none' : 'grid';
  if (!v) {
    vid.removeAttribute('src');
    return;
  }

  // Update Facecam status badge on the player
  const camBadge = $('#phoneCamStatus');
  const camText = $('#phoneCamText');
  if (camBadge && camText) {
    const isDeframed = v.label && v.label.toLowerCase().includes('deframed');
    if (isDeframed) {
      camText.textContent = 'Facecam: None (Deframed 9:16)';
      camBadge.className = 'phone-cam-status none';
    } else if (c.has_facecam === false) {
      camText.textContent = 'Facecam: None';
      camBadge.className = 'phone-cam-status none';
    } else {
      camText.textContent = 'Facecam: Active (YuNet)';
      camBadge.className = 'phone-cam-status';
    }
  }

  const t = fresh ? 0 : vid.currentTime, was = !vid.paused, sp = vid.playbackRate;
  const bust = Date.now();
  vid.src = `/api/video/${encodeURIComponent(S.vid)}/${encodeURIComponent(v.file)}?t=${bust}`;
  vid.onloadedmetadata = () => {
    if (!fresh) vid.currentTime = t;
    vid.playbackRate = sp;
    if (was || fresh) vid.play().catch(() => {});
  };
  $('#dl').href = `/api/download/${encodeURIComponent(S.vid)}/${encodeURIComponent(v.file)}`;
}

function inspect(c) {
  const br = c.br || { hook: Math.min(100, c.score + 3), acoustic: Math.max(0, c.score - 6), retention: Math.max(0, c.score - 2) };
  const rows = [
    ['Hook Velocity', br.hook ?? br.hook_velocity],
    ['Acoustic Peak', br.acoustic ?? br.acoustic_peak],
    ['Retention Pacing', br.retention ?? br.retention_pacing]
  ];

  $('#inspect').innerHTML = `
    <div class="inspect-top-row">
      <div class="big">${c.score}<small class="muted">/100</small></div>
      <span class="facecam-meta-badge ${c.has_facecam ? 'on' : 'off'}">
        <span class="dot"></span> ${c.has_facecam ? 'Facecam Active' : 'No Facecam'}
      </span>
    </div>
    ${rows.map(([n, v]) => `
      <div class="meter">
        <div><span>${n}</span><span>${Math.round(v || 0)}</span></div>
        <i><u style="width:${Math.min(100, v || 0)}%"></u></i>
      </div>
    `).join('')}
    <h3>Top hook</h3>
    <div class="hook">${esc(c.hook)}</div>
    <h3>Why this goes viral</h3>
    <p class="why">${esc(c.why || 'High-energy emotional reaction with strong retention payoff.')}</p>
    <button class="wide" id="cropBtn" style="margin-top:4px;background:var(--ink);border:1px solid var(--lemon);color:var(--lemon);font-weight:700;">✂ Adjust Video Crop & Framing</button>
    <button class="wide" id="copy" style="margin-top:8px;">Copy social publishing pack</button>
    <div class="acts">
      <button class="ok ${c.status === 'approved' ? 'active' : ''}" id="ap">Approve [A]</button>
      <button class="no ${c.status === 'rejected' ? 'active' : ''}" id="rj">Reject [R]</button>
    </div>
    ${c.status === 'rejected' ? `
      <button class="wide del-clip-btn" id="delCurrentClip" title="Permanently delete this clip's video files">🗑 Delete This Rejected Clip Permanently</button>
    ` : ''}
    <button class="wide export" id="exp">Export approved to Ready-to-Post</button>
  `;

  $('#cropBtn').onclick = () => openCropEditor(c);
  $('#copy').onclick = copyPack;
  $('#ap').onclick = () => mark('approved');
  $('#rj').onclick = () => mark('rejected');
  $('#exp').onclick = exportAll;

  const delCurrent = $('#delCurrentClip');
  if (delCurrent) {
    delCurrent.onclick = deleteCurrentClip;
  }
}

async function copyPack() {
  const c = S.clips[S.cur];
  const tags = (Array.isArray(c.tags) ? c.tags : String(c.tags).split(/\s+/)).filter(Boolean).map(t => t[0] === '#' ? t : '#' + t);
  const txt = `${c.hook}\n\n${c.desc || c.why}\n\n${(tags.length ? tags : ['#gaming', '#shorts', '#viral', '#streamer', '#GTA6']).join(' ')}`;
  try {
    await navigator.clipboard.writeText(txt);
    toast('Social pack copied to clipboard');
  } catch (e) {
    toast('Copy blocked by browser.');
  }
}

async function mark(status) {
  const c = S.clips[S.cur];
  if (!c) return;
  const old = c.status;
  c.status = status;
  renderList();
  inspect(c);
  try {
    await post('/api/approve-clip', { video_id: S.vid, clip_id: c.id, status });
    toast(status === 'approved' ? 'Approved [A]' : 'Rejected [R]');
  } catch (e) {
    c.status = old;
    renderList();
    inspect(c);
    toast('Couldn’t save approval: ' + e.message);
  }
}

async function deleteRejected() {
  const rejected = S.clips.filter(c => c.status === 'rejected');
  if (!rejected.length) return toast('No rejected clips to delete.');

  const ok = window.confirm(`Permanently delete ${rejected.length} rejected clip(s)?\n\nThis will remove their rendered MP4 files and metadata from disk.`);
  if (!ok) return;

  try {
    const res = await post('/api/delete-rejected', { video_id: S.vid });
    if (res.success) {
      const deletedIds = res.deleted_clip_ids || [];
      S.clips = S.clips.filter(c => !deletedIds.includes(c.id));
      toast(`Permanently deleted ${res.deleted_count} rejected clip(s) (freed ${res.freed_mb} MB)`);
      if (S.clips.length) {
        select(Math.max(0, Math.min(S.cur, S.clips.length - 1)));
      } else {
        S.cur = -1;
        renderList();
        load(true);
        $('#inspect').innerHTML = '<p class="muted">No clips remaining in this project.</p>';
      }
    } else {
      toast('Delete failed: ' + (res.error || 'Unknown error'));
    }
  } catch (e) {
    toast('Delete failed: ' + e.message);
  }
}

async function deleteCurrentClip() {
  const c = S.clips[S.cur];
  if (!c) return;

  const ok = window.confirm(`Permanently delete "${c.title}"?\n\nThis will remove all its rendered MP4 video files from disk.`);
  if (!ok) return;

  try {
    const res = await post('/api/delete-clip', { video_id: S.vid, clip_id: c.id });
    if (res.success) {
      toast(`Permanently deleted "${c.title.slice(0, 25)}..."`);
      S.clips.splice(S.cur, 1);
      if (S.clips.length) {
        select(Math.max(0, Math.min(S.cur, S.clips.length - 1)));
      } else {
        S.cur = -1;
        renderList();
        load(true);
        $('#inspect').innerHTML = '<p class="muted">No clips remaining in this project.</p>';
      }
    } else {
      toast('Delete failed: ' + (res.error || 'Unknown error'));
    }
  } catch (e) {
    toast('Delete failed: ' + e.message);
  }
}

async function exportAll() {
  const n = S.clips.filter(c => c.status === 'approved').length;
  if (!n) return toast('Approve at least one clip first');
  try {
    const res = await post('/api/export', { video_id: S.vid });
    toast(`Exported ${n} clip${n > 1 ? 's' : ''} to Ready-to-Post`);
  } catch (e) {
    toast('Export failed: ' + e.message);
  }
}

/* Player Controls */
const playTog = () => vid.paused ? vid.play().catch(() => {}) : vid.pause();
$('#play').onclick = playTog;
vid.onclick = playTog;
vid.onplay = () => $('#play').textContent = 'Pause';
vid.onpause = () => $('#play').textContent = 'Play';
vid.ontimeupdate = () => {
  const d = vid.duration || 0;
  $('#seek').value = d ? vid.currentTime / d * 1000 : 0;
  $('#time').textContent = fmt(vid.currentTime) + ' / ' + fmt(d);
};
$('#seek').oninput = e => {
  if (vid.duration) vid.currentTime = e.target.value / 1000 * vid.duration;
};
$('#mute').onclick = () => {
  vid.muted = !vid.muted;
  $('#mute').textContent = vid.muted ? 'Muted' : 'Sound on';
};
$('#speed').onclick = () => {
  const s = [1, 1.5, 2], n = s[(s.indexOf(vid.playbackRate) + 1) % 3];
  vid.playbackRate = n;
  $('#speed').textContent = n + 'x';
};
vid.onended = () => {
  if (S.cur < S.clips.length - 1) select(S.cur + 1);
};

/* Keyboard Navigation */
addEventListener('keydown', e => {
  if (S.view !== 3 || /INPUT|SELECT|TEXTAREA/.test(e.target.tagName) && e.target.type !== 'range' || e.metaKey || e.ctrlKey) return;
  const k = e.key.toLowerCase();
  if (k === ' ') {
    e.preventDefault();
    playTog();
  } else if (k === 'a') {
    mark('approved');
  } else if (k === 'r') {
    mark('rejected');
  } else if ('123'.includes(k) && k) {
    variant(+k - 1);
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    select(S.cur - 1);
  } else if (e.key === 'ArrowDown') {
    e.preventDefault();
    select(S.cur + 1);
  }
});

/* ========================================================
   Interactive Video Crop & Framing Editor
   ======================================================== */
const FACECAM_PRESETS = {
  bl: { name: 'Bottom-Left (Speed)', box: [400, 0, 950, 280] },
  br: { name: 'Bottom-Right', box: [400, 720, 950, 1000] },
  center: { name: 'Center / Full', box: [150, 220, 850, 780] },
  tl: { name: 'Top-Left', box: [50, 0, 600, 280] },
  tr: { name: 'Top-Right', box: [50, 720, 600, 1000] },
};

let cropState = {
  clip: null,
  layout: 'stack',
  hasFacecam: true,
  splitPct: 38,
  focusX: 500,
  contentRatio: '3:4', // Default 3:4 matching reference
  textPosition: 'bottom', // 'above', 'middle', 'bottom'
  contentZoom: 1.0,
  streamerBox: [400, 0, 950, 280],
  camSizePct: 28,
  activePreset: 'bl',
  autoFocusX: 500,
  autoSplitPct: 38,
  autoHasFacecam: true,
  autoContentRatio: '3:4',
  autoTextPosition: 'bottom',
  autoContentZoom: 1.0,
  autoStreamerBox: [400, 0, 950, 280],
  isDraggingCam: false,
  dragStartX: 0,
  dragStartY: 0,
  dragStartBox: null,
};

function matchFacecamPreset(box) {
  if (!box || box.length !== 4) return 'bl';
  const [ymin, xmin, ymax, xmax] = box;
  if (xmin < 120 && ymin > 320) return 'bl';
  if (xmax > 880 && ymin > 320) return 'br';
  if (xmin < 120 && ymin < 250) return 'tl';
  if (xmax > 880 && ymin < 250) return 'tr';
  if (xmin > 160 && xmax < 840) return 'center';
  return 'custom';
}

function openCropEditor(c) {
  if (!c) return;
  cropState.clip = c;

  // Determine initial layout
  const curVariant = c.variants && c.variants[S.vi];
  let initialLayout = 'stack';
  if (curVariant) {
    const lbl = (curVariant.label || '').toLowerCase();
    if (lbl.includes('deframed') || lbl.includes('reframed') || lbl.includes('content')) initialLayout = 'reframed';
    else if (lbl.includes('letterbox') || lbl.includes('blur')) initialLayout = 'blur-fill';
    else initialLayout = 'stack';
  }
  cropState.layout = initialLayout;

  // Read vision parameters or defaults
  const vis = c.vision || {};
  cropState.hasFacecam = c.has_facecam !== false;
  cropState.splitPct = vis.streamer_pct || 38;
  cropState.focusX = vis.content_focus_x != null ? vis.content_focus_x : 500;
  cropState.contentRatio = vis.content_ratio || '3:4';
  cropState.textPosition = vis.text_position || 'bottom';
  cropState.contentZoom = vis.content_zoom || 1.0;

  // Streamer box
  if (vis.streamer_box && vis.streamer_box.length === 4) {
    cropState.streamerBox = [...vis.streamer_box];
  } else {
    // Default to Bottom-Left for gaming/streamer webcams
    cropState.streamerBox = [...FACECAM_PRESETS.bl.box];
  }
  cropState.activePreset = matchFacecamPreset(cropState.streamerBox);
  cropState.camSizePct = Math.round((cropState.streamerBox[3] - cropState.streamerBox[1]) / 10) || 28;

  // Save auto-detected defaults for Reset button
  cropState.autoHasFacecam = cropState.hasFacecam;
  cropState.autoSplitPct = cropState.splitPct;
  cropState.autoFocusX = cropState.focusX;
  cropState.autoContentRatio = cropState.contentRatio;
  cropState.autoTextPosition = cropState.textPosition;
  cropState.autoContentZoom = cropState.contentZoom;
  cropState.autoStreamerBox = [...cropState.streamerBox];

  // Update modal titles & images
  $('#cropModalTitle').textContent = `Adjust Video Crop · ${c.id.toUpperCase()}`;
  $('#cropModalSub').textContent = `Interactive framing and aspect ratios for "${c.title}"`;
  
  const frameUrl = c.sample_frame || `/api/video/${encodeURIComponent(S.vid)}/vision_${c.id}_mid.jpg`;
  const frameImg = $('#cropFrameImg');
  frameImg.onload = () => updateCropPreview();
  frameImg.src = frameUrl;
  if (frameImg.complete && frameImg.naturalWidth) {
    updateCropPreview();
  }

  // Sync inputs
  $$('#cropLayoutGroup button').forEach(b => {
    b.classList.toggle('on', b.dataset.layout === cropState.layout);
  });
  $$('#cropRatioGrid button').forEach(b => {
    b.classList.toggle('on', b.dataset.ratio === cropState.contentRatio);
  });
  if ($('#cropRatioVal')) {
    $('#cropRatioVal').textContent = cropState.contentRatio === '3:4' ? '3:4 (Reference)' : cropState.contentRatio;
  }
  $$('#cropTextPosBtns button').forEach(b => {
    b.classList.toggle('on', b.dataset.pos === cropState.textPosition);
  });
  if ($('#cropTextPosVal')) {
    $('#cropTextPosVal').textContent = cropState.textPosition[0].toUpperCase() + cropState.textPosition.slice(1);
  }
  if ($('#cropZoomSlider')) {
    $('#cropZoomSlider').value = Math.round(cropState.contentZoom * 100);
    $('#cropZoomVal').textContent = cropState.contentZoom.toFixed(1) + 'x';
  }
  if ($('#cropFacecamToggle')) $('#cropFacecamToggle').checked = cropState.hasFacecam;
  if ($('#cropSplitSlider')) {
    $('#cropSplitSlider').value = cropState.splitPct;
    $('#cropSplitVal').textContent = cropState.splitPct + '%';
  }
  if ($('#cropCamSizeSlider')) {
    $('#cropCamSizeSlider').value = cropState.camSizePct;
    $('#cropCamSizeVal').textContent = cropState.camSizePct + '%';
  }
  if ($('#cropFocusSlider')) {
    $('#cropFocusSlider').value = cropState.focusX;
    $('#cropFocusVal').textContent = `${Math.round(cropState.focusX / 10)}% (${cropState.focusX < 450 ? 'Left' : cropState.focusX > 550 ? 'Right' : 'Center'})`;
  }

  updateCropPreview();

  // Show modal
  $('#cropModal').style.display = 'flex';
  const url = new URL(window.location);
  url.searchParams.set('modal', 'crop');
  history.pushState({ step: 3, vid: S.vid, modal: 'crop' }, '', url.toString());
}

function closeCropEditor(pop = true) {
  $('#cropModal').style.display = 'none';
  const url = new URL(window.location);
  if (url.searchParams.has('modal')) {
    url.searchParams.delete('modal');
    if (pop) {
      history.pushState({ step: 3, vid: S.vid }, '', url.toString());
    }
  }
}

function updateCropPreview() {
  const box = $('#cropBox');
  const boxHandle = $('#cropBoxHandle');
  const scrimLeft = $('#cropScrimLeft');
  const scrimRight = $('#cropScrimRight');
  const camBox = $('#cropCamBox');
  const fcSection = $('#cropFacecamSection');
  const ratioSection = $('#cropRatioSection');
  const focusGroup = $('#cropFocusGroup');
  const zoomGroup = $('#cropZoomGroup');
  const livePill = $('#cropLivePill');
  const tierLabel = $('#cropTierLabel');
  const aiHint = $('#cropAiHint');
  const aiHintText = $('#cropAiHintText');
  const hintEl = $('#cropCanvasHint');

  // Calculate crop box aspect ratio relative to 16:9 canvas (16/9 = 1.7778)
  let targetAr = 9 / 16;
  if (cropState.layout === 'reframed') {
    if (cropState.contentRatio === '3:4') targetAr = 3 / 4;
    else if (cropState.contentRatio === '1:1') targetAr = 1.0;
    else if (cropState.contentRatio === '4:5') targetAr = 4 / 5;
    else if (cropState.contentRatio === '16:9') targetAr = 16 / 9;
    else targetAr = 9 / 16;
  } else if (cropState.layout === 'stack') {
    // Bottom gameplay tier aspect ratio
    const botH = 1920 - 1920 * (cropState.splitPct / 100);
    targetAr = Math.min(1.5, Math.max(0.6, 1080 / botH));
  } else if (cropState.layout === 'blur-fill') {
    targetAr = 16 / 9;
  }

  // Width percentage on 16:9 canvas
  const canvasAr = 16 / 9;
  let baseWidthPct = (targetAr / canvasAr) * 100;
  if (cropState.layout === 'reframed') {
    baseWidthPct = Math.min(100, Math.max(15, baseWidthPct / cropState.contentZoom));
  } else {
    baseWidthPct = Math.min(100, Math.max(20, baseWidthPct));
  }

  const centerPct = (cropState.focusX / 1000) * 100;
  const leftPct = Math.max(0, Math.min(100 - baseWidthPct, centerPct - baseWidthPct / 2));
  const rightPct = leftPct + baseWidthPct;

  // Sync 16:9 Stage Card elements
  if (cropState.layout === 'blur-fill') {
    // Reference Letterbox: full 16:9 stream is fitted into vertical frame (no horizontal crop)
    if (scrimLeft) scrimLeft.style.display = 'none';
    if (scrimRight) scrimRight.style.display = 'none';
    if (box) {
      box.style.display = 'block';
      box.style.left = '0%';
      box.style.width = '100%';
    }
    if (boxHandle) boxHandle.style.display = 'none';
    if (tierLabel) tierLabel.textContent = 'Full 16:9 Stream (Letterbox)';
    if (camBox) camBox.style.display = 'none';
  } else {
    if (scrimLeft) {
      scrimLeft.style.display = 'block';
      scrimLeft.style.width = leftPct + '%';
    }
    if (scrimRight) {
      scrimRight.style.display = 'block';
      scrimRight.style.left = rightPct + '%';
      scrimRight.style.width = (100 - rightPct) + '%';
    }
    if (box) {
      box.style.display = 'block';
      box.style.left = leftPct + '%';
      box.style.width = baseWidthPct + '%';
    }
    if (boxHandle) {
      boxHandle.style.display = 'block';
      boxHandle.innerHTML = cropState.layout === 'stack' ? '<span>↔ Drag to Pan Gameplay</span>' : '<span>↔ Drag to Pan</span>';
    }
    if (tierLabel) {
      tierLabel.textContent = cropState.layout === 'stack' ? 'Gameplay / Content' : `Content (${cropState.contentRatio})`;
    }
    if (camBox) {
      if (cropState.layout === 'stack' && cropState.hasFacecam) {
        camBox.style.display = 'flex';
        const [ymin, xmin, ymax, xmax] = cropState.streamerBox;
        camBox.style.left = (xmin / 10) + '%';
        camBox.style.top = (ymin / 10) + '%';
        camBox.style.width = Math.max(8, (xmax - xmin) / 10) + '%';
        camBox.style.height = Math.max(8, (ymax - ymin) / 10) + '%';
      } else {
        camBox.style.display = 'none';
      }
    }
  }

  // Update canvas hint
  if (hintEl) {
    if (cropState.layout === 'reframed') {
      hintEl.innerHTML = '💡 Drag the <b>yellow box</b> or click the 16:9 stream to pan · Choose <b>aspect ratio & subtitle position</b> below';
    } else if (cropState.layout === 'stack') {
      hintEl.innerHTML = '💡 Drag <b>cyan facecam</b> or <b>yellow gameplay</b> box · Adjust split & position controls';
    } else {
      hintEl.innerHTML = '💡 Full 16:9 stream centered in vertical 9:16 letterbox with top headline & subtitles';
    }
  }

  // Toggle controls sections based on layout
  if (cropState.layout === 'reframed') {
    if (ratioSection) ratioSection.style.display = 'flex';
    if (fcSection) fcSection.style.display = 'none';
    if (focusGroup) focusGroup.style.display = 'flex';
    if (zoomGroup) zoomGroup.style.display = 'flex';
    if (livePill) livePill.textContent = `Deframed ${cropState.contentRatio}`;
  } else if (cropState.layout === 'stack') {
    if (ratioSection) ratioSection.style.display = 'none';
    if (fcSection) fcSection.style.display = 'flex';
    if (focusGroup) focusGroup.style.display = 'flex';
    if (zoomGroup) zoomGroup.style.display = 'none';
    if (livePill) livePill.textContent = 'Reaction Stacked';
  } else {
    // Reference Letterbox
    if (ratioSection) ratioSection.style.display = 'none';
    if (fcSection) fcSection.style.display = 'none';
    if (focusGroup) focusGroup.style.display = 'none';
    if (zoomGroup) zoomGroup.style.display = 'none';
    if (livePill) livePill.textContent = 'Reference Letterbox';
  }

  // Facecam AI Hint: ONLY show for Reaction Stacked layout AND when Facecam is enabled
  if (aiHint) {
    if (cropState.layout === 'stack' && cropState.hasFacecam) {
      aiHint.style.display = 'flex';
      if (aiHintText) {
        if (cropState.activePreset === 'auto') {
          aiHintText.textContent = 'Facecam auto-detected by OpenCV YuNet.';
        } else if (cropState.activePreset === 'bl') {
          aiHintText.textContent = 'Facecam positioned at Bottom-Left corner.';
        } else if (cropState.activePreset === 'br') {
          aiHintText.textContent = 'Facecam positioned at Bottom-Right corner.';
        } else if (cropState.activePreset === 'tl') {
          aiHintText.textContent = 'Facecam positioned at Top-Left corner.';
        } else if (cropState.activePreset === 'tr') {
          aiHintText.textContent = 'Facecam positioned at Top-Right corner.';
        } else if (cropState.activePreset === 'center') {
          aiHintText.textContent = 'Facecam centered on streamer.';
        } else {
          aiHintText.textContent = 'Facecam custom positioned by user.';
        }
      }
    } else {
      aiHint.style.display = 'none';
    }
  }

  // Update button active states
  $$('#cropLayoutGroup button').forEach(b => {
    b.classList.toggle('on', b.dataset.layout === cropState.layout);
  });
  $$('#cropRatioGrid button').forEach(b => {
    b.classList.toggle('on', b.dataset.ratio === cropState.contentRatio);
  });
  $$('#cropPresetGrid button').forEach(b => {
    b.classList.toggle('on', b.dataset.pos === cropState.activePreset);
  });
  $$('#cropTextPosBtns button').forEach(b => {
    b.classList.toggle('on', b.dataset.pos === cropState.textPosition);
  });

  // Update text tags
  if ($('#cropRatioVal')) $('#cropRatioVal').textContent = cropState.contentRatio === '3:4' ? '3:4 (Reference)' : cropState.contentRatio;
  if ($('#cropTextPosVal')) $('#cropTextPosVal').textContent = cropState.textPosition[0].toUpperCase() + cropState.textPosition.slice(1);
  if ($('#cropZoomVal')) $('#cropZoomVal').textContent = cropState.contentZoom.toFixed(1) + 'x';
  if ($('#cropSplitVal')) $('#cropSplitVal').textContent = cropState.splitPct + '%';
  if ($('#cropCamSizeVal')) $('#cropCamSizeVal').textContent = cropState.camSizePct + '%';
  if ($('#cropFocusVal')) $('#cropFocusVal').textContent = `${Math.round(cropState.focusX / 10)}% (${cropState.focusX < 450 ? 'Left' : cropState.focusX > 550 ? 'Right' : 'Center'})`;

  // Draw 9:16 Live Canvas Preview
  drawLive916Preview();
}

function drawLive916Preview() {
  const canvas = $('#cropLiveCanvas');
  const img = $('#cropFrameImg');
  if (!canvas || !img || !img.naturalWidth) return;

  const ctx = canvas.getContext('2d');
  const cw = canvas.width;  // 270
  const ch = canvas.height; // 480
  const nw = img.naturalWidth;
  const nh = img.naturalHeight;

  // Solid deep black background for professional presentation
  ctx.fillStyle = '#000000';
  ctx.fillRect(0, 0, cw, ch);

  if (cropState.layout === 'stack') {
    if (cropState.hasFacecam) {
      // 1. Reaction Stacked: Top Tier (Streamer Facecam)
      const topH = Math.round(ch * (cropState.splitPct / 100));
      const botH = ch - topH;

      const [s_ymin, s_xmin, s_ymax, s_xmax] = cropState.streamerBox;
      const raw_bx = (s_xmin / 1000) * nw;
      const raw_by = (s_ymin / 1000) * nh;
      const raw_bw = Math.max(10, ((s_xmax - s_xmin) / 1000) * nw);
      const raw_bh = Math.max(10, ((s_ymax - s_ymin) / 1000) * nh);
      const center_sx = raw_bx + raw_bw / 2;
      const center_sy = raw_by + raw_bh / 2;

      const ar_top = cw / topH;
      const needed_h = Math.max(raw_bh * 1.25, (raw_bw / ar_top) * 1.1);
      let s_src_h = Math.max(nh * 0.35, Math.min(nh, needed_h));
      let s_src_w = s_src_h * ar_top;
      if (s_src_w > nw) {
        s_src_w = nw;
        s_src_h = s_src_w / ar_top;
      }
      const s_src_x = Math.max(0, Math.min(nw - s_src_w, center_sx - s_src_w / 2));
      const s_src_y = Math.max(0, Math.min(nh - s_src_h, center_sy - s_src_h / 2));

      ctx.drawImage(img, s_src_x, s_src_y, s_src_w, s_src_h, 0, 0, cw, topH);

      // 2. Bottom Tier (Gameplay / Content)
      const ar_bot = cw / botH;
      let g_src_h = nh;
      let g_src_w = g_src_h * ar_bot;
      if (g_src_w > nw) {
        g_src_w = nw;
        g_src_h = g_src_w / ar_bot;
      }
      const focus_cx = (cropState.focusX / 1000) * nw;
      const g_src_x = Math.max(0, Math.min(nw - g_src_w, focus_cx - g_src_w / 2));
      const g_src_y = 0;

      ctx.drawImage(img, g_src_x, g_src_y, g_src_w, g_src_h, 0, topH, cw, botH);

      // Divider Line (Neon Magenta glow)
      ctx.fillStyle = '#FF4F7B';
      ctx.shadowColor = '#FF4F7B';
      ctx.shadowBlur = 6;
      ctx.fillRect(0, topH - 1, cw, 2);
      ctx.shadowBlur = 0;

      // Subtitles
      ctx.save();
      ctx.textAlign = 'center';
      ctx.font = '800 14px var(--d, Anton, system-ui)';
      if (cropState.textPosition === 'above') {
        ctx.fillStyle = '#00F2FE';
        ctx.shadowColor = '#00F2FE';
        ctx.shadowBlur = 8;
        ctx.fillText('DISTILL FIVE TO', cw / 2, 44);
      } else if (cropState.textPosition === 'bottom') {
        ctx.fillStyle = '#00F2FE';
        ctx.shadowColor = '#00F2FE';
        ctx.shadowBlur = 8;
        ctx.fillText('DISTILL FIVE TO', cw / 2, ch - 38);
      } else {
        // Middle / Across Seam
        ctx.fillStyle = '#FFE14A';
        ctx.shadowColor = 'rgba(0,0,0,0.9)';
        ctx.shadowBlur = 6;
        ctx.fillText('DISTILL FIVE TO', cw / 2, topH + 24);
      }
      ctx.restore();
    } else {
      // Facecam disabled in Stack mode: single full-bleed 9:16 crop
      const ar = 9 / 16;
      const crop_w = Math.round(nh * ar);
      const focus_cx = (cropState.focusX / 1000) * nw;
      const crop_x = Math.max(0, Math.min(nw - crop_w, focus_cx - crop_w / 2));
      ctx.drawImage(img, crop_x, 0, crop_w, nh, 0, 0, cw, ch);

      // Subtitles
      ctx.save();
      ctx.textAlign = 'center';
      ctx.font = '800 14px var(--d, Anton, system-ui)';
      ctx.fillStyle = '#00F2FE';
      ctx.shadowColor = '#00F2FE';
      ctx.shadowBlur = 8;
      const subY = cropState.textPosition === 'above' ? 44 : cropState.textPosition === 'middle' ? ch / 2 : ch - 42;
      ctx.fillText('DISTILL FIVE TO', cw / 2, subY);
      ctx.restore();
    }
  } else if (cropState.layout === 'blur-fill') {
    // ----------------------------------------------------
    // Reference Letterbox (Full 16:9 stream centered in 9:16)
    // ----------------------------------------------------
    ctx.fillStyle = '#000000';
    ctx.fillRect(0, 0, cw, ch);

    // Full 16:9 stream (height = 152px on 270x480 canvas)
    const lbH = Math.round(cw * (9 / 16));
    const lbY = Math.round((ch - lbH) / 2); // 164px
    ctx.drawImage(img, 0, 0, nw, nh, 0, lbY, cw, lbH);

    // 3-Line Bold Headline in Top Black Letterbox Bar (Exact Reference Short Style)
    const clipTitle = cropState.clip?.title || 'GOING OVER GTA 6';
    const cleanTitle = clipTitle.replace(/[#@].*/, '').trim().toUpperCase();
    const words = cleanTitle.split(/\s+/);

    let l1 = 'LIVE REACTION';
    let l2 = cleanTitle;
    let l3 = 'GTA 6';
    if (words.length >= 4) {
      const p1 = Math.ceil(words.length / 3);
      const p2 = Math.ceil((words.length * 2) / 3);
      l1 = words.slice(0, p1).join(' ');
      l2 = words.slice(p1, p2).join(' ');
      l3 = words.slice(p2).join(' ');
    } else if (words.length >= 2) {
      l1 = words[0];
      l2 = words.slice(1).join(' ');
      l3 = '';
    }

    ctx.save();
    ctx.textAlign = 'center';
    ctx.font = '800 13px var(--d, Anton, system-ui)';
    ctx.fillStyle = '#FFFFFF';
    ctx.shadowColor = 'rgba(0,0,0,0.9)';
    ctx.shadowBlur = 4;
    ctx.fillText(l1, cw / 2, Math.max(32, lbY - 80));

    ctx.fillStyle = '#00F2FE';
    ctx.shadowColor = '#00F2FE';
    ctx.shadowBlur = 8;
    ctx.fillText(l2, cw / 2, Math.max(54, lbY - 55));

    if (l3) {
      ctx.fillStyle = '#FFFFFF';
      ctx.shadowColor = 'rgba(0,0,0,0.9)';
      ctx.shadowBlur = 4;
      ctx.fillText(l3, cw / 2, Math.max(76, lbY - 30));
    }
    ctx.restore();

    // Kinetic Subtitles in Bottom Black Letterbox Bar
    ctx.save();
    ctx.textAlign = 'center';
    ctx.font = '800 14px var(--d, Anton, system-ui)';
    ctx.fillStyle = '#00F2FE';
    ctx.shadowColor = '#00F2FE';
    ctx.shadowBlur = 8;
    const subY = cropState.textPosition === 'above' ? Math.max(26, lbY - 14) : cropState.textPosition === 'middle' ? ch / 2 : lbY + lbH + 50;
    ctx.fillText('DISTILL FIVE TO', cw / 2, subY);
    ctx.restore();
  } else {
    // ----------------------------------------------------
    // Deframed Content Mode with Selectable Aspect Ratios
    // ----------------------------------------------------
    const ratio = cropState.contentRatio || '3:4';
    const zoom = cropState.contentZoom || 1.0;

    let targetAr = 3 / 4;
    let destW = cw;
    let destH = Math.round(cw * (4 / 3)); // 360px on 480 canvas
    let destY = Math.round((ch - destH) / 2); // 60px top bar, 60px bottom bar

    if (ratio === '9:16') {
      targetAr = 9 / 16;
      destW = cw;
      destH = ch;
      destY = 0;
    } else if (ratio === '1:1') {
      targetAr = 1.0;
      destW = cw;
      destH = cw; // 270px
      destY = Math.round((ch - destH) / 2); // 105px top, 105px bottom
    } else if (ratio === '4:5') {
      targetAr = 4 / 5;
      destW = cw;
      destH = Math.round(cw * (5 / 4)); // 338px
      destY = Math.round((ch - destH) / 2); // 71px top, 71px bottom
    } else if (ratio === '16:9') {
      targetAr = 16 / 9;
      destW = cw;
      destH = Math.round(cw * (9 / 16)); // 152px
      destY = Math.round((ch - destH) / 2);
    }

    const crop_h = Math.round(nh / zoom);
    const crop_w = Math.round(crop_h * targetAr);
    const focus_cx = (cropState.focusX / 1000) * nw;
    const crop_x = Math.max(0, Math.min(nw - crop_w, focus_cx - crop_w / 2));
    const crop_y = Math.max(0, Math.min(nh - crop_h, (nh - crop_h) / 2));

    ctx.drawImage(img, crop_x, crop_y, crop_w, crop_h, 0, destY, destW, destH);

    // Reference style Top Hook Header in top black space (if ratio has top letterbox and position isn't above)
    if (ratio !== '9:16' && cropState.textPosition !== 'above') {
      const hookText = cropState.clip?.title || cropState.clip?.hook_line || 'WHY YOU SHOULD IGNORE ADVICE';
      const cleanHook = hookText.replace(/[#@].*/, '').trim().toUpperCase();
      const words = cleanHook.split(/\s+/);

      let l1 = 'WHY YOU SHOULD';
      let l2 = 'IGNORE ADVICE';
      if (words.length >= 3) {
        const splitIdx = Math.ceil(words.length / 2);
        l1 = words.slice(0, splitIdx).join(' ');
        l2 = words.slice(splitIdx).join(' ');
      }

      ctx.save();
      ctx.textAlign = 'center';
      ctx.font = '800 13px var(--d, Anton, system-ui)';
      ctx.fillStyle = '#FFFFFF';
      ctx.shadowColor = 'rgba(0,0,0,0.8)';
      ctx.shadowBlur = 4;
      ctx.fillText(l1, cw / 2, Math.max(22, destY - 26));

      if (l2) {
        ctx.fillStyle = '#00F2FE';
        ctx.shadowColor = '#00F2FE';
        ctx.shadowBlur = 8;
        ctx.fillText(l2, cw / 2, Math.max(38, destY - 10));
      }
      ctx.restore();
    }

    // Subtitle Text Preview according to chosen textPosition
    ctx.save();
    ctx.textAlign = 'center';
    ctx.font = '800 14px var(--d, Anton, system-ui)';

    if (cropState.textPosition === 'above') {
      ctx.fillStyle = '#00F2FE';
      ctx.shadowColor = '#00F2FE';
      ctx.shadowBlur = 8;
      ctx.fillText('DISTILL FIVE TO', cw / 2, 44);
    } else if (cropState.textPosition === 'middle') {
      ctx.fillStyle = '#FFE14A';
      ctx.shadowColor = 'rgba(0,0,0,0.9)';
      ctx.shadowBlur = 6;
      ctx.fillText('DISTILL FIVE TO', cw / 2, ch / 2);
    } else {
      ctx.fillStyle = '#00F2FE';
      ctx.shadowColor = '#00F2FE';
      ctx.shadowBlur = 8;
      const subY = ratio === '9:16' ? ch - 42 : Math.min(ch - 30, destY + destH + 34);
      ctx.fillText('DISTILL FIVE TO', cw / 2, subY);
    }
    ctx.restore();
  }
}

// ----------------------------------------------------
// Interactive Dragging & Clicking for Crop & Facecam Boxes
// ----------------------------------------------------
const camBoxEl = $('#cropCamBox');
const cropBoxEl = $('#cropBox');
const stageEl = $('#cropStage');

let justDragged = false;

// 1. Draggable Cyan Streamer Facecam Box
if (camBoxEl && stageEl) {
  camBoxEl.onpointerdown = (e) => {
    e.preventDefault();
    e.stopPropagation();
    cropState.isDraggingCam = true;
    cropState.dragStartX = e.clientX;
    cropState.dragStartY = e.clientY;
    cropState.dragStartBox = [...cropState.streamerBox];
    try { camBoxEl.setPointerCapture(e.pointerId); } catch (_) {}
  };

  camBoxEl.onpointermove = (e) => {
    if (!cropState.isDraggingCam) return;
    justDragged = true;
    const stageRect = stageEl.getBoundingClientRect();
    const dx = (e.clientX - cropState.dragStartX) / stageRect.width * 1000;
    const dy = (e.clientY - cropState.dragStartY) / stageRect.height * 1000;

    const [s_ymin, s_xmin, s_ymax, s_xmax] = cropState.dragStartBox;
    const w = s_xmax - s_xmin;
    const h = s_ymax - s_ymin;

    const newXmin = Math.max(0, Math.min(1000 - w, Math.round(s_xmin + dx)));
    const newYmin = Math.max(0, Math.min(1000 - h, Math.round(s_ymin + dy)));

    cropState.streamerBox = [newYmin, newXmin, newYmin + h, newXmin + w];
    cropState.activePreset = 'custom';
    updateCropPreview();
  };

  const endCamDrag = (e) => {
    if (cropState.isDraggingCam) {
      cropState.isDraggingCam = false;
      try { camBoxEl.releasePointerCapture(e.pointerId); } catch (_) {}
      setTimeout(() => { justDragged = false; }, 80);
    }
  };
  camBoxEl.onpointerup = endCamDrag;
  camBoxEl.onpointercancel = endCamDrag;
}

// 2. Draggable Yellow Framing Box (Gameplay / Content)
if (cropBoxEl && stageEl) {
  cropBoxEl.onpointerdown = (e) => {
    e.preventDefault();
    e.stopPropagation();
    cropState.isDraggingBox = true;
    cropState.dragBoxStartX = e.clientX;
    cropState.dragBoxStartFocusX = cropState.focusX;
    try { cropBoxEl.setPointerCapture(e.pointerId); } catch (_) {}
  };

  cropBoxEl.onpointermove = (e) => {
    if (!cropState.isDraggingBox) return;
    justDragged = true;
    const stageRect = stageEl.getBoundingClientRect();
    const dx = (e.clientX - cropState.dragBoxStartX) / stageRect.width * 1000;
    const newFocusX = Math.max(50, Math.min(950, Math.round(cropState.dragBoxStartFocusX + dx)));

    cropState.focusX = newFocusX;
    if ($('#cropFocusSlider')) $('#cropFocusSlider').value = newFocusX;
    if ($('#cropFocusVal')) {
      $('#cropFocusVal').textContent = `${Math.round(newFocusX / 10)}% (${newFocusX < 450 ? 'Left' : newFocusX > 550 ? 'Right' : 'Center'})`;
    }
    updateCropPreview();
  };

  const endBoxDrag = (e) => {
    if (cropState.isDraggingBox) {
      cropState.isDraggingBox = false;
      try { cropBoxEl.releasePointerCapture(e.pointerId); } catch (_) {}
      setTimeout(() => { justDragged = false; }, 80);
    }
  };
  cropBoxEl.onpointerup = endBoxDrag;
  cropBoxEl.onpointercancel = endBoxDrag;
}

// 3. Stage Click to Reposition
if (stageEl) {
  stageEl.onclick = (e) => {
    if (justDragged) return;
    if (e.target === camBoxEl || camBoxEl?.contains(e.target)) return;
    if (e.target === cropBoxEl || cropBoxEl?.contains(e.target)) return;

    const stageRect = stageEl.getBoundingClientRect();
    const clickX = Math.max(0, Math.min(1000, Math.round((e.clientX - stageRect.left) / stageRect.width * 1000)));
    const clickY = Math.max(0, Math.min(1000, Math.round((e.clientY - stageRect.top) / stageRect.height * 1000)));

    if (cropState.layout === 'reframed' || cropState.layout === 'reference' || !cropState.hasFacecam) {
      // Content reframing: center the framing box at clickX
      cropState.focusX = Math.max(50, Math.min(950, clickX));
      if ($('#cropFocusSlider')) $('#cropFocusSlider').value = cropState.focusX;
      if ($('#cropFocusVal')) {
        $('#cropFocusVal').textContent = `${Math.round(cropState.focusX / 10)}% (${cropState.focusX < 450 ? 'Left' : cropState.focusX > 550 ? 'Right' : 'Center'})`;
      }
      updateCropPreview();
    } else if (cropState.layout === 'stack' && cropState.hasFacecam) {
      const splitY = (cropState.splitPct / 100) * 1000;
      if (clickY < splitY) {
        // Upper zone click: center facecam box at click position
        const w = cropState.streamerBox[3] - cropState.streamerBox[1];
        const h = cropState.streamerBox[2] - cropState.streamerBox[0];
        const newXmin = Math.max(0, Math.min(1000 - w, Math.round(clickX - w / 2)));
        const newYmin = Math.max(0, Math.min(1000 - h, Math.round(clickY - h / 2)));
        cropState.streamerBox = [newYmin, newXmin, newYmin + h, newXmin + w];
        cropState.activePreset = 'custom';
        updateCropPreview();
      } else {
        // Lower zone click: center gameplay focus at clickX
        cropState.focusX = Math.max(50, Math.min(950, clickX));
        if ($('#cropFocusSlider')) $('#cropFocusSlider').value = cropState.focusX;
        if ($('#cropFocusVal')) {
          $('#cropFocusVal').textContent = `${Math.round(cropState.focusX / 10)}% (${cropState.focusX < 450 ? 'Left' : cropState.focusX > 550 ? 'Right' : 'Center'})`;
        }
        updateCropPreview();
      }
    }
  };
}

// Event Listeners for Crop Editor
if ($('#cropCloseBtn')) $('#cropCloseBtn').onclick = closeCropEditor;
if ($('#cropCancelBtn')) $('#cropCancelBtn').onclick = closeCropEditor;

$$('#cropLayoutGroup button').forEach(b => {
  b.onclick = () => {
    cropState.layout = b.dataset.layout;
    $$('#cropLayoutGroup button').forEach(btn => btn.classList.toggle('on', btn === b));
    updateCropPreview();
  };
});

if ($('#cropFacecamToggle')) $('#cropFacecamToggle').onchange = function() {
  cropState.hasFacecam = this.checked;
  updateCropPreview();
};

// Preset grid buttons
$$('#cropPresetGrid button').forEach(b => {
  b.onclick = () => {
    const pos = b.dataset.pos;
    if (pos === 'auto') {
      cropState.streamerBox = [...cropState.autoStreamerBox];
      cropState.activePreset = 'auto';
      toast('Facecam reset to auto-detected position');
    } else if (FACECAM_PRESETS[pos]) {
      cropState.streamerBox = [...FACECAM_PRESETS[pos].box];
      cropState.activePreset = pos;
      toast(`Facecam set to ${FACECAM_PRESETS[pos].name}`);
    }
    cropState.camSizePct = Math.round((cropState.streamerBox[3] - cropState.streamerBox[1]) / 10);
    if ($('#cropCamSizeSlider')) $('#cropCamSizeSlider').value = cropState.camSizePct;
    updateCropPreview();
  };
});

// Facecam Box Size / Zoom slider
if ($('#cropCamSizeSlider')) $('#cropCamSizeSlider').oninput = function() {
  const val = +this.value;
  cropState.camSizePct = val;

  const [ymin, xmin, ymax, xmax] = cropState.streamerBox;
  const cx = (xmin + xmax) / 2;
  const cy = (ymin + ymax) / 2;

  const newW = val * 10;
  const newH = Math.min(1000, Math.round(newW * 1.8));

  const nxMin = Math.max(0, Math.min(1000 - newW, Math.round(cx - newW / 2)));
  const nyMin = Math.max(0, Math.min(1000 - newH, Math.round(cy - newH / 2)));

  cropState.streamerBox = [nyMin, nxMin, nyMin + newH, nxMin + newW];
  cropState.activePreset = 'custom';
  updateCropPreview();
};

// Ratio grid buttons
$$('#cropRatioGrid button').forEach(b => {
  b.onclick = () => {
    cropState.contentRatio = b.dataset.ratio;
    $$('#cropRatioGrid button').forEach(btn => btn.classList.toggle('on', btn === b));
    if ($('#cropRatioVal')) {
      $('#cropRatioVal').textContent = cropState.contentRatio === '3:4' ? '3:4 (Reference)' : cropState.contentRatio;
    }
    updateCropPreview();
    toast(`Aspect ratio set to ${cropState.contentRatio}`);
  };
});

// Text position buttons
$$('#cropTextPosBtns button').forEach(b => {
  b.onclick = () => {
    cropState.textPosition = b.dataset.pos;
    $$('#cropTextPosBtns button').forEach(btn => btn.classList.toggle('on', btn === b));
    if ($('#cropTextPosVal')) {
      $('#cropTextPosVal').textContent = cropState.textPosition[0].toUpperCase() + cropState.textPosition.slice(1);
    }
    updateCropPreview();
    toast(`Text position set to ${cropState.textPosition}`);
  };
});

// Content Zoom slider
if ($('#cropZoomSlider')) $('#cropZoomSlider').oninput = function() {
  cropState.contentZoom = +this.value / 100;
  if ($('#cropZoomVal')) $('#cropZoomVal').textContent = cropState.contentZoom.toFixed(1) + 'x';
  updateCropPreview();
};

if ($('#cropSplitSlider')) $('#cropSplitSlider').oninput = function() {
  cropState.splitPct = +this.value;
  updateCropPreview();
};

if ($('#cropFocusSlider')) $('#cropFocusSlider').oninput = function() {
  cropState.focusX = +this.value;
  updateCropPreview();
};

if ($('#cropResetBtn')) $('#cropResetBtn').onclick = () => {
  cropState.focusX = cropState.autoFocusX;
  cropState.splitPct = cropState.autoSplitPct;
  cropState.hasFacecam = cropState.autoHasFacecam;
  cropState.contentRatio = cropState.autoContentRatio;
  cropState.textPosition = cropState.autoTextPosition;
  cropState.contentZoom = cropState.autoContentZoom;
  cropState.streamerBox = [...cropState.autoStreamerBox];
  cropState.activePreset = matchFacecamPreset(cropState.streamerBox);
  cropState.camSizePct = Math.round((cropState.streamerBox[3] - cropState.streamerBox[1]) / 10);

  if ($('#cropFocusSlider')) $('#cropFocusSlider').value = cropState.focusX;
  if ($('#cropSplitSlider')) $('#cropSplitSlider').value = cropState.splitPct;
  if ($('#cropCamSizeSlider')) $('#cropCamSizeSlider').value = cropState.camSizePct;
  if ($('#cropZoomSlider')) $('#cropZoomSlider').value = Math.round(cropState.contentZoom * 100);
  if ($('#cropFacecamToggle')) $('#cropFacecamToggle').checked = cropState.hasFacecam;
  updateCropPreview();
  toast('Reset to default framing and positions');
};

if ($('#cropApplyBtn')) $('#cropApplyBtn').onclick = async () => {
  const c = cropState.clip;
  if (!c) return;

  const btn = $('#cropApplyBtn');
  const spinner = $('#cropApplySpinner');
  const btnText = $('#cropApplyText');

  btn.disabled = true;
  if (spinner) spinner.style.display = 'inline-block';
  if (btnText) btnText.textContent = 'Re-rendering with VideoToolbox…';

  try {
    const res = await post('/api/rerender-clip', {
      video_id: S.vid,
      clip_id: c.id,
      layout: cropState.layout,
      streamer_pct: cropState.splitPct,
      content_focus_x: cropState.focusX,
      has_facecam: cropState.hasFacecam,
      streamer_box: cropState.streamerBox,
      content_ratio: cropState.contentRatio,
      text_position: cropState.textPosition,
      content_zoom: cropState.contentZoom,
    });

    if (res && res.success) {
      c.vision = res.vision;
      c.has_facecam = res.vision.has_facecam !== false;
      if (res.sidecar && res.sidecar.variants) {
        c.variants = res.sidecar.variants.map((v, j) => ({
          file: v.file,
          label: v.name || v.label || LABELS[j] || 'Variant ' + (j + 1)
        }));
      }

      // Find matching variant index based on layout
      const targetLayout = res.layout_target || cropState.layout;
      let newVi = 0;
      if (targetLayout === 'reframed') {
        newVi = c.variants.findIndex(v => (v.label || '').toLowerCase().includes('deframed') || (v.file || '').includes('reframed'));
      } else if (targetLayout === 'blur-fill') {
        newVi = c.variants.findIndex(v => (v.label || '').toLowerCase().includes('letterbox') || (v.label || '').toLowerCase().includes('blur'));
      } else {
        newVi = c.variants.findIndex(v => (v.label || '').toLowerCase().includes('reaction') || (v.label || '').toLowerCase().includes('stack'));
        if (newVi < 0) {
          newVi = c.variants.findIndex(v => (v.label || '').toLowerCase().includes('deframed') || (v.file || '').includes('reframed'));
        }
      }
      if (newVi < 0) newVi = 0;
      S.vi = newVi;

      // Fully re-render list, variant buttons, inspector, and reload video player
      select(S.cur, true);

      toast(`Clip ${c.id.toUpperCase()} re-rendered with new facecam framing!`);
      closeCropEditor();
    } else {
      toast('Re-render failed: ' + (res?.error || 'Unknown error'));
    }
  } catch (err) {
    toast('Re-render error: ' + err.message);
  } finally {
    btn.disabled = false;
    if (spinner) spinner.style.display = 'none';
    if (btnText) btnText.textContent = 'Apply & Re-render';
  }
};

/* ==========================================================================
   Floating Storage & Retention Manager
   ========================================================================== */
let storageDetailsData = null;

async function fetchStorageDetails() {
  try {
    const res = await api('/api/storage-details');
    storageDetailsData = res;
    updateStorageBadge(res);
    return res;
  } catch (e) {
    console.error('Failed to load storage details:', e);
    return null;
  }
}

function updateStorageBadge(data) {
  const badge = $('#storageTotalBadge');
  if (!badge || !data) return;
  const gb = data.total_gb;
  if (gb >= 1) {
    badge.textContent = `${gb.toFixed(1)} GB`;
  } else {
    badge.textContent = `${data.total_mb.toFixed(0)} MB`;
  }
}

function openStorageModal() {
  const modal = $('#storageModalOverlay');
  if (modal) modal.style.display = 'flex';
  renderStorageModal();
  fetchStorageDetails().then(d => {
    if (d) renderStorageModal();
  });
}

function closeStorageModal() {
  const modal = $('#storageModalOverlay');
  if (modal) modal.style.display = 'none';
}

function renderStorageModal() {
  const d = storageDetailsData;
  if (!d) return;

  // Header metrics
  const totalVal = $('#storageTotalVal');
  const totalSub = $('#storageTotalSub');
  const freeVal = $('#storageDiskFreeVal');
  const freeSub = $('#storageDiskTotalSub');
  const countBadge = $('#storageProjectsCount');

  if (totalVal) {
    totalVal.textContent = d.total_gb >= 1 ? `${d.total_gb.toFixed(2)} GB` : `${d.total_mb.toFixed(1)} MB`;
  }
  if (totalSub) {
    totalSub.textContent = `Across ${d.total_projects} projects (${d.total_clips} clips rendered)`;
  }
  if (freeVal) {
    freeVal.textContent = `${d.disk_free_gb.toFixed(1)} GB free`;
  }
  if (freeSub) {
    freeSub.textContent = `of ${d.disk_total_gb.toFixed(1)} GB disk capacity`;
  }
  if (countBadge) {
    countBadge.textContent = `${d.total_projects} project${d.total_projects === 1 ? '' : 's'}`;
  }

  // Render projects list
  const container = $('#storageProjectsContainer');
  if (!container) return;

  if (!d.projects || d.projects.length === 0) {
    container.innerHTML = `
      <div style="text-align: center; padding: 40px 20px; color: rgba(242, 239, 255, 0.4); font-family: var(--d);">
        <p style="font-size: 1.1rem; margin-bottom: 8px;">No projects currently stored.</p>
        <p style="font-size: 0.85rem;">Ingest a stream above to create clips and manage storage.</p>
      </div>
    `;
    return;
  }

  container.innerHTML = d.projects.map(p => {
    const isExp = p.is_expired;
    const retLabel = isExp ? 'EXPIRED (Pending purge)' : `${p.days_until_retention}d until auto-purge`;
    const retClass = isExp ? 'retention-tag expired' : 'retention-tag';
    const totalDisplay = p.total_mb >= 1000 ? `${(p.total_mb / 1024).toFixed(2)} GB` : `${p.total_mb.toFixed(1)} MB`;
    const srcDisplay = p.source_mb >= 1000 ? `${(p.source_mb / 1024).toFixed(2)} GB` : `${p.source_mb.toFixed(1)} MB`;
    const clipsDisplay = p.clips_mb >= 1000 ? `${(p.clips_mb / 1024).toFixed(2)} GB` : `${p.clips_mb.toFixed(1)} MB`;

    const clipsRows = (p.clips || []).map(c => `
      <div class="clip-row-item">
        <div class="clip-row-left">
          <span class="clip-id-badge">${esc(c.clip_id)}</span>
          <span class="clip-title-text" title="${esc(c.title)}">${esc(c.title)}</span>
          <span class="clip-status-pill ${esc(c.status)}">${esc(c.status)}</span>
        </div>
        <div class="clip-row-right">
          <span class="clip-size-text">${c.size_mb.toFixed(1)} MB</span>
          <button class="btn-delete-single-clip" data-vid="${esc(p.video_id)}" data-cid="${esc(c.clip_id)}" data-title="${esc(c.title)}" data-sz="${c.size_mb.toFixed(1)}" title="Delete this clip permanently">
            🗑️ Delete
          </button>
        </div>
      </div>
    `).join('');

    return `
      <div class="proj-card" data-vid="${esc(p.video_id)}">
        <div class="proj-card-top">
          <div class="proj-card-title-col">
            <div class="proj-title">${esc(p.title)}</div>
            <div class="proj-meta-row">
              <span>ID: ${esc(p.video_id)}</span>
              <span>•</span>
              <span>Created ${esc(p.created_at)} (${p.age_days}d ago)</span>
              <span>•</span>
              <span class="${retClass}">${retLabel}</span>
            </div>
          </div>
          <div class="proj-card-size-col">
            <span class="proj-size-val">${totalDisplay}</span>
            <span class="proj-size-label">total</span>
          </div>
        </div>

        <div class="proj-card-content">
          <div class="proj-assets-breakdown">
            <div class="asset-box">
              <div class="asset-info">
                <span class="asset-name">Raw Source VOD</span>
                <span class="asset-size">${p.has_source ? `${srcDisplay} (source.mp4 & audio.wav)` : 'Purged / Not Present'}</span>
              </div>
              <button class="btn-purge-asset btn-purge-source" data-vid="${esc(p.video_id)}" data-title="${esc(p.title)}" data-sz="${srcDisplay}" ${!p.has_source ? 'disabled title="Raw source is already deleted"' : 'title="Delete raw source to save disk space"'}>
                🗑️ Delete Raw Source
              </button>
            </div>
            <div class="asset-box">
              <div class="asset-info">
                <span class="asset-name">Rendered Clips (${p.clips_count})</span>
                <span class="asset-size">${clipsDisplay} total rendered video</span>
              </div>
              <button class="btn-purge-asset btn-purge-project-clips" data-vid="${esc(p.video_id)}" data-title="${esc(p.title)}" data-count="${p.clips_count}" ${p.clips_count === 0 ? 'disabled' : ''} title="Permanently delete all rendered clips for this project">
                🗑️ Delete All Clips
              </button>
            </div>
          </div>

          ${(p.clips && p.clips.length > 0) ? `
            <div>
              <button class="clips-accordion-toggle" type="button" data-vid="${esc(p.video_id)}">
                <span>View individual clips (${p.clips.length})</span>
                <span class="caret">▼</span>
              </button>
              <div class="clips-sub-table" id="clipsSub_${esc(p.video_id)}" style="display:none;">
                ${clipsRows}
              </div>
            </div>
          ` : ''}
        </div>
      </div>
    `;
  }).join('');

  // Wire accordion toggles
  $$('.clips-accordion-toggle', container).forEach(btn => {
    btn.onclick = () => {
      const vid = btn.dataset.vid;
      const sub = $(`#clipsSub_${vid}`, container);
      if (!sub) return;
      const isOpen = sub.style.display !== 'none';
      sub.style.display = isOpen ? 'none' : 'flex';
      btn.classList.toggle('open', !isOpen);
    };
  });

  // Wire single clip deletion
  $$('.btn-delete-single-clip', container).forEach(btn => {
    btn.onclick = async () => {
      const vid = btn.dataset.vid;
      const cid = btn.dataset.cid;
      const title = btn.dataset.title;
      const sz = btn.dataset.sz;
      if (!confirm(`Permanently delete clip "${title}" (${cid}, ${sz} MB)? This cannot be undone.`)) return;

      btn.disabled = true;
      btn.textContent = 'Deleting...';
      try {
        const res = await post('/api/delete-clip', { video_id: vid, clip_id: cid });
        if (res.success) {
          toast(`Clip ${cid} deleted (${res.freed_mb} MB freed)`);
          await fetchStorageDetails();
          renderStorageModal();
          if (S.vid === vid && typeof loadProjectClips === 'function') {
            loadProjectClips(vid);
          }
          if (typeof loadProjects === 'function') {
            loadProjects();
          }
        } else {
          toast('Failed to delete clip: ' + (res.error || 'Unknown error'));
        }
      } catch (e) {
        toast('Delete failed: ' + e.message);
      }
    };
  });

  // Wire source VOD deletion
  $$('.btn-purge-source', container).forEach(btn => {
    btn.onclick = async () => {
      const vid = btn.dataset.vid;
      const title = btn.dataset.title;
      const sz = btn.dataset.sz;
      if (!confirm(`Delete raw source VOD for "${title}"?\n\nThis will free ${sz} of disk space. All rendered clips and transcripts will be kept intact.`)) return;

      btn.disabled = true;
      btn.textContent = 'Deleting...';
      try {
        const res = await post('/api/delete-source', { video_id: vid });
        if (res.success) {
          toast(`Raw source deleted (${res.freed_mb} MB freed)`);
          await fetchStorageDetails();
          renderStorageModal();
          if (typeof loadProjects === 'function') loadProjects();
        } else {
          toast('Failed to delete source: ' + (res.error || 'Unknown error'));
        }
      } catch (e) {
        toast('Delete error: ' + e.message);
      }
    };
  });

  // Wire all clips in project deletion
  $$('.btn-purge-project-clips', container).forEach(btn => {
    btn.onclick = async () => {
      const vid = btn.dataset.vid;
      const title = btn.dataset.title;
      const count = btn.dataset.count;
      if (!confirm(`⚠️ Delete ALL ${count} clips for "${title}"?\n\nThis will permanently delete all rendered MP4s and subtitle files for this project. This cannot be undone.`)) return;

      btn.disabled = true;
      btn.textContent = 'Deleting...';
      try {
        const res = await post('/api/delete-all-clips', { video_id: vid });
        if (res.success) {
          toast(`All clips deleted for ${vid} (${res.freed_mb} MB freed)`);
          await fetchStorageDetails();
          renderStorageModal();
          if (S.vid === vid && typeof loadProjectClips === 'function') {
            loadProjectClips(vid);
          }
          if (typeof loadProjects === 'function') loadProjects();
        } else {
          toast('Failed: ' + (res.error || 'Unknown error'));
        }
      } catch (e) {
        toast('Delete error: ' + e.message);
      }
    };
  });
}

function initStorageManager() {
  const openBtn = $('#storageSettingsBtn');
  const closeBtn = $('#storageCloseBtn');
  const overlay = $('#storageModalOverlay');
  const runRetentionBtn = $('#storageRunRetentionBtn');
  const deleteAllBtn = $('#storageDeleteAllClipsBtn');

  if (openBtn) openBtn.onclick = () => openStorageModal();
  if (closeBtn) closeBtn.onclick = () => closeStorageModal();

  if (overlay) {
    overlay.onclick = e => {
      if (e.target === overlay) closeStorageModal();
    };
  }

  window.addEventListener('keydown', e => {
    if (e.key === 'Escape' && overlay && overlay.style.display !== 'none') {
      closeStorageModal();
    }
  });

  // Global: Enforce 30-Day Purge Now
  if (runRetentionBtn) {
    runRetentionBtn.onclick = async () => {
      if (!confirm('Run the 30-day lifecycle retention purge now?\n\nAny project source VODs or clips older than 30 days will be permanently deleted.')) return;
      runRetentionBtn.disabled = true;
      runRetentionBtn.textContent = 'Enforcing...';
      try {
        const res = await post('/api/retention-clean', { days: 30 });
        if (res.purged_count > 0) {
          toast(`Purged ${res.purged_count} expired project(s), freed ${res.total_freed_mb} MB!`);
        } else {
          toast('All projects are within the 30-day lifecycle. No files expired.');
        }
        await fetchStorageDetails();
        renderStorageModal();
        if (typeof loadProjects === 'function') loadProjects();
      } catch (e) {
        toast('Retention clean failed: ' + e.message);
      } finally {
        runRetentionBtn.disabled = false;
        runRetentionBtn.textContent = '⚡ Enforce 30-Day Purge Now';
      }
    };
  }

  // Global: Delete ALL clips across ALL projects
  if (deleteAllBtn) {
    deleteAllBtn.onclick = async () => {
      const confirmPrompt = prompt('⚠️ DANGER: This will permanently delete ALL rendered clips across ALL projects!\n\nType DELETE to confirm:');
      if (confirmPrompt !== 'DELETE') {
        if (confirmPrompt !== null) toast('Bulk deletion cancelled (confirmation mismatch).');
        return;
      }

      deleteAllBtn.disabled = true;
      deleteAllBtn.textContent = 'Purging all clips...';
      try {
        const res = await post('/api/delete-all-clips', { video_id: 'all' });
        if (res.success) {
          toast(`All clips purged! Freed ${res.freed_mb} MB (${res.freed_gb} GB)`);
          await fetchStorageDetails();
          renderStorageModal();
          if (typeof loadProjects === 'function') loadProjects();
          if (S.vid && typeof loadProjectClips === 'function') loadProjectClips(S.vid);
        } else {
          toast('Failed: ' + (res.error || 'Unknown error'));
        }
      } catch (e) {
        toast('Bulk delete error: ' + e.message);
      } finally {
        deleteAllBtn.disabled = false;
        deleteAllBtn.textContent = '🗑️ Delete All Clips (All Projects)';
      }
    };
  }

  // Initial fetch for badge
  fetchStorageDetails();
}

// Load workspace streams on startup
loadProjects();
initStorageManager();

/* Background Animation */
(() => {
  const c = $('#bgc'), x = c.getContext('2d'), L = 18, still = matchMedia('(prefers-reduced-motion:reduce)').matches;
  let W, H, D, last = 0;
  const rs = () => {
    D = Math.min(devicePixelRatio || 1, 1.5);
    W = c.width = innerWidth * D;
    H = c.height = innerHeight * D;
    if (still) draw(3);
  };
  const nz = (u, v) => Math.sin(u * 1.3 + v) * .5 + Math.sin(u * 2.9 - v * 1.7) * .3 + Math.sin(u * 6.1 + v * 2.3) * .2;
  function draw(t) {
    x.clearRect(0, 0, W, H);
    const top = H * .4, st = (H - top) / L, peaks = [];
    for (let i = 0; i < L; i++) {
      const d = i / (L - 1), y0 = top + i * st + st * .5, amp = st * (2.4 + d * 3), p = new Path2D();
      let mn = 1e9, mx = 0;
      for (let k = 0; k <= 110; k++) {
        const px = k * W / 110, u = px / W, env = Math.exp(-Math.pow((u - .5) * 3, 2)), n = Math.abs(nz(u * 9, t * .5 - i * .7)) * (.3 + .7 * env), y = y0 - n * amp * (.4 + env);
        k ? p.lineTo(px, y) : p.moveTo(px, y);
        if (y < mn) { mn = y; mx = px; }
      }
      const f = new Path2D(p);
      f.lineTo(W, H);
      f.lineTo(0, H);
      f.closePath();
      x.fillStyle = '#05061A';
      x.fill(f);
      x.strokeStyle = `hsla(${236 - d * 14},100%,${58 + d * 24}%,${.22 + d * .55})`;
      x.lineWidth = (1 + d * 1.3) * D;
      x.stroke(p);
      if (i > L - 5 && y0 - mn > amp * .8) peaks.push([mx, mn]);
    }
    x.fillStyle = '#FFE14A';
    peaks.forEach(([px, py]) => {
      x.beginPath();
      x.arc(px, py, 3 * D, 0, 7);
      x.fill();
      x.fillRect(px - .5 * D, py, D, 14 * D);
    });
    const hx = ((t * .035) % 1) * W, g = x.createLinearGradient(0, 0, 0, H);
    g.addColorStop(0, 'rgba(255,225,74,0)');
    g.addColorStop(.55, 'rgba(255,225,74,.3)');
    g.addColorStop(1, 'rgba(255,225,74,0)');
    x.fillStyle = g;
    x.fillRect(hx, 0, D, H);
  }
  rs();
  addEventListener('resize', rs);
  if (!still) (function f(ms) {
    requestAnimationFrame(f);
    if (ms - last < 33) return;
    last = ms;
    draw(ms/1000);
  })(0);
})();
