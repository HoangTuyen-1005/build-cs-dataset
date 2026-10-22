"""Multi-user local app for reviewing ASR transcripts in metadata.csv.

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
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

# Concurrency & Claims Management
metadata_lock = threading.RLock()
claims_lock = threading.Lock()
active_claims: dict[int, dict] = {}  # row_index -> {"user": str, "timestamp": float}
CLAIM_TIMEOUT = 120.0  # seconds


def clean_expired_claims() -> None:
    """Removes locks that have exceeded the claim timeout."""
    now = time.time()
    with claims_lock:
        expired = [idx for idx, c in active_claims.items() if (now - c.get("timestamp", 0)) > CLAIM_TIMEOUT]
        for idx in expired:
            del active_claims[idx]


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
    .sidebar-filter-tabs {
      display: flex;
      gap: 3px;
      margin-top: 8px;
    }
    .filter-tab {
      flex: 1;
      padding: 4px 2px;
      font-size: 10.5px;
      border-radius: 4px;
      background: #1e1e2e;
      color: #9399b2;
      border: 1px solid #313244;
      cursor: pointer;
      text-align: center;
      transition: all 0.12s;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .filter-tab:hover {
      background: #313244;
      color: #cdd6f4;
    }
    .filter-tab.active {
      background: #45475a;
      color: #89b4fa;
      border-color: #89b4fa;
      font-weight: 600;
    }
    .sidebar-stats-box {
      border-bottom: 1px solid var(--border-sidebar);
      background: #161623;
    }
    .sidebar-stats {
      padding: 6px 14px 4px;
      font-size: 11px;
      color: var(--text-sidebar-muted);
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .sidebar-progress-track {
      height: 3px;
      background: #28283d;
      width: 100%;
      overflow: hidden;
    }
    .sidebar-progress-fill {
      height: 100%;
      background: #22c55e;
      transition: width 0.3s ease;
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
    .chunk-status-icon {
      font-size: 11px;
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
    .tree-badge.badge-all-done {
      background: #059669;
      color: #ffffff;
      font-weight: 600;
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

    /* Live Presence Badges in Sidebar */
    .chunk-claim-badge {
      font-size: 10px;
      padding: 1px 5px;
      border-radius: 4px;
      margin-left: 4px;
      font-weight: 600;
      flex-shrink: 0;
    }
    .chunk-claim-badge.other {
      background: #fef3c7;
      color: #b45309;
      border: 1px solid #fde68a;
    }
    .chunk-claim-badge.mine {
      background: #dbeafe;
      color: #1d4ed8;
      border: 1px solid #bfdbfe;
    }
    .chunk-verifier-tag {
      font-size: 9.5px;
      color: #94a3b8;
      margin-left: 4px;
      font-weight: 500;
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
    
    /* User Session Bar in Header */
    .user-session-bar {
      display: flex;
      align-items: center;
      gap: 7px;
      background: #ffffff;
      border: 1.5px solid #cbd5e1;
      border-radius: 20px;
      padding: 4px 12px;
      box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
    }
    .user-badge-icon {
      font-size: 14px;
      line-height: 1;
    }
    .user-label {
      font-size: 0.82rem;
      font-weight: 600;
      color: #64748b;
      user-select: none;
    }
    .reviewer-input {
      border: 0;
      outline: none;
      font-size: 0.88rem;
      font-weight: 700;
      color: #1e293b;
      width: 130px;
      background: transparent;
      padding: 2px 4px;
    }
    .reviewer-input:focus {
      color: var(--accent);
    }

    button { border: 0; border-radius: 7px; padding: 9px 15px; font-size: 0.95rem; cursor: pointer; background: #e2e8f0; font-weight: 500; transition: background 0.12s; }
    button:hover { background: #cbd5e1; }
    #save { background: var(--accent); color: white; }
    #save:hover { background: var(--accent-hover); }
    #delete { background: var(--danger); color: white; margin-left: auto; }
    #delete:hover { background: var(--danger-hover); }
    audio { width: 100%; margin: 16px 0 12px; }
    textarea { width: 100%; min-height: 230px; box-sizing: border-box; padding: 14px; font: 1.05rem/1.55 system-ui, sans-serif; border: 1px solid #94a3b8; border-radius: 8px; transition: border-color .15s, box-shadow .15s; background: #fff; }
    textarea:focus { border-color: var(--accent); outline: none; box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.2); }
    .details { color: #475569; font-size: .88rem; margin: 8px 0 14px; }
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
      margin: 8px 0 10px;
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

    /* Stats Dashboard */
    .stats-dashboard {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(215px, 1fr));
      gap: 12px;
      margin: 4px 0 12px;
    }
    .stat-card {
      background: #ffffff;
      border: 1px solid #e2e8f0;
      border-radius: 10px;
      padding: 10px 14px;
      display: flex;
      align-items: center;
      gap: 12px;
      box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
      transition: all 0.15s ease;
      cursor: default;
    }
    .stat-card:hover {
      border-color: #cbd5e1;
      box-shadow: 0 4px 8px rgba(0, 0, 0, 0.06);
    }
    .stat-icon {
      font-size: 22px;
      width: 40px;
      height: 40px;
      display: flex;
      align-items: center;
      justify-content: center;
      border-radius: 8px;
      background: #f1f5f9;
      flex-shrink: 0;
    }
    .stat-card.primary .stat-icon {
      background: #eff6ff;
      color: #2563eb;
    }
    .stat-card.mine .stat-icon {
      background: #fdf4ff;
      color: #c026d3;
    }
    .stat-card.success .stat-icon {
      background: #f0fdf4;
      color: #16a34a;
    }
    .stat-content {
      flex: 1;
      min-width: 0;
    }
    .stat-label {
      font-size: 0.74rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: #64748b;
      margin-bottom: 2px;
    }
    .stat-value {
      font-size: 1.05rem;
      font-weight: 700;
      color: #0f172a;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .stat-sub {
      font-size: 0.78rem;
      font-weight: 500;
      color: #94a3b8;
    }
    .stat-badge {
      font-size: 0.75rem;
      font-weight: 700;
      padding: 3px 8px;
      border-radius: 6px;
      background: #f1f5f9;
      color: #475569;
      flex-shrink: 0;
    }
    .stat-badge.primary {
      background: #dbeafe;
      color: #1d4ed8;
    }
    .stat-badge.mine {
      background: #f5d0fe;
      color: #a21caf;
    }
    .stat-badge.success {
      background: #dcfce7;
      color: #15803d;
    }

    /* Live Claim / Lock Alert Banner */
    .claim-banner {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 9px 14px;
      border-radius: 8px;
      font-size: 0.88rem;
      margin: 6px 0 12px;
      transition: all 0.2s;
    }
    .claim-banner.warning {
      background: #fffbeb;
      border: 1px solid #fcd34d;
      color: #b45309;
    }
    .claim-banner.active-mine {
      background: #f0fdf4;
      border: 1px solid #bbf7d0;
      color: #15803d;
    }
    .btn-claim-skip {
      border: 0;
      border-radius: 6px;
      padding: 4px 10px;
      font-size: 0.82rem;
      font-weight: 600;
      cursor: pointer;
      background: #fef08a;
      color: #854d0e;
      transition: background 0.12s;
      flex-shrink: 0;
    }
    .btn-claim-skip:hover {
      background: #fde047;
    }

    /* Verification Status in Main Panel */
    .verify-status-bar {
      display: flex;
      align-items: center;
      gap: 14px;
      margin: 6px 0 12px;
      flex-wrap: wrap;
    }
    .verify-badge {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      padding: 4px 11px;
      border-radius: 6px;
      font-size: 0.84rem;
      font-weight: 600;
      transition: all 0.15s;
    }
    .verify-badge.verified {
      background: #dcfce7;
      color: #15803d;
      border: 1px solid #86efac;
    }
    .verify-badge.unverified {
      background: #fef3c7;
      color: #b45309;
      border: 1px solid #fde68a;
    }
    .verify-checkbox-container {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      cursor: pointer;
      user-select: none;
      font-size: 0.9rem;
      font-weight: 500;
      color: #334155;
    }
    .verify-checkbox-container input {
      width: 17px;
      height: 17px;
      accent-color: #16a34a;
      cursor: pointer;
    }
    .btn-download-csv {
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      gap: 5px;
      background: #eff6ff;
      color: #2563eb;
      border: 1px solid #bfdbfe;
      border-radius: 6px;
      padding: 6px 11px;
      font-size: 0.84rem;
      font-weight: 600;
      transition: all 0.15s;
    }
    .btn-download-csv:hover {
      background: #dbeafe;
      color: #1d4ed8;
      border-color: #93c5fd;
    }
    .btn-verify-next {
      background: #16a34a !important;
      color: white !important;
      font-weight: 600 !important;
    }
    .btn-verify-next:hover {
      background: #15803d !important;
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
        <input id="search-input" class="search-input" type="text" placeholder="🔍 Lọc user ID, filename..." spellcheck="false">
        <button id="clear-search" class="clear-search-btn" title="Xóa tìm kiếm" style="display:none;">✕</button>
      </div>
      <div class="sidebar-filter-tabs">
        <button class="filter-tab active" data-vfilter="all" id="tab-vfilter-all">Tất cả</button>
        <button class="filter-tab" data-vfilter="unverified" id="tab-vfilter-unverified">⏳ Chưa duyệt</button>
        <button class="filter-tab" data-vfilter="verified" id="tab-vfilter-verified">✅ Đã duyệt</button>
        <button class="filter-tab" data-vfilter="mine" id="tab-vfilter-mine">👤 Của tôi</button>
      </div>
    </div>
    <div class="sidebar-stats-box">
      <div class="sidebar-stats" id="sidebar-stats">
        <span>Đang nạp danh sách...</span>
      </div>
      <div class="sidebar-progress-track" title="Tiến độ kiểm duyệt toàn bộ dataset">
        <div class="sidebar-progress-fill" id="sidebar-progress-fill" style="width: 0%;"></div>
      </div>
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
        <div class="user-session-bar" title="Tên của bạn sẽ được tự động gắn vào metadata khi duyệt">
          <span class="user-badge-icon">👤</span>
          <label for="reviewer-name" class="user-label">Người duyệt:</label>
          <input id="reviewer-name" class="reviewer-input" type="text" placeholder="Nhập tên bạn..." maxlength="30" spellcheck="false" autocomplete="name" />
        </div>
        <a href="/api/export_csv" class="btn-download-csv" download="metadata.csv" title="Tải file metadata.csv mới nhất về máy tính">📥 Tải CSV</a>
        <strong id="counter" class="counter-badge">0 / 0</strong>
      </header>

      <!-- Stats Dashboard: Duration & Samples -->
      <div class="stats-dashboard" id="stats-dashboard">
        <div class="stat-card primary" id="card-total-duration" title="Tổng thời lượng đã kiểm duyệt trên toàn bộ dataset">
          <div class="stat-icon">⏱️</div>
          <div class="stat-content">
            <div class="stat-label">Thời lượng đã duyệt</div>
            <div class="stat-value" id="stat-verified-duration">0s <span class="stat-sub">/ 0s</span></div>
          </div>
          <div class="stat-badge primary" id="stat-duration-pct">0%</div>
        </div>

        <div class="stat-card mine" id="card-my-duration" title="Thời lượng do chính bạn đã duyệt (dựa theo Người duyệt)">
          <div class="stat-icon">👤</div>
          <div class="stat-content">
            <div class="stat-label">Của bạn đã duyệt</div>
            <div class="stat-value" id="stat-my-duration">0s</div>
          </div>
          <div class="stat-badge mine" id="stat-my-count">0 mẫu</div>
        </div>

        <div class="stat-card success" id="card-total-samples" title="Tiến độ số lượng mẫu audio đã kiểm duyệt">
          <div class="stat-icon">✅</div>
          <div class="stat-content">
            <div class="stat-label">Số mẫu đã duyệt</div>
            <div class="stat-value" id="stat-verified-samples">0 <span class="stat-sub">/ 0</span></div>
          </div>
          <div class="stat-badge success" id="stat-samples-pct">0%</div>
        </div>
      </div>

      <div class="scope-banner" id="scope-banner">
        <span class="scope-text" id="scope-path">📍 Đang xem: <strong>Tất cả dữ liệu</strong></span>
        <button id="reset-filter" class="btn-reset-filter" style="display:none;">✕ Xem tất cả</button>
      </div>

      <div id="claim-banner" class="claim-banner" style="display:none;"></div>

      <p class="details">Phím tắt: <kbd>Space</kbd> phát/dừng, <kbd>←</kbd>/<kbd>→</kbd> tua track, <kbd>[</kbd>/<kbd>]</kbd> câu trước/tiếp, <kbd>Ctrl+S</kbd> lưu, <kbd>Ctrl+Enter</kbd> duyệt & tiếp.</p>
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
      
      <div class="verify-status-bar">
        <span id="verify-badge" class="verify-badge unverified">⏳ Chưa kiểm duyệt</span>
        <label class="verify-checkbox-container" title="Đánh dấu mẫu này đã được kiểm duyệt kỹ">
          <input type="checkbox" id="verify-checkbox">
          <span class="verify-checkbox-text">Đã kiểm duyệt (is_verified)</span>
        </label>
      </div>

      <textarea id="text" spellcheck="false" placeholder="Chưa có transcript..."></textarea>
      <div class="actions" style="margin-top:16px">
        <button id="prev" title="Mẫu trước trong phạm vi hiện tại ([)">← Trước</button>
        <button id="save" title="Lưu transcript (Ctrl+S)">💾 Lưu thay đổi</button>
        <button id="verify-next" class="btn-verify-next" title="Đánh dấu đã duyệt, lưu và chuyển sang mẫu tiếp theo (Ctrl+Enter)">✅ Đã duyệt & Tiếp →</button>
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

// Multi-user & Claim State
let activeClaims = {}; // { [row_index]: { user: string, expires_in: number } }
let currentClaimedRowIndex = null;
let heartbeatTimer = null;
let syncTimer = null;

// Filter state
let activeScope = { type: 'all', userId: null };
let verificationFilter = 'all'; // 'all' | 'unverified' | 'verified' | 'mine'
let expandedTreeKeys = new Set();
let searchQuery = '';

const $ = s => document.querySelector(s);
const status = (message, error=false) => {
  $('#status').textContent = message;
  $('#status').className = error ? 'error' : '';
};

// Reviewer Name management
function getReviewerName() {
  const input = $('#reviewer-name');
  let name = input ? input.value.trim() : '';
  if (!name) {
    try {
      name = localStorage.getItem('reviewer_name') || '';
    } catch (e) {}
  }
  if (!name) {
    name = 'Reviewer_' + Math.floor(1000 + Math.random() * 9000);
    try { localStorage.setItem('reviewer_name', name); } catch (e) {}
  }
  if (input && input.value !== name) {
    input.value = name;
  }
  return name;
}

// Duration formatting helpers
function formatDuration(seconds) {
  const s = Math.round(Number(seconds) || 0);
  if (s <= 0) return '0s';
  const hours = Math.floor(s / 3600);
  const minutes = Math.floor((s % 3600) / 60);
  const secs = s % 60;
  if (hours > 0) {
    return `${hours}h ${minutes.toString().padStart(2, '0')}m ${secs.toString().padStart(2, '0')}s`;
  }
  if (minutes > 0) {
    return `${minutes}m ${secs.toString().padStart(2, '0')}s`;
  }
  return `${secs}s`;
}

function formatDurationHuman(seconds) {
  const s = Math.round(Number(seconds) || 0);
  if (s <= 0) return '0 giây';
  const hours = Math.floor(s / 3600);
  const minutes = Math.floor((s % 3600) / 60);
  const secs = s % 60;
  const parts = [];
  if (hours > 0) parts.push(`${hours} giờ`);
  if (minutes > 0) parts.push(`${minutes} phút`);
  if (secs > 0 || parts.length === 0) parts.push(`${secs} giây`);
  return parts.join(' ');
}

function updateStatsDashboard() {
  const myName = getReviewerName().toLowerCase();
  let totalDur = 0;
  let verifiedDur = 0;
  let myDur = 0;
  let totalCount = allRows.length;
  let verifiedCount = 0;
  let myCount = 0;

  for (let i = 0; i < totalCount; i++) {
    const r = allRows[i];
    const d = parseFloat(r.duration) || 0;
    totalDur += d;
    if (r.is_verified) {
      verifiedDur += d;
      verifiedCount++;
      if ((r.verified_by || '').trim().toLowerCase() === myName) {
        myDur += d;
        myCount++;
      }
    }
  }

  const durPct = totalDur > 0 ? ((verifiedDur / totalDur) * 100).toFixed(1) : '0.0';
  const samplesPct = totalCount > 0 ? ((verifiedCount / totalCount) * 100).toFixed(1) : '0.0';

  const statVerDur = $('#stat-verified-duration');
  if (statVerDur) {
    statVerDur.innerHTML = `${formatDuration(verifiedDur)} <span class="stat-sub">/ ${formatDuration(totalDur)}</span>`;
  }
  const statDurPct = $('#stat-duration-pct');
  if (statDurPct) {
    statDurPct.textContent = `${Math.round(parseFloat(durPct))}%`;
  }
  const cardTotalDur = $('#card-total-duration');
  if (cardTotalDur) {
    cardTotalDur.title = `Đã kiểm duyệt: ${formatDurationHuman(verifiedDur)} (${verifiedDur.toFixed(1)}s)\nTổng dataset: ${formatDurationHuman(totalDur)} (${totalDur.toFixed(1)}s)\nTiến độ thời lượng: ${durPct}%`;
  }

  const statMyDur = $('#stat-my-duration');
  if (statMyDur) {
    statMyDur.textContent = formatDuration(myDur);
  }
  const statMyCount = $('#stat-my-count');
  if (statMyCount) {
    statMyCount.textContent = `${myCount} mẫu`;
  }
  const cardMyDur = $('#card-my-duration');
  if (cardMyDur) {
    cardMyDur.title = `Người duyệt: ${getReviewerName()}\nThời lượng bạn đã duyệt: ${formatDurationHuman(myDur)} (${myDur.toFixed(1)}s)\nSố mẫu bạn đã duyệt: ${myCount} mẫu`;
  }

  const statVerSamples = $('#stat-verified-samples');
  if (statVerSamples) {
    statVerSamples.innerHTML = `${verifiedCount} <span class="stat-sub">/ ${totalCount}</span>`;
  }
  const statSamplesPct = $('#stat-samples-pct');
  if (statSamplesPct) {
    statSamplesPct.textContent = `${Math.round(parseFloat(samplesPct))}%`;
  }
  const cardTotalSamples = $('#card-total-samples');
  if (cardTotalSamples) {
    cardTotalSamples.title = `Đã kiểm duyệt: ${verifiedCount} / ${totalCount} mẫu (${samplesPct}%)`;
  }
}

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

// Compute filtered row indices based on activeScope & verificationFilter
function updateScopedIndices() {
  scopedIndices = [];
  const myName = getReviewerName().toLowerCase();

  for (let i = 0; i < allRows.length; i++) {
    const r = allRows[i];

    // Verification status filter
    if (verificationFilter === 'unverified' && r.is_verified) continue;
    if (verificationFilter === 'verified' && !r.is_verified) continue;
    if (verificationFilter === 'mine') {
      if (!r.is_verified || (r.verified_by || '').trim().toLowerCase() !== myName) {
        continue;
      }
    }

    // User scope filter
    if (activeScope.type === 'user') {
      if (r.user_id === activeScope.userId) {
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

  let filterTag = '';
  if (verificationFilter === 'unverified') filterTag = ' • <em style="color:#d97706;">[Chưa duyệt]</em>';
  else if (verificationFilter === 'verified') filterTag = ' • <em style="color:#15803d;">[Đã duyệt]</em>';
  else if (verificationFilter === 'mine') filterTag = ` • <em style="color:#2563eb;">[Của tôi: ${getReviewerName()}]</em>`;

  let scopeDurInfo = '';
  if (count > 0) {
    let scTotalDur = 0;
    let scVerDur = 0;
    for (let idx of scopedIndices) {
      const d = parseFloat(allRows[idx]?.duration) || 0;
      scTotalDur += d;
      if (allRows[idx]?.is_verified) scVerDur += d;
    }
    const scPct = scTotalDur > 0 ? Math.round((scVerDur / scTotalDur) * 100) : 0;
    scopeDurInfo = ` &nbsp;•&nbsp; ⏱️ <strong>${formatDuration(scVerDur)}</strong> / ${formatDuration(scTotalDur)} (${scPct}%)`;
  }

  if (activeScope.type === 'user') {
    scopePath.innerHTML = `📍 Phạm vi User: <strong>${activeScope.userId}</strong>${scopeDurInfo}${filterTag}`;
    resetBtn.style.display = 'inline-block';
  } else {
    scopePath.innerHTML = `📍 Phạm vi: <strong>Tất cả dữ liệu</strong>${scopeDurInfo}${filterTag}`;
    resetBtn.style.display = (verificationFilter !== 'all') ? 'inline-block' : 'none';
  }

  $('#counter').textContent = count ? `${scopedIndex + 1} / ${count}` : '0 / 0';
  $('#prev').disabled = count <= 1 || scopedIndex === 0;
  $('#next').disabled = count <= 1 || scopedIndex === count - 1;

  updateStatsDashboard();
}

// Build user map with verification counts
function getUsersMap() {
  const usersMap = new Map();
  allRows.forEach((r, idx) => {
    if (!usersMap.has(r.user_id)) {
      usersMap.set(r.user_id, {
        userId: r.user_id,
        samples: 0,
        verifiedSamples: 0,
        indices: []
      });
    }
    const usr = usersMap.get(r.user_id);
    usr.samples++;
    if (r.is_verified) usr.verifiedSamples++;
    usr.indices.push(idx);
  });
  return usersMap;
}

function renderSidebar() {
  const container = $('#tree-container');
  const usersMap = getUsersMap();
  const q = searchQuery.toLowerCase().trim();
  const myName = getReviewerName().toLowerCase();

  const totalVerified = allRows.filter(r => r.is_verified).length;
  const totalUnverified = allRows.length - totalVerified;
  const myVerified = allRows.filter(r => r.is_verified && (r.verified_by || '').trim().toLowerCase() === myName).length;
  const pct = allRows.length ? Math.round((totalVerified / allRows.length) * 100) : 0;

  const totalDuration = allRows.reduce((acc, r) => acc + (parseFloat(r.duration) || 0), 0);
  const verifiedDuration = allRows.reduce((acc, r) => acc + (r.is_verified ? (parseFloat(r.duration) || 0) : 0), 0);
  const durPct = totalDuration > 0 ? Math.round((verifiedDuration / totalDuration) * 100) : 0;

  // Sidebar stats & progress bar (showing both count and audio duration)
  $('#sidebar-stats').innerHTML = `<span>${usersMap.size} user • ${allRows.length} mẫu • ⏱️ <strong style="color:#4ade80;">${formatDuration(verifiedDuration)}</strong> / ${formatDuration(totalDuration)} (${durPct}%)</span>`;
  const fill = $('#sidebar-progress-fill');
  if (fill) fill.style.width = durPct + '%';

  // Update filter tab texts and active classes
  const tabAll = $('#tab-vfilter-all');
  const tabUnver = $('#tab-vfilter-unverified');
  const tabVer = $('#tab-vfilter-verified');
  const tabMine = $('#tab-vfilter-mine');
  if (tabAll) tabAll.textContent = `Tất cả (${allRows.length})`;
  if (tabUnver) tabUnver.textContent = `⏳ Chưa (${totalUnverified})`;
  if (tabVer) tabVer.textContent = `✅ Đã duyệt (${totalVerified})`;
  if (tabMine) tabMine.textContent = `👤 Của tôi (${myVerified})`;

  document.querySelectorAll('.filter-tab').forEach(tab => {
    tab.classList.toggle('active', tab.getAttribute('data-vfilter') === verificationFilter);
  });

  let html = '';

  // Root item: "Tất cả dữ liệu"
  const isAllActive = activeScope.type === 'all';
  let rootCount = allRows.length;
  if (verificationFilter === 'unverified') rootCount = totalUnverified;
  else if (verificationFilter === 'verified') rootCount = totalVerified;
  else if (verificationFilter === 'mine') rootCount = myVerified;

  html += `
    <div class="tree-item ${isAllActive ? 'active' : ''}" data-type="all">
      <span class="tree-icon">🌟</span>
      <span class="tree-label"><strong>Tất cả dữ liệu</strong></span>
      <span class="tree-badge">${rootCount}</span>
    </div>
  `;

  // Render Users
  usersMap.forEach((userData, userId) => {
    const userKey = `user_${userId}`;
    const isUserActive = activeScope.type === 'user' && activeScope.userId === userId;
    const isCurrentUser = (scopedIndices.length && allRows[scopedIndices[scopedIndex]]?.user_id === userId);

    // Filter user indices based on verificationFilter
    const filteredIndices = userData.indices.filter(idx => {
      const row = allRows[idx];
      if (verificationFilter === 'unverified' && row.is_verified) return false;
      if (verificationFilter === 'verified' && !row.is_verified) return false;
      if (verificationFilter === 'mine' && (!row.is_verified || (row.verified_by || '').trim().toLowerCase() !== myName)) return false;
      return true;
    });

    // Check filter match
    const userMatches = !q || userId.toLowerCase().includes(q) || filteredIndices.some(i => allRows[i].filename.toLowerCase().includes(q));
    if (q && !userMatches) {
      return;
    }

    if (verificationFilter !== 'all' && filteredIndices.length === 0) {
      return;
    }

    // Auto expand on search or active
    const isExpanded = q ? true : (expandedTreeKeys.has(userKey) || isCurrentUser);
    const isAllDone = (userData.verifiedSamples === userData.samples && userData.samples > 0);
    const badgeText = `${userData.verifiedSamples}/${userData.samples}`;

    let userTotalDur = 0;
    let userVerDur = 0;
    userData.indices.forEach(i => {
      const d = parseFloat(allRows[i].duration) || 0;
      userTotalDur += d;
      if (allRows[i].is_verified) userVerDur += d;
    });

    html += `
      <div class="tree-item tree-user ${isUserActive ? 'active' : ''}" data-type="user" data-user="${userId}">
        <span class="tree-caret ${isExpanded ? 'expanded' : ''}" data-toggle-key="${userKey}">▶</span>
        <span class="tree-icon">📁</span>
        <span class="tree-label" title="User ID: ${userId}">${userId}</span>
        <span class="tree-badge ${isAllDone ? 'badge-all-done' : ''}" title="${userData.verifiedSamples}/${userData.samples} mẫu (${formatDuration(userVerDur)} / ${formatDuration(userTotalDur)})">${badgeText}</span>
      </div>
      <div class="tree-children ${isExpanded ? 'expanded' : ''}" id="children_${userKey}">
    `;

    // Chunks under user
    filteredIndices.forEach(idx => {
      const row = allRows[idx];
      const isCurrentChunk = (scopedIndices.length && scopedIndices[scopedIndex] === idx);
      const chunkName = row.filename;

      if (q && !chunkName.toLowerCase().includes(q) && !userId.toLowerCase().includes(q)) {
        return;
      }

      const statusIcon = row.is_verified ? '✅' : '⚪';

      // Live Claim presence badge
      const claim = activeClaims[String(row.row_index)] || activeClaims[row.row_index];
      let claimBadge = '';
      if (claim && !row.is_verified) {
        if (claim.user.toLowerCase() === myName) {
          claimBadge = `<span class="chunk-claim-badge mine" title="Bạn đang mở mẫu này">🔵 Tôi</span>`;
        } else {
          claimBadge = `<span class="chunk-claim-badge other" title="Đang được review bởi ${claim.user}">🟡 ${claim.user}</span>`;
        }
      }

      let verifierTag = '';
      if (row.is_verified && row.verified_by) {
        verifierTag = `<span class="chunk-verifier-tag" title="Kiểm duyệt bởi ${row.verified_by}">[${row.verified_by}]</span>`;
      }

      html += `
        <div class="tree-item tree-chunk ${isCurrentChunk ? 'active-chunk' : ''}" data-type="chunk" data-index="${idx}" title="${row.audio_path} (${row.is_verified ? 'Đã duyệt bởi ' + (row.verified_by || 'người dùng') : 'Chưa duyệt'})">
          <span class="chunk-status-icon">${statusIcon}</span>
          <span class="tree-label">${chunkName}${verifierTag}</span>
          ${claimBadge}
          <span class="tree-badge" style="background:transparent; color:#6c7086;">${row.duration || ''}s</span>
        </div>
      `;
    });

    html += `</div>`; // Close user children
  });

  container.innerHTML = html;

  // Add click handlers for tree items
  container.querySelectorAll('.tree-item').forEach(el => {
    el.onclick = (e) => {
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
      } else if (type === 'user') {
        const userId = el.getAttribute('data-user');
        setScope({ type: 'user', userId });
      } else if (type === 'chunk') {
        const targetIdx = Number(el.getAttribute('data-index'));
        const row = allRows[targetIdx];
        if (row) {
          activeScope = { type: 'user', userId: row.user_id };
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

// Multi-user Claiming API
async function claimSample(rowIndex) {
  const reviewer = getReviewerName();
  try {
    const res = await fetch('/api/claim', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ row_index: rowIndex, user: reviewer })
    });
    const data = await res.json();
    currentClaimedRowIndex = rowIndex;
    updateClaimBanner(rowIndex, data);
    return data;
  } catch (e) {
    console.error('Lỗi khi claim mẫu:', e);
  }
}

async function releaseSample(rowIndex) {
  if (rowIndex === null || rowIndex === undefined) return;
  const reviewer = getReviewerName();
  try {
    await fetch('/api/release', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ row_index: rowIndex, user: reviewer }),
      keepalive: true
    });
  } catch (e) {}
  if (currentClaimedRowIndex === rowIndex) {
    currentClaimedRowIndex = null;
  }
}

function updateClaimBanner(rowIndex, claimInfo = null) {
  const banner = $('#claim-banner');
  if (!banner) return;
  const myName = getReviewerName();
  
  const claim = (claimInfo && claimInfo.ok !== undefined)
    ? (claimInfo.ok ? { user: myName } : { user: claimInfo.claimed_by })
    : (activeClaims[String(rowIndex)] || activeClaims[rowIndex]);

  if (!claim) {
    banner.style.display = 'none';
    banner.className = 'claim-banner';
    banner.innerHTML = '';
    return;
  }

  if (claim.user.toLowerCase() === myName.toLowerCase()) {
    banner.className = 'claim-banner active-mine';
    banner.style.display = 'flex';
    banner.innerHTML = `
      <div style="display:flex; align-items:center; gap:8px;">
        <span class="claim-icon">🔒</span>
        <span>Bạn đang kiểm duyệt mẫu này <em>(Tự động giữ quyền chỉnh sửa)</em></span>
      </div>
    `;
  } else {
    banner.className = 'claim-banner warning';
    banner.style.display = 'flex';
    banner.innerHTML = `
      <div style="display:flex; align-items:center; gap:8px;">
        <span class="claim-icon">⚠️</span>
        <span>Mẫu này đang được review bởi <strong style="text-decoration:underline;">${claim.user}</strong>!</span>
      </div>
      <button id="btn-skip-claimed" class="btn-claim-skip" title="Chuyển ngay đến mẫu chưa ai làm">⏭️ Nhảy sang mẫu trống khác</button>
    `;
    const skipBtn = $('#btn-skip-claimed');
    if (skipBtn) {
      skipBtn.onclick = () => skipToNextAvailable();
    }
  }
}

// Find next unverified sample not claimed by someone else
function findNextAvailableScopedIndex(start, end = null) {
  const myName = getReviewerName().toLowerCase();
  const limit = (end !== null) ? end : scopedIndices.length;
  for (let i = start; i < limit; i++) {
    const rowIdx = scopedIndices[i];
    const r = allRows[rowIdx];
    if (r.is_verified) continue;
    const claim = activeClaims[String(r.row_index)] || activeClaims[r.row_index];
    if (claim && claim.user.toLowerCase() !== myName) continue; // Claimed by someone else
    return i;
  }
  return -1;
}

async function skipToNextAvailable() {
  const next = findNextAvailableScopedIndex(scopedIndex + 1);
  if (next !== -1) {
    scopedIndex = next;
    await render();
    status('Đã nhảy sang mẫu chưa có người review.');
    return;
  }
  const wrap = findNextAvailableScopedIndex(0, scopedIndex);
  if (wrap !== -1) {
    scopedIndex = wrap;
    await render();
    status('Đã quay lại mẫu trống phía trước.');
    return;
  }
  status('Không còn mẫu trống nào trong danh sách hiện tại.', true);
}

// Real-time claims & rows synchronization
async function syncRealtime() {
  try {
    const [claimsRes, rowsRes] = await Promise.all([
      fetch('/api/claims'),
      fetch('/api/rows')
    ]);
    if (claimsRes.ok) {
      activeClaims = await claimsRes.json();
    }
    if (rowsRes.ok) {
      const serverRows = await rowsRes.json();
      const actualIdx = (scopedIndices.length && scopedIndex < scopedIndices.length) ? scopedIndices[scopedIndex] : -1;
      
      serverRows.forEach(sr => {
        const local = allRows[sr.row_index];
        if (local) {
          local.is_verified = (sr.is_verified === true || sr.is_verified === 'True' || sr.is_verified === 'true' || sr.is_verified === '1');
          local.verified_by = sr.verified_by || '';
          if (local.row_index !== actualIdx || !dirty) {
            local.text = sr.text;
          }
        }
      });
    }

    renderSidebar();
    updateStatsDashboard();
    if (scopedIndices.length && scopedIndex < scopedIndices.length) {
      const actualIdx = scopedIndices[scopedIndex];
      updateClaimBanner(allRows[actualIdx].row_index);
    }
  } catch (e) {
    // Network glitch ignored
  }
}

async function load() {
  getReviewerName();
  const data = await (await fetch('/api/rows')).json();
  allRows = data.map(r => {
    const parts = (r.audio_path || '').split('/');
    let userId = r.speaker_id || 'unknown_user';

    if (parts.length >= 3 && parts[0] === 'train') {
      userId = parts[1];
    } else if (parts.length >= 4 && parts[0] === 'train') {
      userId = parts[2];
    }

    const base = parts[parts.length - 1] || '';
    const mUser = base.match(/(\d{15,22})/);
    if (mUser && (!userId || userId === 'unknown_user')) {
      userId = mUser[1];
    }

    const isVer = (r.is_verified === true || r.is_verified === 'True' || r.is_verified === 'true' || r.is_verified === '1');

    return {
      ...r,
      user_id: userId,
      filename: base || r.audio_path,
      is_verified: isVer,
      verified_by: (r.verified_by || '').trim()
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

  // Initial fetch for active claims
  try {
    const cRes = await fetch('/api/claims');
    if (cRes.ok) activeClaims = await cRes.json();
  } catch (e) {}

  updateScopedIndices();
  await render();

  // Start background sync & heartbeat
  if (syncTimer) clearInterval(syncTimer);
  syncTimer = setInterval(syncRealtime, 4000);

  if (heartbeatTimer) clearInterval(heartbeatTimer);
  heartbeatTimer = setInterval(() => {
    if (scopedIndices.length && scopedIndex < scopedIndices.length) {
      const actualIdx = scopedIndices[scopedIndex];
      const r = allRows[actualIdx];
      if (r) claimSample(r.row_index);
    }
  }, 25000);
}

async function render() {
  updateScopedIndices();
  updateScopeUI();
  renderSidebar();

  if (!scopedIndices.length) {
    $('#details').textContent = 'Không có mẫu nào trong phạm vi đã chọn.';
    $('#audio').src = '';
    $('#text').value = '';
    const banner = $('#claim-banner');
    if (banner) banner.style.display = 'none';
    const badge = $('#verify-badge');
    if (badge) {
      badge.className = 'verify-badge unverified';
      badge.textContent = '⏳ Không có mẫu';
    }
    const chk = $('#verify-checkbox');
    if (chk) chk.checked = false;
    return;
  }

  const actualIdx = scopedIndices[scopedIndex];
  const r = allRows[actualIdx];
  dirty = false;

  // Claim this sample for active reviewer
  claimSample(r.row_index);

  // Update Verification Badge & Checkbox
  const badge = $('#verify-badge');
  const checkbox = $('#verify-checkbox');
  if (r.is_verified) {
    const byText = r.verified_by ? ` (bởi <strong>${r.verified_by}</strong>)` : '';
    badge.className = 'verify-badge verified';
    badge.innerHTML = `✅ Đã kiểm duyệt${byText}`;
    checkbox.checked = true;
  } else {
    badge.className = 'verify-badge unverified';
    badge.innerHTML = '⏳ Chưa kiểm duyệt (is_verified = False)';
    checkbox.checked = false;
  }

  const statusHtml = r.is_verified
    ? `<span style="color:#15803d; font-weight:600;">✅ Đã duyệt${r.verified_by ? ' (' + r.verified_by + ')' : ''}</span>`
    : '<span style="color:#b45309; font-weight:600;">⏳ Chưa duyệt</span>';
  
  $('#details').innerHTML = `${r.audio_path} &nbsp;•&nbsp; ${r.duration || '?'} giây &nbsp;•&nbsp; User: <strong>${r.user_id}</strong> &nbsp;•&nbsp; ${statusHtml}`;
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

async function save(go=0, forceVerified=null) {
  if (!scopedIndices.length) return;
  const actualIdx = scopedIndices[scopedIndex];
  const r = allRows[actualIdx];
  const text = $('#text').value;
  const targetVerified = (forceVerified !== null) ? forceVerified : $('#verify-checkbox').checked;
  const reviewer = getReviewerName();

  const isTextChanged = (text !== (r.text || ''));
  const isStatusChanged = (targetVerified !== r.is_verified);

  if (!isTextChanged && !isStatusChanged && !go) {
    status('Không có thay đổi.');
    return;
  }

  status('Đang lưu...');
  try {
    const response = await fetch('/api/rows/' + r.row_index, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text: text,
        is_verified: targetVerified,
        reviewer: reviewer
      })
    });
    const result = await response.json();
    if (!response.ok) {
      status(result.error || 'Không thể lưu.', true);
      return;
    }

    r.text = text;
    r.is_verified = targetVerified;
    r.verified_by = targetVerified ? reviewer : '';
    dirty = false;
    status(`Đã lưu thành công (is_verified = ${r.is_verified ? 'True' : 'False'}${r.verified_by ? ', bởi ' + r.verified_by : ''}).`);

    // Release claim on saved row
    releaseSample(r.row_index);

    if (go > 0) {
      // Smart advance: look for next unverified sample not claimed by someone else
      const nextAvailable = findNextAvailableScopedIndex(scopedIndex + 1);
      if (nextAvailable !== -1) {
        scopedIndex = nextAvailable;
      } else {
        // Try wrap
        const wrap = findNextAvailableScopedIndex(0, scopedIndex);
        if (wrap !== -1) {
          scopedIndex = wrap;
          status('Đã hoàn thành các mẫu phía sau, chuyển sang mẫu chưa duyệt phía trước.');
        } else {
          scopedIndex = Math.max(0, Math.min(scopedIndices.length - 1, scopedIndex + go));
        }
      }
      render();
    } else if (go < 0) {
      scopedIndex = Math.max(0, scopedIndex + go);
      render();
    } else {
      render();
    }
  } catch (err) {
    status('Lỗi khi lưu: ' + err.message, true);
  }
}

async function verifyAndNext() {
  $('#verify-checkbox').checked = true;
  await save(1, true);
}

async function deleteSample() {
  if (!scopedIndices.length) return;
  const actualIdx = scopedIndices[scopedIndex];
  const r = allRows[actualIdx];

  const confirmed = confirm(`Bạn có chắc chắn muốn xóa mẫu này?\n\n• Audio: ${r.audio_path}\n• User: ${r.user_id}\n• Thời lượng: ${r.duration || '?'} giây\n\nFile audio sẽ bị XÓA VĨNH VIỄN khỏi ổ đĩa và loại khỏi metadata.csv!`);
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
$('#verify-next').onclick = () => verifyAndNext();
$('#verify-checkbox').onchange = (e) => save(0, e.target.checked);
$('#prev').onclick = () => save(-1);
$('#next').onclick = () => save(1);
$('#delete').onclick = () => deleteSample();
$('#rewind').onclick = () => seekBy(-seekStep());
$('#forward').onclick = () => seekBy(seekStep());
$('#seek-step').oninput = e => setSeekStep(e.target.value);
$('#speed').oninput = e => setSpeed(e.target.value);
$('#normal-speed').onclick = () => setSpeed(1);
$('#reset-filter').onclick = () => {
  activeScope = { type: 'all' };
  verificationFilter = 'all';
  scopedIndex = 0;
  updateScopedIndices();
  render();
};

// Reviewer name input change
$('#reviewer-name').addEventListener('input', e => {
  const name = e.target.value.trim();
  if (name) {
    try { localStorage.setItem('reviewer_name', name); } catch (err) {}
    renderSidebar();
    updateStatsDashboard();
  }
});
$('#reviewer-name').addEventListener('change', e => {
  const name = e.target.value.trim();
  if (name && scopedIndices.length && scopedIndex < scopedIndices.length) {
    const actualIdx = scopedIndices[scopedIndex];
    claimSample(allRows[actualIdx].row_index);
  }
});

// Filter tabs in sidebar
document.querySelectorAll('.filter-tab').forEach(tab => {
  tab.onclick = () => {
    verificationFilter = tab.getAttribute('data-vfilter');
    scopedIndex = 0;
    updateScopedIndices();
    render();
  };
});

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
  if (e.target !== $('#text') && e.target !== $('#reviewer-name') && e.target !== $('#search-input')) {
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
  const isEditingText = (document.activeElement === $('#text') || e.target === $('#text') || document.activeElement === $('#reviewer-name') || document.activeElement === $('#search-input'));

  // Quick save: Ctrl+S or Cmd+S
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
    e.preventDefault();
    save();
    return;
  }

  // Quick verify & next: Ctrl+Enter or Cmd+Enter
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
    e.preventDefault();
    verifyAndNext();
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
      if (document.activeElement) document.activeElement.blur();
      status('Đã thoát vùng nhập liệu. Phím ← / → đang điều khiển tua audio.');
    }
    return;
  }

  // Alt + Arrow keys allow seeking audio ANYTIME (even while editing text)
  if (e.altKey && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
    e.preventDefault();
    seekBy(e.key === 'ArrowLeft' ? -seekStep() : seekStep());
    return;
  }

  // If typing inside the textarea or inputs, let normal arrow keys move text cursor
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

// Clean release on window close
window.addEventListener('beforeunload', () => {
  if (currentClaimedRowIndex !== null) {
    const reviewer = getReviewerName();
    navigator.sendBeacon('/api/release', JSON.stringify({ row_index: currentClaimedRowIndex, user: reviewer }));
  }
});

// Restore saved settings from localStorage
try {
  const savedSpeed = localStorage.getItem('review_speed');
  if (savedSpeed) setSpeed(savedSpeed);
  const savedSeek = localStorage.getItem('review_seek_step');
  if (savedSeek) setSeekStep(savedSeek);
  const savedReviewer = localStorage.getItem('reviewer_name');
  if (savedReviewer) {
    const input = $('#reviewer-name');
    if (input) input.value = savedReviewer;
  }
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
        with metadata_lock:
            with self.metadata_file.open("r", encoding="utf-8-sig", newline="") as file:
                reader = csv.DictReader(file)
                fields = list(reader.fieldnames or [])
                if "is_verified" not in fields:
                    fields.append("is_verified")
                if "verified_by" not in fields:
                    fields.append("verified_by")
                rows = []
                for r in reader:
                    val = r.get("is_verified", "").strip().lower()
                    r["is_verified"] = "True" if val in ("true", "1", "yes") else "False"
                    r["verified_by"] = r.get("verified_by", "").strip()
                    rows.append(r)
                return fields, rows

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
        elif path == "/api/claims":
            clean_expired_claims()
            with claims_lock:
                now = time.time()
                data = {
                    str(idx): {
                        "user": c["user"],
                        "expires_in": max(0, int(CLAIM_TIMEOUT - (now - c["timestamp"]))),
                    }
                    for idx, c in active_claims.items()
                }
            self.send_json(data)
        elif path == "/api/export_csv":
            try:
                with metadata_lock:
                    data = self.metadata_file.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header("Content-Disposition", 'attachment; filename="metadata.csv"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except Exception as e:
                self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))
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
                self.send_header("Content-Disposition", 'inline; filename="preview"')
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
                    else:
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
                pass
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/claim":
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(content_length).decode("utf-8")) if content_length > 0 else {}
                row_index = int(payload.get("row_index", -1))
                user = str(payload.get("user", "")).strip() or "Ẩn danh"
                if row_index < 0:
                    self.send_json({"error": "Invalid row_index"}, HTTPStatus.BAD_REQUEST)
                    return

                clean_expired_claims()
                with claims_lock:
                    # Release other claims held by this user
                    for idx in list(active_claims.keys()):
                        if active_claims[idx].get("user") == user and idx != row_index:
                            del active_claims[idx]

                    # Check if claimed by someone else
                    if row_index in active_claims and active_claims[row_index].get("user") != user:
                        c = active_claims[row_index]
                        remaining = max(0, int(CLAIM_TIMEOUT - (time.time() - c["timestamp"])))
                        self.send_json({"ok": False, "claimed_by": c["user"], "expires_in": remaining})
                        return

                    active_claims[row_index] = {"user": user, "timestamp": time.time()}
                    self.send_json({"ok": True, "user": user})
            except Exception as e:
                self.send_json({"error": str(e)}, HTTPStatus.BAD_REQUEST)

        elif path == "/api/release":
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(content_length).decode("utf-8")) if content_length > 0 else {}
                row_index = int(payload.get("row_index", -1))
                user = str(payload.get("user", "")).strip()
                with claims_lock:
                    if row_index in active_claims:
                        if not user or active_claims[row_index].get("user") == user:
                            del active_claims[row_index]
                self.send_json({"ok": True})
            except Exception as e:
                self.send_json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
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

            with metadata_lock:
                fields, rows = self.read_rows()
                if row_index < 0 or row_index >= len(rows):
                    raise IndexError("row does not exist")

                if "text" in payload and isinstance(payload["text"], str):
                    rows[row_index]["text"] = payload["text"].strip()

                if "is_verified" in payload:
                    iv = payload["is_verified"]
                    is_ver_bool = iv is True or str(iv).strip().lower() in ("true", "1", "yes")
                    rows[row_index]["is_verified"] = "True" if is_ver_bool else "False"
                    if is_ver_bool:
                        reviewer = str(payload.get("reviewer", "")).strip()
                        if reviewer:
                            rows[row_index]["verified_by"] = reviewer
                    else:
                        rows[row_index]["verified_by"] = ""
                elif "verified_by" in payload:
                    rows[row_index]["verified_by"] = str(payload["verified_by"]).strip()

                if "is_verified" not in fields:
                    fields.append("is_verified")
                if "verified_by" not in fields:
                    fields.append("verified_by")

                temporary = self.metadata_file.with_suffix(".csv.tmp")
                with temporary.open("w", encoding="utf-8", newline="") as file:
                    writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
                    writer.writeheader()
                    writer.writerows(rows)
                os.replace(temporary, self.metadata_file)

            # Release claim on this row after saving
            with claims_lock:
                active_claims.pop(row_index, None)

            self.send_json({
                "ok": True,
                "is_verified": rows[row_index]["is_verified"] == "True",
                "verified_by": rows[row_index]["verified_by"],
            })
        except (ValueError, KeyError, IndexError, json.JSONDecodeError) as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        if not path.startswith("/api/rows/"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            row_index = int(path.rsplit("/", 1)[1])
            with metadata_lock:
                fields, rows = self.read_rows()
                if row_index < 0 or row_index >= len(rows):
                    raise IndexError("Dòng không tồn tại")

                target_row = rows[row_index]
                audio_rel_path = target_row.get("audio_path", "").strip()

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

                del rows[row_index]
                temporary = self.metadata_file.with_suffix(".csv.tmp")
                with temporary.open("w", encoding="utf-8", newline="") as file:
                    writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
                    writer.writeheader()
                    writer.writerows(rows)
                os.replace(temporary, self.metadata_file)

            # Adjust claims
            with claims_lock:
                active_claims.pop(row_index, None)
                new_claims = {}
                for c_idx, c_val in active_claims.items():
                    if c_idx > row_index:
                        new_claims[c_idx - 1] = c_val
                    else:
                        new_claims[c_idx] = c_val
                active_claims.clear()
                active_claims.update(new_claims)

            print(f"[review] Deleted row {row_index} and file '{audio_rel_path}' (deleted={file_deleted})")
            self.send_json({"ok": True, "file_deleted": file_deleted})
        except (ValueError, IndexError) as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except Exception as error:
            self.send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)


def main() -> None:
    import zipfile
    parser = argparse.ArgumentParser(description="Multi-user review app for audio and ASR metadata.")
    parser.add_argument("--metadata", default="cs_dataset/metadata.csv", help="Path to metadata.csv")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    metadata_file = Path(args.metadata).resolve()

    # Cloud / HF Spaces Auto-Unzip: Check if dataset zip exists and metadata.csv doesn't
    if not metadata_file.is_file():
        zip_candidates = [
            Path("cs_dataset.zip"),
            metadata_file.parent.with_suffix(".zip"),
            Path("dataset.zip"),
        ]
        for z in zip_candidates:
            if z.is_file():
                print(f"[review] Found dataset archive '{z}', auto-extracting...")
                try:
                    with zipfile.ZipFile(z, "r") as zf:
                        zf.extractall(".")
                    print(f"[review] Successfully extracted '{z}'.")
                except Exception as ze:
                    print(f"[review] Error extracting '{z}': {ze}")
                break

    if not metadata_file.is_file():
        parser.error(f"Metadata file not found: {metadata_file}")
    with metadata_file.open("r", encoding="utf-8-sig", newline="") as file:
        fields = csv.reader(file)
        header = next(fields, [])
    if "text" not in header:
        parser.error("metadata.csv must contain a 'text' column")
    ReviewServer.metadata_file = metadata_file
    ReviewServer.dataset_dir = metadata_file.parent

    # Support PORT environment variable (standard in Hugging Face Spaces / Docker / Cloud)
    port = int(os.getenv("PORT", args.port))
    server = ThreadingHTTPServer(("0.0.0.0", port), ReviewServer)
    print(f"Open http://127.0.0.1:{port} in your browser")
    print(f"Server listening on all network interfaces (0.0.0.0:{port}) for LAN/Multi-user access")
    print(f"Editing: {metadata_file}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
