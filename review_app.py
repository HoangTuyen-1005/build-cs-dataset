"""Small local app for reviewing ASR transcripts in metadata.csv.

Run: python review_app.py
Then open the address printed in the terminal. No third-party packages or
online account are required.
"""

from __future__ import annotations

import argparse
import csv
import json
import mimetypes
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


APP_HTML = r"""<!doctype html>
<html lang="vi">
<head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
  <title>ASR Transcript Reviewer</title>
  <style>
    :root {
      --bg-app: #0f172a;
      --bg-sidebar: #181825;
      --bg-sidebar-hover: #313244;
      --bg-sidebar-active: #45475a;
      --border-sidebar: #28283d;
      --text-sidebar: #cdd6f4;
      --text-sidebar-muted: #8890a5;
      --bg-main: #f8fafc;
      --text-main: #0f172a;
      --border-main: #e2e8f0;
      --accent: #2563eb;
      --accent-hover: #1d4ed8;
      --accent-active: #89b4fa;
      --danger: #ef4444;
      --danger-hover: #dc2626;
    }
    * { box-sizing: border-box; }
    html, body { height: 100%; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: var(--bg-main); color: var(--text-main); overflow: hidden; }
    #app-container { display: flex; height: 100vh; width: 100vw; }

    /* VS Code Style Sidebar */
    #sidebar {
      width: 320px;
      min-width: 250px;
      max-width: 440px;
      background: var(--bg-sidebar);
      color: var(--text-sidebar);
      border-right: 1px solid var(--border-sidebar);
      display: flex;
      flex-direction: column;
      flex-shrink: 0;
      transition: margin-left 0.2s cubic-bezier(0.4, 0, 0.2, 1);
      user-select: none;
      z-index: 20;
    }
    #sidebar.collapsed {
      margin-left: -320px;
    }
    .sidebar-header {
      padding: 12px 14px 10px;
      border-bottom: 1px solid var(--border-sidebar);
      background: #14141f;
    }
    .sidebar-title-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 8px;
    }
    .sidebar-title {
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: #9399b2;
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .icon-btn {
      background: transparent;
      border: 0;
      color: #9399b2;
      cursor: pointer;
      padding: 4px 6px;
      border-radius: 4px;
      font-size: 13px;
      line-height: 1;
    }
    .icon-btn:hover {
      background: var(--bg-sidebar-hover);
      color: #ffffff;
    }
    .search-box {
      position: relative;
      display: flex;
      align-items: center;
    }
    .search-input {
      width: 100%;
      background: #11111b;
      border: 1px solid #313244;
      border-radius: 6px;
      color: #cdd6f4;
      padding: 6px 26px 6px 9px;
      font-size: 12px;
    }
    .search-input:focus {
      outline: none;
      border-color: #89b4fa;
    }
    .clear-search-btn {
      position: absolute;
      right: 6px;
      background: transparent;
      border: 0;
      color: #6c7086;
      cursor: pointer;
      font-size: 11px;
      padding: 2px 4px;
    }
    .sidebar-stats {
      padding: 6px 14px;
      font-size: 11px;
      color: var(--text-sidebar-muted);
      border-bottom: 1px solid var(--border-sidebar);
      display: flex;
      align-items: center;
      justify-content: space-between;
      background: #161623;
    }
    .tree-container {
      flex: 1;
      overflow-y: auto;
      overflow-x: hidden;
      padding: 6px 0 16px;
    }
    .tree-container::-webkit-scrollbar { width: 6px; }
    .tree-container::-webkit-scrollbar-thumb { background: #313244; border-radius: 3px; }

    /* Tree View Items */
    .tree-item {
      display: flex;
      align-items: center;
      padding: 5px 12px;
      cursor: pointer;
      font-size: 12.5px;
      color: var(--text-sidebar);
      gap: 6px;
      transition: background 0.1s;
      white-space: nowrap;
      text-overflow: ellipsis;
      overflow: hidden;
    }
    .tree-item:hover {
      background: var(--bg-sidebar-hover);
    }
    .tree-item.active {
      background: var(--bg-sidebar-active);
      color: #89b4fa;
      font-weight: 600;
      border-left: 3px solid #89b4fa;
    }
    .tree-caret {
      display: inline-block;
      width: 12px;
      text-align: center;
      font-size: 9px;
      color: #7f849c;
      transition: transform 0.12s;
    }
    .tree-caret.expanded {
      transform: rotate(90deg);
    }
    .tree-icon {
      font-size: 13px;
      line-height: 1;
      flex-shrink: 0;
    }
    .tree-label {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      flex: 1;
    }
    .tree-badge {
      margin-left: auto;
      font-size: 10.5px;
      background: #313244;
      color: #a6adc8;
      padding: 1px 6px;
      border-radius: 9px;
      flex-shrink: 0;
    }
    .tree-item.active .tree-badge {
      background: #89b4fa;
      color: #11111b;
      font-weight: 700;
    }
    .tree-children {
      display: none;
    }
    .tree-children.expanded {
      display: block;
    }
    .tree-user { padding-left: 24px; }
    .tree-chunk {
      padding-left: 42px;
      font-size: 11.5px;
      color: #9399b2;
    }
    .tree-chunk.active-chunk {
      background: rgba(137, 180, 250, 0.18);
      color: #89b4fa;
      font-weight: 600;
      border-left: 3px solid #89b4fa;
    }

    /* Main Review Panel */
    #main {
      flex: 1;
      height: 100%;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      padding: 24px 36px;
    }
    .main-inner {
      max-width: 920px;
      width: 100%;
      margin: 0 auto;
    }
    header, .actions, .details { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
    header { justify-content: space-between; margin-bottom: 8px; }
    h1 { margin: 0; font-size: 1.45rem; display: flex; align-items: center; gap: 10px; }
    button { border: 0; border-radius: 7px; padding: 9px 15px; font-size: 0.95rem; cursor: pointer; background: #e2e8f0; font-weight: 500; transition: background 0.12s; }
    button:hover { background: #cbd5e1; }
    #save { background: var(--accent); color: white; }
    #save:hover { background: var(--accent-hover); }
    #delete { background: var(--danger); color: white; margin-left: auto; }
    #delete:hover { background: var(--danger-hover); }
    audio { width: 100%; margin: 18px 0 14px; }
    textarea { width: 100%; min-height: 230px; box-sizing: border-box; padding: 14px; font: 1.05rem/1.55 system-ui, sans-serif; border: 1px solid #94a3b8; border-radius: 8px; transition: border-color .15s, box-shadow .15s; background: #fff; }
    textarea:focus { border-color: var(--accent); outline: none; box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.2); }
    .details { color: #475569; font-size: .88rem; margin: 8px 0 16px; }
    #status { min-height: 1.5em; color: #047857; font-weight: 500; }
    #status.error { color: #b91c1c; }
    .player-tools { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin: 4px 0 12px; }
    .player-tools input { width: 170px; }
    kbd { border: 1px solid #cbd5e1; border-radius: 4px; padding: 1px 5px; background: #ffffff; font-size: 0.85em; }

    /* Scope Banner */
    .scope-banner {
      display: flex;
      align-items: center;
      justify-content: space-between;
      background: #eff6ff;
      border: 1px solid #bfdbfe;
      padding: 9px 14px;
      border-radius: 8px;
      font-size: 0.88rem;
      color: #1e40af;
      margin: 8px 0 14px;
    }
    .scope-banner .scope-text {
      display: flex;
      align-items: center;
      gap: 6px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .scope-banner strong { color: #1e3a8a; }
    .btn-reset-filter {
      border: 0;
      border-radius: 5px;
      padding: 4px 9px;
      font-size: 0.8rem;
      cursor: pointer;
      background: #dbeafe;
      color: #1e40af;
      margin-left: 10px;
      flex-shrink: 0;
    }
    .btn-reset-filter:hover { background: #bfdbfe; }
    .counter-badge {
      font-size: 1.1rem;
      font-weight: 700;
      color: var(--accent);
      background: #dbeafe;
      padding: 4px 10px;
      border-radius: 6px;
    }
  </style>
</head>
<body>
<div id="app-container">
  <!-- Left Sidebar (VS Code Explorer style) -->
  <aside id="sidebar">
    <div class="sidebar-header">
      <div class="sidebar-title-row">
        <span class="sidebar-title">📁 EXPLORER : DATASET</span>
        <button id="btn-collapse-sidebar" class="icon-btn" title="Thu gọn sidebar (Ctrl+B)">◀</button>
      </div>
      <div class="search-box">
        <input id="search-input" class="search-input" type="text" placeholder="🔍 Lọc room, user ID..." spellcheck="false">
        <button id="clear-search" class="clear-search-btn" title="Xóa tìm kiếm" style="display:none;">✕</button>
      </div>
    </div>
    <div class="sidebar-stats" id="sidebar-stats">
      <span>Đang nạp danh sách...</span>
    </div>
    <div class="tree-container" id="tree-container">
      <!-- Generated tree view -->
    </div>
  </aside>

  <!-- Main Review Panel -->
  <main id="main">
    <div class="main-inner">
      <header>
        <h1>
          <button id="btn-open-sidebar" class="icon-btn" title="Mở sidebar (Ctrl+B)" style="display:none;">☰</button>
          ASR Transcript Reviewer
        </h1>
        <strong id="counter" class="counter-badge">0 / 0</strong>
      </header>

      <div class="scope-banner" id="scope-banner">
        <span class="scope-text" id="scope-path">📍 Đang xem: <strong>Tất cả dữ liệu</strong></span>
        <button id="reset-filter" class="btn-reset-filter" style="display:none;">✕ Xem tất cả</button>
      </div>

      <p class="details">Phím tắt: <kbd>Space</kbd> phát/dừng, <kbd>←</kbd>/<kbd>→</kbd> tua track, <kbd>[</kbd>/<kbd>]</kbd> câu trước/tiếp, <kbd>Esc</kbd> thoát sửa text, <kbd>Ctrl</kbd>+<kbd>S</kbd> lưu.</p>
      <div class="details" id="details"></div>
      <audio id="audio" controls preload="metadata"></audio>
      <div class="player-tools">
        <button id="rewind" title="Lùi theo bước tua đã chọn">↶ Tua lùi</button>
        <button id="forward" title="Tới theo bước tua đã chọn">Tua tới ↷</button>
        <label for="seek-step">Bước tua: <strong id="seek-value">5 giây</strong></label>
        <input id="seek-step" type="range" min="1" max="5" step="1" value="5" aria-label="Số giây mỗi lần tua">
        <label for="speed">Tốc độ: <strong id="speed-value">1.00×</strong></label>
        <input id="speed" type="range" min="0.5" max="2" step="0.05" value="1" aria-label="Tốc độ phát audio">
        <button id="normal-speed">1×</button>
      </div>
      <textarea id="text" spellcheck="false" placeholder="Chưa có transcript..."></textarea>
      <div class="actions" style="margin-top:16px">
        <button id="prev" title="Mẫu trước trong phạm vi hiện tại ([)">← Trước</button>
        <button id="save" title="Lưu transcript (Ctrl+S)">Lưu thay đổi</button>
        <button id="next" title="Mẫu tiếp theo trong phạm vi hiện tại (])">Tiếp →</button>
        <button id="delete" title="Xóa vĩnh viễn file audio này và loại khỏi metadata.csv">🗑️ Xóa mẫu này</button>
        <span id="status"></span>
      </div>
    </div>
  </main>
</div>

<script>
let allRows = [];
let scopedIndices = []; // Indices in allRows matching the active filter
let scopedIndex = 0;   // Position within scopedIndices
let dirty = false;
let currentBlobUrl = null;

// Filter state: { type: 'all' } | { type: 'room', roomId } | { type: 'user', roomId, userId }
let activeScope = { type: 'all', roomId: null, userId: null };
let expandedTreeKeys = new Set();
let searchQuery = '';

const $ = s => document.querySelector(s);
const status = (message, error=false) => {
  $('#status').textContent = message;
  $('#status').className = error ? 'error' : '';
};

function seekBy(seconds) {
  const audio = $('#audio');
  const duration = Number.isFinite(audio.duration) ? audio.duration : Infinity;
  const cur = audio.currentTime || 0;
  const target = Math.max(0, Math.min(duration, cur + seconds));
  audio.currentTime = target;
  status((seconds > 0 ? '⏩ Tua tới +' : '⏪ Tua lùi -') + Math.abs(seconds) + 's (' + target.toFixed(1) + 's' + (Number.isFinite(duration) ? ' / ' + duration.toFixed(1) + 's' : '') + ')');
}
function seekStep() { return Number($('#seek-step').value) || 5; }
function setSeekStep(value) {
  const seconds = Math.max(1, Math.min(5, Number(value) || 5));
  $('#seek-step').value = seconds;
  $('#seek-value').textContent = seconds + ' giây';
  try { localStorage.setItem('review_seek_step', seconds); } catch (e) {}
}
function getSpeed() { return Number($('#speed').value) || 1.0; }
function setSpeed(value) {
  const speed = Math.max(.5, Math.min(2, Number(value) || 1.0));
  const audio = $('#audio');
  audio.playbackRate = speed;
  audio.defaultPlaybackRate = speed;
  $('#speed').value = speed;
  $('#speed-value').textContent = speed.toFixed(2) + '×';
  try { localStorage.setItem('review_speed', speed); } catch (e) {}
}

// Compute filtered row indices based on activeScope
function updateScopedIndices() {
  scopedIndices = [];
  for (let i = 0; i < allRows.length; i++) {
    const r = allRows[i];
    if (activeScope.type === 'user') {
      if (r.room_id === activeScope.roomId && r.user_id === activeScope.userId) {
        scopedIndices.push(i);
      }
    } else if (activeScope.type === 'room') {
      if (r.room_id === activeScope.roomId) {
        scopedIndices.push(i);
      }
    } else {
      scopedIndices.push(i);
    }
  }

  if (scopedIndex >= scopedIndices.length) {
    scopedIndex = Math.max(0, scopedIndices.length - 1);
  }
}

function updateScopeUI() {
  const resetBtn = $('#reset-filter');
  const scopePath = $('#scope-path');
  const count = scopedIndices.length;

  if (activeScope.type === 'user') {
    scopePath.innerHTML = `📍 Phạm vi User: <strong>${activeScope.userId}</strong> (Phòng: <em>${activeScope.roomId.slice(0, 8)}...</em>)`;
    resetBtn.style.display = 'inline-block';
  } else if (activeScope.type === 'room') {
    scopePath.innerHTML = `📍 Phạm vi Phòng: <strong>${activeScope.roomId}</strong>`;
    resetBtn.style.display = 'inline-block';
  } else {
    scopePath.innerHTML = `📍 Phạm vi: <strong>Tất cả dữ liệu</strong>`;
    resetBtn.style.display = 'none';
  }

  $('#counter').textContent = count ? `${scopedIndex + 1} / ${count}` : '0 / 0';
  $('#prev').disabled = count <= 1 || scopedIndex === 0;
  $('#next').disabled = count <= 1 || scopedIndex === count - 1;
}

// Build hierarchical room & user data tree
function getHierarchy() {
  const roomsMap = new Map();
  allRows.forEach((r, idx) => {
    if (!roomsMap.has(r.room_id)) {
      roomsMap.set(r.room_id, {
        roomId: r.room_id,
        usersMap: new Map(),
        totalSamples: 0,
        indices: []
      });
    }
    const rm = roomsMap.get(r.room_id);
    rm.totalSamples++;
    rm.indices.push(idx);

    if (!rm.usersMap.has(r.user_id)) {
      rm.usersMap.set(r.user_id, {
        userId: r.user_id,
        roomId: r.room_id,
        samples: 0,
        indices: []
      });
    }
    const usr = rm.usersMap.get(r.user_id);
    usr.samples++;
    usr.indices.push(idx);
  });
  return roomsMap;
}

function renderSidebar() {
  const container = $('#tree-container');
  const roomsMap = getHierarchy();
  const q = searchQuery.toLowerCase().trim();

  // Sidebar stats
  let totalUsers = 0;
  roomsMap.forEach(rm => { totalUsers += rm.usersMap.size; });
  $('#sidebar-stats').innerHTML = `<span>${roomsMap.size} phòng • ${totalUsers} user • ${allRows.length} mẫu</span>`;

  let html = '';

  // Root item: "Tất cả dữ liệu"
  const isAllActive = activeScope.type === 'all';
  html += `
    <div class="tree-item ${isAllActive ? 'active' : ''}" data-type="all">
      <span class="tree-icon">🌟</span>
      <span class="tree-label"><strong>Tất cả dữ liệu</strong></span>
      <span class="tree-badge">${allRows.length}</span>
    </div>
  `;

  // Render Rooms & Users
  roomsMap.forEach((roomData, roomId) => {
    const roomKey = `room_${roomId}`;
    const isRoomActive = activeScope.type === 'room' && activeScope.roomId === roomId;
    const isCurrentRoom = (scopedIndices.length && allRows[scopedIndices[scopedIndex]]?.room_id === roomId);

    // Check filter match
    let roomMatches = !q || roomId.toLowerCase().includes(q);
    let matchingUsers = [];
    roomData.usersMap.forEach((userData, userId) => {
      const userMatches = !q || userId.toLowerCase().includes(q) || userData.indices.some(i => allRows[i].filename.toLowerCase().includes(q));
      if (userMatches) {
        matchingUsers.push(userData);
      }
    });

    if (q && !roomMatches && matchingUsers.length === 0) {
      return;
    }

    // Auto expand on search or active
    const isExpanded = q ? true : (expandedTreeKeys.has(roomKey) || isCurrentRoom);
    const shortRoomId = roomId.length > 20 ? roomId.slice(0, 18) + '...' : roomId;

    html += `
      <div class="tree-item tree-room ${isRoomActive ? 'active' : ''}" data-type="room" data-room="${roomId}">
        <span class="tree-caret ${isExpanded ? 'expanded' : ''}" data-toggle-key="${roomKey}">▶</span>
        <span class="tree-icon">📁</span>
        <span class="tree-label" title="${roomId}">${shortRoomId}</span>
        <span class="tree-badge">${roomData.totalSamples}</span>
      </div>
      <div class="tree-children ${isExpanded ? 'expanded' : ''}" id="children_${roomKey}">
    `;

    // Users under room
    const usersToRender = q ? matchingUsers : Array.from(roomData.usersMap.values());
    usersToRender.forEach(userData => {
      const userId = userData.userId;
      const userKey = `user_${roomId}_${userId}`;
      const isUserActive = activeScope.type === 'user' && activeScope.roomId === roomId && activeScope.userId === userId;
      const isCurrentUser = (scopedIndices.length && allRows[scopedIndices[scopedIndex]]?.user_id === userId && allRows[scopedIndices[scopedIndex]]?.room_id === roomId);
      const isUserExpanded = q ? true : (expandedTreeKeys.has(userKey) || isCurrentUser);

      html += `
        <div class="tree-item tree-user ${isUserActive ? 'active' : ''}" data-type="user" data-room="${roomId}" data-user="${userId}">
          <span class="tree-caret ${isUserExpanded ? 'expanded' : ''}" data-toggle-key="${userKey}">▶</span>
          <span class="tree-icon">👤</span>
          <span class="tree-label" title="User ID: ${userId}">${userId}</span>
          <span class="tree-badge">${userData.samples}</span>
        </div>
        <div class="tree-children ${isUserExpanded ? 'expanded' : ''}" id="children_${userKey}">
      `;

      // Chunks under user
      userData.indices.forEach(idx => {
        const row = allRows[idx];
        const isCurrentChunk = (scopedIndices.length && scopedIndices[scopedIndex] === idx);
        const chunkName = row.filename;

        if (q && !chunkName.toLowerCase().includes(q) && !userId.toLowerCase().includes(q) && !roomId.toLowerCase().includes(q)) {
          return;
        }

        html += `
          <div class="tree-item tree-chunk ${isCurrentChunk ? 'active-chunk' : ''}" data-type="chunk" data-index="${idx}" title="${row.audio_path}">
            <span class="tree-icon">🎵</span>
            <span class="tree-label">${chunkName}</span>
            <span class="tree-badge" style="background:transparent; color:#6c7086;">${row.duration || ''}s</span>
          </div>
        `;
      });

      html += `</div>`; // Close user children
    });

    html += `</div>`; // Close room children
  });

  container.innerHTML = html;

  // Add click handlers for tree items
  container.querySelectorAll('.tree-item').forEach(el => {
    el.onclick = (e) => {
      // If clicking caret, toggle collapse
      if (e.target.classList.contains('tree-caret')) {
        e.stopPropagation();
        const key = e.target.getAttribute('data-toggle-key');
        if (expandedTreeKeys.has(key)) {
          expandedTreeKeys.delete(key);
        } else {
          expandedTreeKeys.add(key);
        }
        renderSidebar();
        return;
      }

      const type = el.getAttribute('data-type');
      if (type === 'all') {
        setScope({ type: 'all' });
      } else if (type === 'room') {
        const roomId = el.getAttribute('data-room');
        setScope({ type: 'room', roomId });
      } else if (type === 'user') {
        const roomId = el.getAttribute('data-room');
        const userId = el.getAttribute('data-user');
        setScope({ type: 'user', roomId, userId });
      } else if (type === 'chunk') {
        const targetIdx = Number(el.getAttribute('data-index'));
        const row = allRows[targetIdx];
        if (row) {
          // Switch to this user's scope and select this chunk
          activeScope = { type: 'user', roomId: row.room_id, userId: row.user_id };
          updateScopedIndices();
          scopedIndex = Math.max(0, scopedIndices.indexOf(targetIdx));
          render();
        }
      }
    };
  });

  // Auto scroll active chunk into view if present
  const activeEl = container.querySelector('.tree-chunk.active-chunk');
  if (activeEl) {
    activeEl.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }
}

function setScope(newScope) {
  activeScope = newScope;
  scopedIndex = 0;
  updateScopedIndices();
  render();
}

async function load() {
  const data = await (await fetch('/api/rows')).json();
  allRows = data.map(r => {
    const parts = (r.audio_path || '').split('/');
    let roomId = 'default_room';
    let userId = r.speaker_id || 'speaker_001';

    if (parts.length >= 4 && parts[0] === 'train') {
      roomId = parts[1];
      userId = parts[2];
    } else {
      const base = parts[parts.length - 1] || '';
      const mRoom = base.match(/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})/);
      const mUser = base.match(/(\d{15,22})/);
      if (mRoom) roomId = mRoom[1];
      if (mUser) userId = mUser[1];
    }

    return {
      ...r,
      room_id: roomId,
      user_id: userId,
      filename: parts[parts.length - 1] || r.audio_path
    };
  });

  if (!allRows.length) {
    $('#counter').textContent = '0 / 0';
    $('#details').textContent = '';
    $('#audio').src = '';
    $('#text').value = '';
    status('metadata.csv không còn dòng dữ liệu nào.');
    renderSidebar();
    return;
  }

  updateScopedIndices();
  await render();
}

async function render() {
  updateScopedIndices();
  updateScopeUI();
  renderSidebar();

  if (!scopedIndices.length) {
    $('#details').textContent = 'Không có mẫu nào trong phạm vi đã chọn.';
    $('#audio').src = '';
    $('#text').value = '';
    return;
  }

  const actualIdx = scopedIndices[scopedIndex];
  const r = allRows[actualIdx];
  dirty = false;

  $('#details').textContent = `${r.audio_path}  •  ${r.duration || '?'} giây  •  Phòng: ${r.room_id.slice(0, 8)}...  •  User: ${r.user_id}`;
  $('#text').value = r.text || '';
  $('#text').blur();
  status('');

  // Revoke previous blob URL to prevent memory leaks
  if (currentBlobUrl) {
    URL.revokeObjectURL(currentBlobUrl);
    currentBlobUrl = null;
  }

  try {
    const resp = await fetch('/api/stream/' + r.row_index);
    if (!resp.ok) throw new Error('Không thể tải file audio');
    const rawBlob = await resp.blob();
    const audioBlob = new Blob([rawBlob], { type: 'audio/wav' });
    currentBlobUrl = URL.createObjectURL(audioBlob);
    const audioEl = $('#audio');
    audioEl.src = currentBlobUrl;
    // Re-apply preserved playback rate
    const curSpeed = getSpeed();
    audioEl.playbackRate = curSpeed;
    audioEl.defaultPlaybackRate = curSpeed;
  } catch (err) {
    status('Lỗi tải audio: ' + err.message, true);
  }
}

async function save(go=0) {
  if (!scopedIndices.length) return;
  const actualIdx = scopedIndices[scopedIndex];
  const r = allRows[actualIdx];
  const text = $('#text').value;

  if (!dirty && !go) {
    status('Không có thay đổi.');
    return;
  }

  status('Đang lưu...');
  const response = await fetch('/api/rows/' + r.row_index, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text })
  });
  const result = await response.json();
  if (!response.ok) {
    status(result.error || 'Không thể lưu.', true);
    return;
  }

  r.text = text;
  dirty = false;
  status('Đã lưu thay đổi.');

  if (go) {
    scopedIndex = Math.max(0, Math.min(scopedIndices.length - 1, scopedIndex + go));
    render();
  }
}

async function deleteSample() {
  if (!scopedIndices.length) return;
  const actualIdx = scopedIndices[scopedIndex];
  const r = allRows[actualIdx];

  const confirmed = confirm(`Bạn có chắc chắn muốn xóa mẫu này?\n\n• Audio: ${r.audio_path}\n• Phòng: ${r.room_id}\n• User: ${r.user_id}\n• Thời lượng: ${r.duration || '?'} giây\n\nFile audio sẽ bị XÓA VĨNH VIỄN khỏi ổ đĩa và loại khỏi metadata.csv!`);
  if (!confirmed) return;

  status('Đang xóa mẫu audio...');
  try {
    const response = await fetch('/api/rows/' + r.row_index, { method: 'DELETE' });
    const result = await response.json();
    if (!response.ok) {
      status(result.error || 'Không thể xóa mẫu.', true);
      return;
    }

    allRows.splice(actualIdx, 1);
    allRows.forEach((row, idx) => { row.row_index = idx; });
    dirty = false;

    updateScopedIndices();
    if (!scopedIndices.length) {
      status('Đã xóa hết mẫu trong phạm vi này. Tự động chuyển về Tất cả dữ liệu.');
      activeScope = { type: 'all' };
      scopedIndex = 0;
      updateScopedIndices();
    } else if (scopedIndex >= scopedIndices.length) {
      scopedIndex = scopedIndices.length - 1;
    }

    render();
    status('Đã xóa vĩnh viễn audio và cập nhật metadata.csv.');
  } catch (err) {
    status('Lỗi khi xóa: ' + err.message, true);
  }
}

// UI Event Listeners
$('#text').addEventListener('input', () => { dirty = true; status('Có thay đổi chưa lưu.'); });
$('#save').onclick = () => save();
$('#prev').onclick = () => save(-1);
$('#next').onclick = () => save(1);
$('#delete').onclick = () => deleteSample();
$('#rewind').onclick = () => seekBy(-seekStep());
$('#forward').onclick = () => seekBy(seekStep());
$('#seek-step').oninput = e => setSeekStep(e.target.value);
$('#speed').oninput = e => setSpeed(e.target.value);
$('#normal-speed').onclick = () => setSpeed(1);
$('#reset-filter').onclick = () => setScope({ type: 'all' });

// Sidebar Toggle
function toggleSidebar(force) {
  const sb = $('#sidebar');
  const btnOpen = $('#btn-open-sidebar');
  sb.classList.toggle('collapsed', force);
  const isCollapsed = sb.classList.contains('collapsed');
  btnOpen.style.display = isCollapsed ? 'inline-block' : 'none';
}
$('#btn-collapse-sidebar').onclick = () => toggleSidebar(true);
$('#btn-open-sidebar').onclick = () => toggleSidebar(false);

// Search input
$('#search-input').addEventListener('input', e => {
  searchQuery = e.target.value;
  $('#clear-search').style.display = searchQuery ? 'inline-block' : 'none';
  renderSidebar();
});
$('#clear-search').onclick = () => {
  $('#search-input').value = '';
  searchQuery = '';
  $('#clear-search').style.display = 'none';
  renderSidebar();
};

// Ensure speed is maintained whenever audio loads metadata or starts playing
$('#audio').addEventListener('loadedmetadata', () => {
  const s = getSpeed();
  $('#audio').playbackRate = s;
  $('#audio').defaultPlaybackRate = s;
});
$('#audio').addEventListener('play', () => {
  const s = getSpeed();
  if ($('#audio').playbackRate !== s) {
    $('#audio').playbackRate = s;
  }
});

// Blur text and buttons when clicking outside so arrow keys always seek audio
document.addEventListener('pointerdown', e => {
  if (e.target !== $('#text')) {
    $('#text').blur();
  }
});

// Remove persistent button focus after click so Space doesn't re-trigger buttons
document.querySelectorAll('button').forEach(btn => {
  btn.addEventListener('mouseup', () => btn.blur());
});

$('#text').addEventListener('focus', () => {
  status('Đang ở vùng sửa text (nhấn Esc hoặc click ra ngoài để dùng ← / → tua audio).');
});
$('#text').addEventListener('blur', () => {
  if ($('#status').textContent.includes('Đang ở vùng sửa text')) {
    status('');
  }
});

// Global Keyboard Navigation
document.addEventListener('keydown', e => {
  const isEditingText = (document.activeElement === $('#text') || e.target === $('#text'));

  // Quick save: Ctrl+S or Cmd+S
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
    e.preventDefault();
    save();
    return;
  }

  // Toggle sidebar: Ctrl+B
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'b') {
    e.preventDefault();
    toggleSidebar();
    return;
  }

  // Escape exits text editing mode immediately
  if (e.key === 'Escape') {
    if (isEditingText) {
      $('#text').blur();
      status('Đã thoát vùng sửa text. Phím ← / → đang điều khiển tua audio.');
    }
    return;
  }

  // Alt + Arrow keys allow seeking audio ANYTIME (even while editing text)
  if (e.altKey && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
    e.preventDefault();
    seekBy(e.key === 'ArrowLeft' ? -seekStep() : seekStep());
    return;
  }

  // If typing inside the textarea, let normal arrow keys move text cursor
  if (isEditingText) {
    return;
  }

  // Space toggles Play / Pause
  if (e.code === 'Space') {
    e.preventDefault();
    const audio = $('#audio');
    audio.paused ? audio.play() : audio.pause();
    return;
  }

  // ArrowLeft / ArrowRight continuously seeks audio track
  if (e.key === 'ArrowLeft') {
    e.preventDefault();
    seekBy(-seekStep());
    return;
  }
  if (e.key === 'ArrowRight') {
    e.preventDefault();
    seekBy(seekStep());
    return;
  }

  // Next / Previous sample within active scope: [ and ]
  if (e.key === '[') {
    e.preventDefault();
    save(-1);
    return;
  }
  if (e.key === ']') {
    e.preventDefault();
    save(1);
    return;
  }
});

// Restore saved settings from localStorage
try {
  const savedSpeed = localStorage.getItem('review_speed');
  if (savedSpeed) setSpeed(savedSpeed);
  const savedSeek = localStorage.getItem('review_seek_step');
  if (savedSeek) setSeekStep(savedSeek);
} catch (e) {}

load().catch(e => status('Không đọc được metadata: ' + e.message, true));
</script>
</body>
</html>"""


