"""Review stage: Generate a self-contained static HTML review dashboard with keyboard shortcuts and export."""

import json
from pathlib import Path
from typing import List
from clipforge.cache import CacheManager
from clipforge.models import ClipItem

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, minimum-scale=1.0, shrink-to-fit=no" />
  <title>ClipForge Review - {{ video_id }}</title>
  <style>
    :root {
      --bg: #0f1117;
      --card-bg: #1a1d26;
      --accent: #ffd60a;
      --accent-hover: #ffc300;
      --text: #f0f2f5;
      --text-muted: #9ba1b0;
      --border: #2a2e3d;
      --success: #10b981;
      --danger: #ef4444;
    }
    html {
      overflow-x: hidden;
      max-width: 100%;
    }
    body {
      overflow-x: hidden;
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      display: flex;
      flex-direction: column;
      height: 100vh;
    }
    header {
      background: var(--card-bg);
      border-bottom: 1px solid var(--border);
      padding: 1rem 2rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    h1 {
      margin: 0;
      font-size: 1.25rem;
      font-weight: 700;
      color: var(--accent);
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }
    .badge {
      background: var(--border);
      color: var(--text);
      font-size: 0.75rem;
      padding: 0.2rem 0.6rem;
      border-radius: 999px;
      font-weight: 500;
    }
    .header-actions {
      display: flex;
      gap: 0.75rem;
    }
    button {
      cursor: pointer;
      font-family: inherit;
      font-weight: 600;
      border: none;
      border-radius: 6px;
      padding: 0.5rem 1rem;
      transition: all 0.15s ease;
    }
    .btn-export {
      background: var(--accent);
      color: #000;
    }
    .btn-export:hover {
      background: var(--accent-hover);
    }
    main {
      flex: 1;
      display: grid;
      grid-template-columns: 380px 1fr;
      overflow: hidden;
    }
    .clip-list {
      background: var(--card-bg);
      border-right: 1px solid var(--border);
      overflow-y: auto;
      padding: 1rem;
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
    }
    .clip-card {
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 0.85rem;
      cursor: pointer;
      transition: all 0.15s;
    }
    .clip-card:hover {
      border-color: var(--accent);
    }
    .clip-card.active {
      border-color: var(--accent);
      background: #232733;
    }
    .clip-card.approved {
      border-left: 4px solid var(--success);
    }
    .clip-card.rejected {
      border-left: 4px solid var(--danger);
      opacity: 0.5;
    }
    .card-header {
      display: flex;
      justify-content: space-between;
      margin-bottom: 0.4rem;
    }
    .score-badge {
      color: var(--accent);
      font-weight: 700;
      font-size: 0.9rem;
    }
    .card-title {
      font-size: 0.95rem;
      font-weight: 600;
      line-height: 1.3;
      margin-bottom: 0.3rem;
    }
    .card-meta {
      font-size: 0.75rem;
      color: var(--text-muted);
    }
    .clip-preview-panel {
      display: flex;
      gap: 2rem;
      padding: 2rem;
      overflow-y: auto;
      align-items: flex-start;
      justify-content: center;
    }
    .video-container {
      width: 360px;
      height: 640px;
      background: #000;
      border-radius: 12px;
      overflow: hidden;
      box-shadow: 0 10px 30px rgba(0,0,0,0.5);
      border: 1px solid var(--border);
      flex-shrink: 0;
    }
    video {
      width: 100%;
      height: 100%;
      object-fit: cover;
    }
    .details-panel {
      max-width: 550px;
      flex: 1;
      display: flex;
      flex-direction: column;
      gap: 1.25rem;
    }
    .section-title {
      font-size: 0.8rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--text-muted);
      margin-bottom: 0.4rem;
      font-weight: 700;
    }
    .title-input {
      width: 100%;
      background: var(--card-bg);
      border: 1px solid var(--border);
      color: var(--text);
      padding: 0.75rem;
      border-radius: 6px;
      font-size: 1.1rem;
      font-weight: 600;
      box-sizing: border-box;
    }
    .title-input:focus {
      outline: 2px solid var(--accent);
    }
    .alt-titles {
      display: flex;
      flex-direction: column;
      gap: 0.5rem;
    }
    .alt-pill {
      background: var(--card-bg);
      border: 1px solid var(--border);
      padding: 0.5rem 0.75rem;
      border-radius: 6px;
      font-size: 0.85rem;
      cursor: pointer;
    }
    .alt-pill:hover {
      border-color: var(--accent);
    }
    .text-block {
      background: var(--card-bg);
      border: 1px solid var(--border);
      padding: 0.85rem;
      border-radius: 6px;
      line-height: 1.5;
      font-size: 0.9rem;
    }
    .review-buttons {
      display: flex;
      gap: 1rem;
      margin-top: 1rem;
    }
    .btn-approve {
      background: var(--success);
      color: #fff;
      flex: 1;
      padding: 0.85rem;
      font-size: 1rem;
    }
    .btn-reject {
      background: var(--danger);
      color: #fff;
      flex: 1;
      padding: 0.85rem;
      font-size: 1rem;
    }
    .shortcuts-hint {
      margin-top: 1rem;
      font-size: 0.8rem;
      color: var(--text-muted);
      text-align: center;
    }
    kbd {
      background: var(--border);
      padding: 0.2rem 0.4rem;
      border-radius: 4px;
      font-family: inherit;
    }
  </style>
