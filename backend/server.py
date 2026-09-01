import os
import uuid
import subprocess
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

app = FastAPI(title="Zero Recap API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"

UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)


@app.get("/")
def home():
    return {
        "app": "Zero Recap",
        "status": "online",
        "message": "Zero Recap backend is running"
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/upload")
async def upload_video(file: UploadFile = File(...)):
    allowed = {
        "video/mp4",
        "video/mov",
        "video/quicktime",
        "video/webm",
        "video/x-matroska"
    }

    if file.content_type not in allowed:
        raise HTTPException(
            status_code=400,
            detail="Please upload a supported video file."
        )

    video_id = str(uuid.uuid4())
    extension = Path(file.filename or "video.mp4").suffix or ".mp4"

    input_path = UPLOAD_DIR / f"{video_id}{extension}"

    with open(input_path, "wb") as buffer:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            buffer.write(chunk)

    return {
        "success": True,
        "video_id": video_id,
        "filename": input_path.name,
        "message": "Video uploaded successfully."
    }


@app.post("/extract-audio/{video_id}")
def extract_audio(video_id: str):
    matches = list(UPLOAD_DIR.glob(f"{video_id}.*"))

    if not matches:
        raise HTTPException(
            status_code=404,
            detail="Video not found."
        )

    input_path = matches[0]
    audio_path = OUTPUT_DIR / f"{video_id}.mp3"

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-acodec",
        "libmp3lame",
        "-q:a",
        "4",
        str(audio_path)
    ]

    try:
        subprocess.run(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
    except subprocess.CalledProcessError:
        raise HTTPException(
            status_code=500,
            detail="Audio extraction failed. Make sure FFmpeg is installed."
        )

    return {
        "success": True,
        "video_id": video_id,
        "audio": f"/audio/{video_id}"
    }


@app.get("/audio/{video_id}")
def get_audio(video_id: str):
    audio_path = OUTPUT_DIR / f"{video_id}.mp3"

    if not audio_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Audio not found."
        )

    return FileResponse(
        audio_path,
        media_type="audio/mpeg",
        filename=audio_path.name
    )


@app.get("/video/{video_id}")
def get_video(video_id: str):
    matches = list(UPLOAD_DIR.glob(f"{video_id}.*"))

    if not matches:
        raise HTTPException(
            status_code=404,
            detail="Video not found."
        )

    return FileResponse(
        matches[0],
        media_type="video/mp4"
    )


@app.post("/create-recap/{video_id}")
def create_recap(video_id: str):
    """
    Placeholder for the AI recap pipeline.

    Next stage:
    1. Transcribe audio
    2. Ask AI to find important moments
    3. Extract highlight clips
    4. Combine clips
    5. Add captions
    6. Return final recap video
    """

    matches = list(UPLOAD_DIR.glob(f"{video_id}.*"))

    if not matches:
        raise HTTPException(
            status_code=404,
            detail="Video not found."
        )

    return {
        "success": True,
        "video_id": video_id,
        "status": "ready_for_ai_processing",
        "message": "AI recap pipeline is ready for the next stage."
    }