class ReviewServer(BaseHTTPRequestHandler):
    dataset_dir: Path
    metadata_file: Path

    def log_message(self, format: str, *args: object) -> None:
        print("[review] " + format % args)

    def send_json(self, value: object, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_rows(self) -> tuple[list[str], list[dict[str, str]]]:
        with self.metadata_file.open("r", encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            return reader.fieldnames or [], list(reader)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            body = APP_HTML.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/rows":
            _, rows = self.read_rows()
            self.send_json([{**row, "row_index": i} for i, row in enumerate(rows)])
        elif path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
        elif path.startswith("/api/stream/"):
            try:
                row_idx = int(path.removeprefix("/api/stream/"))
                _, rows = self.read_rows()
                if row_idx < 0 or row_idx >= len(rows):
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                audio_rel = rows[row_idx].get("audio_path", "").strip()
                audio_path = (self.dataset_dir / audio_rel).resolve()
                try:
                    audio_path.relative_to(self.dataset_dir.resolve())
                except ValueError:
                    self.send_error(HTTPStatus.FORBIDDEN)
                    return
                if not audio_path.is_file():
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return

                data = audio_path.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", "inline; filename=\"preview\"")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
                self.end_headers()
                self.wfile.write(data)
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                pass
            except Exception as e:
                self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))
        elif path.startswith("/audio/"):
            relative_path = unquote(path.removeprefix("/audio/"))
            audio_path = (self.dataset_dir / relative_path).resolve()
            try:
                audio_path.relative_to(self.dataset_dir.resolve())
            except ValueError:
                self.send_error(HTTPStatus.FORBIDDEN, "Invalid audio path")
                return
            if not audio_path.is_file():
                self.send_error(HTTPStatus.NOT_FOUND, "Audio file not found")
                return
            mime_type = mimetypes.guess_type(audio_path.name)[0] or "application/octet-stream"
            file_size = audio_path.stat().st_size
            start, end = 0, file_size - 1
            range_header = self.headers.get("Range")
            if range_header:
                try:
                    unit, byte_range = range_header.strip().split("=", 1)
                    if unit != "bytes":
                        raise ValueError
                    start_text, end_text = byte_range.split(",", 1)[0].split("-", 1)
                    if start_text:
                        start = int(start_text)
                        end = int(end_text) if end_text else end
                    else:  # A suffix range such as "bytes=-500".
                        suffix_length = int(end_text)
                        start = max(0, file_size - suffix_length)
                    if start < 0 or start >= file_size or end < start:
                        raise ValueError
                    end = min(end, file_size - 1)
                except ValueError:
                    self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    self.send_header("Content-Range", f"bytes */{file_size}")
                    self.end_headers()
                    return

            content_length = end - start + 1
            try:
                self.send_response(HTTPStatus.PARTIAL_CONTENT if range_header else HTTPStatus.OK)
                self.send_header("Content-Type", mime_type)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(content_length))
                if range_header:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                self.end_headers()

                with audio_path.open("rb") as file:
                    file.seek(start)
                    remaining = content_length
                    while remaining:
                        block = file.read(min(64 * 1024, remaining))
                        if not block:
                            break
                        self.wfile.write(block)
                        remaining -= len(block)
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                # Browser aborted the connection (normal behavior when seeking, skipping tracks, or pausing)
                pass
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_PUT(self) -> None:
        path = urlparse(self.path).path
        if not path.startswith("/api/rows/"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            row_index = int(path.rsplit("/", 1)[1])
            content_length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
            text = payload["text"]
            if not isinstance(text, str):
                raise ValueError("text must be a string")
            fields, rows = self.read_rows()
            if row_index < 0 or row_index >= len(rows):
                raise IndexError("row does not exist")
            rows[row_index]["text"] = text.strip()
            temporary = self.metadata_file.with_suffix(".csv.tmp")
            with temporary.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows)
            os.replace(temporary, self.metadata_file)
            self.send_json({"ok": True})
        except (ValueError, KeyError, IndexError, json.JSONDecodeError) as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        if not path.startswith("/api/rows/"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            row_index = int(path.rsplit("/", 1)[1])
            fields, rows = self.read_rows()
            if row_index < 0 or row_index >= len(rows):
                raise IndexError("Dòng không tồn tại")

            target_row = rows[row_index]
            audio_rel_path = target_row.get("audio_path", "").strip()

            # Safely delete audio file from disk if it exists
            file_deleted = False
            if audio_rel_path:
                audio_path = (self.dataset_dir / audio_rel_path).resolve()
                try:
                    audio_path.relative_to(self.dataset_dir.resolve())
                    if audio_path.is_file():
                        audio_path.unlink()
                        file_deleted = True
                except (ValueError, OSError) as err:
                    print(f"[review] Warning: Could not delete file '{audio_path}': {err}")

            # Delete row from metadata
            del rows[row_index]
            temporary = self.metadata_file.with_suffix(".csv.tmp")
            with temporary.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows)
            os.replace(temporary, self.metadata_file)

            print(f"[review] Deleted row {row_index} and file '{audio_rel_path}' (deleted={file_deleted})")
            self.send_json({"ok": True, "file_deleted": file_deleted})
        except (ValueError, IndexError) as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except Exception as error:
            self.send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)


def main() -> None:
    parser = argparse.ArgumentParser(description="Review audio and edit ASR metadata locally.")
    parser.add_argument("--metadata", default="cs_dataset/metadata.csv", help="Path to metadata.csv")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    metadata_file = Path(args.metadata).resolve()
    if not metadata_file.is_file():
        parser.error(f"Metadata file not found: {metadata_file}")
    with metadata_file.open("r", encoding="utf-8-sig", newline="") as file:
        fields = csv.reader(file)
        header = next(fields, [])
    if "text" not in header:
        parser.error("metadata.csv must contain a 'text' column")
    ReviewServer.metadata_file = metadata_file
    ReviewServer.dataset_dir = metadata_file.parent
    server = ThreadingHTTPServer(("127.0.0.1", args.port), ReviewServer)
    print(f"Open http://127.0.0.1:{args.port} in your browser")
    print(f"Editing: {metadata_file}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