</head>
<body>
  <header>
    <h1><span>🎬</span> ClipForge Review Dashboard</h1>
    <span class="badge">{{ video_id }}</span>
    <div class="header-actions">
      <button class="btn-export" onclick="exportApproved()">Export Approved Clips</button>
    </div>
  </header>

  <main>
    <div class="clip-list" id="clipList"></div>

    <div class="clip-preview-panel">
      <div class="video-container">
        <video id="player" controls autoplay loop playsinline></video>
      </div>

      <div class="details-panel">
        <div>
          <div class="section-title">Clip Title (Edit or pick alternative)</div>
          <input type="text" id="titleInput" class="title-input" oninput="updateActiveTitle(this.value)">
        </div>

        <div>
          <div class="section-title">Alternative Titles (Click to apply)</div>
          <div class="alt-titles" id="altTitles"></div>
        </div>

        <div>
          <div class="section-title">Why This Moment Goes Viral</div>
          <div class="text-block" id="whyViral"></div>
        </div>

        <div>
          <div class="section-title">Hook Line (First 2-3s overlay)</div>
          <div class="text-block" id="hookLine" style="color: var(--accent); font-weight: 600;"></div>
        </div>

        <div class="review-buttons">
          <button class="btn-approve" onclick="toggleStatus('approved')">Approve [A]</button>
          <button class="btn-reject" onclick="toggleStatus('rejected')">Reject [R]</button>
        </div>

        <div class="shortcuts-hint">
          Shortcuts: <kbd>A</kbd> Approve | <kbd>R</kbd> Reject | <kbd>↑</kbd> <kbd>↓</kbd> Select Clip | <kbd>Space</kbd> Play/Pause
        </div>
      </div>
    </div>
  </main>

  <script>
    const clips = {{ clips_json | safe }};
    let currentIndex = 0;
    const states = {}; // { id: 'approved' | 'rejected' }

    clips.forEach(c => states[c.id] = 'pending');

    function renderList() {
      const listEl = document.getElementById('clipList');
      listEl.innerHTML = '';
      clips.forEach((c, idx) => {
        const card = document.createElement('div');
        card.className = `clip-card ${idx === currentIndex ? 'active' : ''} ${states[c.id]}`;
        card.onclick = () => selectClip(idx);
        card.innerHTML = `
          <div class="card-header">
            <span style="font-weight: 700; color: var(--text-muted);">${c.id}</span>
            <span class="score-badge">${c.score}/100</span>
          </div>
          <div class="card-title">${c.title}</div>
          <div class="card-meta">${(c.end - c.start).toFixed(1)}s • ${c.category} • ${states[c.id].toUpperCase()}</div>
        `;
        listEl.appendChild(card);
      });
    }

    function selectClip(idx) {
      if (idx < 0 || idx >= clips.length) return;
      currentIndex = idx;
      const c = clips[currentIndex];
      const player = document.getElementById('player');
      player.src = `${c.id}.mp4`;
      player.play().catch(() => {});

      document.getElementById('titleInput').value = c.title;
      document.getElementById('whyViral').textContent = c.why_viral;
      document.getElementById('hookLine').textContent = c.hook_line || 'Watch this moment';

      const altsEl = document.getElementById('altTitles');
      altsEl.innerHTML = '';
      (c.title_alts || []).forEach(alt => {
        const pill = document.createElement('div');
        pill.className = 'alt-pill';
        pill.textContent = alt;
        pill.onclick = () => {
          c.title = alt;
          document.getElementById('titleInput').value = alt;
          renderList();
        };
        altsEl.appendChild(pill);
      });

      renderList();
    }

    function updateActiveTitle(val) {
      clips[currentIndex].title = val;
      renderList();
    }

    function toggleStatus(status) {
      const c = clips[currentIndex];
      states[c.id] = states[c.id] === status ? 'pending' : status;
      renderList();
      // Auto advance to next clip
      if (currentIndex < clips.length - 1) {
        selectClip(currentIndex + 1);
      }
    }

    function exportApproved() {
      const approved = clips.filter(c => states[c.id] === 'approved');
      const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(approved, null, 2));
      const dlAnchor = document.createElement('a');
      dlAnchor.setAttribute("href", dataStr);
      dlAnchor.setAttribute("download", `approved_${clips[0] ? clips[0].id.split('_')[0] : 'clips'}.json`);
      document.body.appendChild(dlAnchor);
      dlAnchor.click();
      dlAnchor.remove();
    }

    window.addEventListener('keydown', (e) => {
      if (e.target.tagName === 'INPUT') return;
      if (e.key === 'a' || e.key === 'A') toggleStatus('approved');
      if (e.key === 'r' || e.key === 'R') toggleStatus('rejected');
      if (e.key === 'ArrowUp' && currentIndex > 0) selectClip(currentIndex - 1);
      if (e.key === 'ArrowDown' && currentIndex < clips.length - 1) selectClip(currentIndex + 1);
      if (e.key === ' ') {
        e.preventDefault();
        const p = document.getElementById('player');
        p.paused ? p.play() : p.pause();
      }
    });

    if (clips.length > 0) {
      selectClip(0);
    }
  </script>
</body>
</html>
"""


def generate_review_html(
    clips: List[ClipItem],
    cache: CacheManager,
    out_dir: Path,
) -> Path:
    """Generate static review.html in both cache/ and out/<video_id>/."""
    clips_data = [c.model_dump() for c in clips]
    rendered_html = (
        HTML_TEMPLATE.replace("{{ video_id }}", cache.video_id)
        .replace("{{ clips_json | safe }}", json.dumps(clips_data))
    )

    # Write in out/<video_id>/review.html so it sits right next to the mp4 files
    out_html = out_dir / "review.html"
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(rendered_html)

    # Also write in cache/<video_id>/review.html
    cache_html = cache.path("review.html")
    with open(cache_html, "w", encoding="utf-8") as f:
        f.write(rendered_html)

    return out_html
