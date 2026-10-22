"""FastAPI + Gradio bridge for Hugging Face Spaces (100% Free, No Credit Card Required).

Runs review_app UI natively at root '/' while mounting Gradio at '/gradio'
to satisfy Hugging Face Spaces Gradio SDK requirements.
"""

from __future__ import annotations

# Import spaces at the VERY TOP of the file (required by Hugging Face ZeroGPU)
try:
    import spaces
    import torch

    @spaces.GPU
    def _gpu_worker(n: float = 0.0) -> str:
        t = torch.zeros(1).cuda()
        return f"GPU Ready: {t.device}"
except Exception:
    def _gpu_worker(n: float = 0.0) -> str:
        return "CPU Ready"

import csv
import json
import os
import time
import zipfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
import gradio as gr
import uvicorn

from review_app import (
    APP_HTML,
    CLAIM_TIMEOUT,
    active_claims,
    claims_lock,
    clean_expired_claims,
    metadata_lock,
)

DATASET_DIR = Path("cs_dataset").resolve()
METADATA_FILE = (DATASET_DIR / "metadata.csv").resolve()

# Auto-unzip on startup if cs_dataset does not exist or metadata.csv is missing
if not METADATA_FILE.is_file():
    zip_candidates = [
        Path("cs_dataset.zip"),
        Path("dataset.zip"),
        DATASET_DIR.with_suffix(".zip"),
    ]
    for z in zip_candidates:
        if z.is_file():
            print(f"[app] Found dataset archive '{z}', extracting...")
            try:
                with zipfile.ZipFile(z, "r") as zf:
                    zf.extractall(".")
                print(f"[app] Successfully extracted '{z}'.")
            except Exception as ze:
                print(f"[app] Error extracting '{z}': {ze}")
            break


def read_metadata_rows() -> tuple[list[str], list[dict[str, str]]]:
    """Thread-safe reader for metadata.csv."""
    if not METADATA_FILE.is_file():
        return ["id", "audio_path", "text", "duration", "type", "speaker_id", "is_verified", "verified_by"], []
    with metadata_lock:
        with METADATA_FILE.open("r", encoding="utf-8-sig", newline="") as file:
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


# 1. Initialize FastAPI Application
app = FastAPI(title="ASR Transcript Reviewer")


# 2. Main UI Route (Serves the exact full-screen review app)
@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    return HTMLResponse(APP_HTML)


# 3. REST API Endpoints
@app.get("/api/rows")
async def get_rows():
    _, rows = read_metadata_rows()
    return [{**row, "row_index": i} for i, row in enumerate(rows)]


@app.get("/api/claims")
async def get_claims():
    clean_expired_claims()
    with claims_lock:
        now = time.time()
        return {
            str(idx): {
                "user": c["user"],
                "expires_in": max(0, int(CLAIM_TIMEOUT - (now - c["timestamp"]))),
            }
            for idx, c in active_claims.items()
        }


@app.post("/api/claim")
async def claim_sample(request: Request):
    payload = await request.json()
    row_index = int(payload.get("row_index", -1))
    user = str(payload.get("user", "")).strip() or "Ẩn danh"
    if row_index < 0:
        raise HTTPException(status_code=400, detail="Invalid row_index")

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
            return {"ok": False, "claimed_by": c["user"], "expires_in": remaining}

        active_claims[row_index] = {"user": user, "timestamp": time.time()}
        return {"ok": True, "user": user}


@app.post("/api/release")
async def release_sample(request: Request):
    payload = await request.json()
    row_index = int(payload.get("row_index", -1))
    user = str(payload.get("user", "")).strip()
    with claims_lock:
        if row_index in active_claims:
            if not user or active_claims[row_index].get("user") == user:
                del active_claims[row_index]
    return {"ok": True}


@app.put("/api/rows/{row_index}")
async def update_row(row_index: int, request: Request):
    payload = await request.json()
    with metadata_lock:
        fields, rows = read_metadata_rows()
        if row_index < 0 or row_index >= len(rows):
            raise HTTPException(status_code=404, detail="Row not found")

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

        temporary = METADATA_FILE.with_suffix(".csv.tmp")
        with temporary.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary, METADATA_FILE)

    with claims_lock:
        active_claims.pop(row_index, None)

    return {
        "ok": True,
        "is_verified": rows[row_index]["is_verified"] == "True",
        "verified_by": rows[row_index]["verified_by"],
    }


@app.delete("/api/rows/{row_index}")
async def delete_row(row_index: int):
    with metadata_lock:
        fields, rows = read_metadata_rows()
        if row_index < 0 or row_index >= len(rows):
            raise HTTPException(status_code=404, detail="Row not found")

        target_row = rows[row_index]
        audio_rel_path = target_row.get("audio_path", "").strip()

        file_deleted = False
        if audio_rel_path:
            audio_path = (DATASET_DIR / audio_rel_path).resolve()
            try:
                audio_path.relative_to(DATASET_DIR.resolve())
                if audio_path.is_file():
                    audio_path.unlink()
                    file_deleted = True
            except Exception as err:
                print(f"[app] Warning: Could not delete audio '{audio_path}': {err}")

        del rows[row_index]
        temporary = METADATA_FILE.with_suffix(".csv.tmp")
        with temporary.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary, METADATA_FILE)

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

    return {"ok": True, "file_deleted": file_deleted}


@app.get("/api/stream/{row_index}")
async def stream_audio(row_index: int):
    _, rows = read_metadata_rows()
    if row_index < 0 or row_index >= len(rows):
        raise HTTPException(status_code=404, detail="Sample not found")

    audio_rel = rows[row_index].get("audio_path", "").strip()
    audio_path = (DATASET_DIR / audio_rel).resolve()
    try:
        audio_path.relative_to(DATASET_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Forbidden audio path")

    if not audio_path.is_file():
        raise HTTPException(status_code=404, detail="Audio file not found on disk")

    # Starlette's FileResponse automatically handles HTTP Range requests (seeking)
    return FileResponse(audio_path, media_type="audio/wav", filename="preview.wav")


@app.get("/audio/{path:path}")
async def get_audio_file(path: str):
    audio_path = (DATASET_DIR / path).resolve()
    try:
        audio_path.relative_to(DATASET_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Forbidden audio path")

    if not audio_path.is_file():
        raise HTTPException(status_code=404, detail="Audio file not found")

    return FileResponse(audio_path)


@app.get("/api/export_csv")
async def export_csv():
    if not METADATA_FILE.is_file():
        raise HTTPException(status_code=404, detail="metadata.csv not found")
    return FileResponse(
        METADATA_FILE,
        media_type="text/csv",
        filename="metadata.csv",
        headers={"Content-Disposition": 'attachment; filename="metadata.csv"'},
    )


# 4. Mount Gradio interface to satisfy Hugging Face Spaces Gradio SDK & ZeroGPU scanner
with gr.Blocks(title="ASR Transcript Reviewer Backend") as demo:
    n_in = gr.Number(value=0, visible=False)
    t_out = gr.Textbox(visible=False)
    btn = gr.Button("GPU Check", visible=False)
    btn.click(fn=_gpu_worker, inputs=n_in, outputs=t_out)

app = gr.mount_gradio_app(app, demo, path="/gradio")


# 5. Local development entrypoint
if __name__ == "__main__":
    port = int(os.getenv("PORT", "7860"))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
