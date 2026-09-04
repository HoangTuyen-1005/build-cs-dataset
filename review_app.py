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
    :root { color-scheme: light; font-family: system-ui, sans-serif; }
    body { max-width: 940px; margin: 32px auto; padding: 0 20px; color: #1f2937; }
    header, .actions, .details { display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
    header { justify-content:space-between; } h1 { margin:0; font-size:1.45rem; }
    button { border:0; border-radius:7px; padding:9px 14px; font-size:1rem; cursor:pointer; background:#e5e7eb; }
    button:hover { background:#d1d5db; } #save { background:#2563eb; color:white; } #save:hover { background:#1d4ed8; }
    audio { width:100%; margin:24px 0 16px; } textarea { width:100%; min-height:240px; box-sizing:border-box; padding:14px; font:1.05rem/1.55 system-ui,sans-serif; border:1px solid #9ca3af; border-radius:8px; }
    .details { color:#4b5563; font-size:.9rem; margin:10px 0 18px; } #status { min-height:1.5em; color:#047857; } #status.error { color:#b91c1c; }
    .player-tools { display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin:4px 0 12px; } .player-tools input { width:180px; }
    kbd { border:1px solid #9ca3af; border-radius:4px; padding:1px 5px; background:#f9fafb; }
  </style>
</head>
<body>
  <header><h1>ASR Transcript Reviewer</h1><strong id="counter"></strong></header>
  <p class="details">Phím tắt: <kbd>Space</kbd> phát/dừng, <kbd>←</kbd>/<kbd>→</kbd> lùi/tới 5 giây, <kbd>Ctrl</kbd>+<kbd>S</kbd> lưu.</p>
  <div class="details" id="details"></div>
  <audio id="audio" controls preload="metadata"></audio>
  <div class="player-tools">
    <button id="rewind" title="Lùi theo bước tua đã chọn">↶ Tua lùi</button><button id="forward" title="Tới theo bước tua đã chọn">Tua tới ↷</button>
    <label for="seek-step">Bước tua: <strong id="seek-value">5 giây</strong></label>
    <input id="seek-step" type="range" min="1" max="5" step="1" value="5" aria-label="Số giây mỗi lần tua">
    <label for="speed">Tốc độ: <strong id="speed-value">1.00×</strong></label>
    <input id="speed" type="range" min="0.5" max="2" step="0.05" value="1" aria-label="Tốc độ phát audio">
    <button id="normal-speed">1×</button>
  </div>
  <textarea id="text" spellcheck="false" placeholder="Chưa có transcript..."></textarea>
  <div class="actions" style="margin-top:16px"><button id="prev">← Trước</button><button id="save">Lưu thay đổi</button><button id="next">Tiếp →</button><span id="status"></span></div>
<script>
let rows=[], index=0, dirty=false;
const $ = s => document.querySelector(s);
const status = (message, error=false) => { $('#status').textContent=message; $('#status').className=error?'error':''; };
function seekBy(seconds) { const audio=$('#audio'); const duration=Number.isFinite(audio.duration) ? audio.duration : Infinity; audio.currentTime=Math.max(0, Math.min(duration, (audio.currentTime || 0) + seconds)); }
function seekStep() { return Number($('#seek-step').value); }
function setSeekStep(value) { const seconds=Math.max(1, Math.min(5, Number(value))); $('#seek-step').value=seconds; $('#seek-value').textContent=seconds+' giây'; }
function setSpeed(value) { const speed=Math.max(.5, Math.min(2, Number(value))); $('#audio').playbackRate=speed; $('#speed').value=speed; $('#speed-value').textContent=speed.toFixed(2)+'×'; }
async function load() { rows=await (await fetch('/api/rows')).json(); if (!rows.length) { status('metadata.csv chưa có dòng dữ liệu.', true); return; } render(); }
function render() { const r=rows[index]; dirty=false; $('#counter').textContent=`${index+1} / ${rows.length}`; $('#details').textContent=`${r.audio_path}  •  ${r.duration || '?'} giây  •  ${r.type || ''}  •  ${r.speaker_id || ''}`; $('#audio').src='/audio/'+encodeURIComponent(r.audio_path); $('#text').value=r.text || ''; status(''); }
async function save(go=0) { if (!rows.length) return; const r=rows[index], text=$('#text').value; if (!dirty && !go) { status('Không có thay đổi.'); return; } status('Đang lưu...'); const response=await fetch('/api/rows/'+r.row_index, {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text})}); const result=await response.json(); if (!response.ok) { status(result.error || 'Không thể lưu.', true); return; } r.text=text; dirty=false; status('Đã lưu.'); if (go) { index=Math.max(0,Math.min(rows.length-1,index+go)); render(); } }
$('#text').addEventListener('input',()=>{dirty=true; status('Có thay đổi chưa lưu.');});
$('#save').onclick=()=>save(); $('#prev').onclick=()=>save(-1); $('#next').onclick=()=>save(1);
$('#rewind').onclick=()=>seekBy(-seekStep()); $('#forward').onclick=()=>seekBy(seekStep()); $('#seek-step').oninput=e=>setSeekStep(e.target.value); $('#speed').oninput=e=>setSpeed(e.target.value); $('#normal-speed').onclick=()=>setSpeed(1);
document.addEventListener('keydown', e=>{ if ((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='s') {e.preventDefault(); save(); return;} if (e.target=== $('#text')) return; if (e.code==='Space') {e.preventDefault(); $('#audio').paused?$('#audio').play():$('#audio').pause();} if(e.key==='ArrowLeft') {e.preventDefault(); seekBy(-seekStep());} if(e.key==='ArrowRight') {e.preventDefault(); seekBy(seekStep());} });
load().catch(e=>status('Không đọc được metadata: '+e.message,true));
</script></body></html>"""


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
