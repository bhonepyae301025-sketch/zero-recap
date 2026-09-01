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


# =========================
# UPLOAD VIDEO
# =========================

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

    extension = Path(
        file.filename or "video.mp4"
    ).suffix.lower() or ".mp4"

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


# =========================
# GET VIDEO DURATION
# =========================

def get_video_duration(video_path: Path) -> float:

    command = [
        "ffprobe",
        "-v", "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video_path)
    ]

    try:
        result = subprocess.run(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        return float(result.stdout.strip())

    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Could not read video duration."
        )


# =========================
# EXTRACT AUDIO
# =========================

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


# =========================
# GET AUDIO
# =========================

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


# =========================
# GET ORIGINAL VIDEO
# =========================

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


# =========================
# CREATE RECAP VIDEO
# =========================

@app.post("/create-recap/{video_id}")
def create_recap(video_id: str):

    matches = list(UPLOAD_DIR.glob(f"{video_id}.*"))

    if not matches:
        raise HTTPException(
            status_code=404,
            detail="Video not found."
        )

    input_path = matches[0]

    recap_path = OUTPUT_DIR / f"{video_id}_recap.mp4"

    duration = get_video_duration(input_path)

    # ---------------------------------
    # SHORT VIDEO
    # ---------------------------------

    if duration <= 45:

        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(recap_path)
        ]

    # ---------------------------------
    # LONG VIDEO
    # Create 3 highlight sections:
    #
    # Beginning
    # Middle
    # Ending
    #
    # Total approximately 45 seconds
    # ---------------------------------

    else:

        segment = 15

        middle_start = max(
            0,
            (duration / 2) - (segment / 2)
        )

        end_start = max(
            0,
            duration - segment
        )

        filter_complex = (
            f"[0:v]trim=start=0:end={segment},"
            f"setpts=PTS-STARTPTS[v0];"

            f"[0:a]atrim=start=0:end={segment},"
            f"asetpts=PTS-STARTPTS[a0];"

            f"[0:v]trim=start={middle_start}:end={middle_start + segment},"
            f"setpts=PTS-STARTPTS[v1];"

            f"[0:a]atrim=start={middle_start}:end={middle_start + segment},"
            f"asetpts=PTS-STARTPTS[a1];"

            f"[0:v]trim=start={end_start}:end={duration},"
            f"setpts=PTS-STARTPTS[v2];"

            f"[0:a]atrim=start={end_start}:end={duration},"
            f"asetpts=PTS-STARTPTS[a2];"

            f"[v0][a0][v1][a1][v2][a2]"
            f"concat=n=3:v=1:a=1[outv][outa]"
        )

        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),

            "-filter_complex",
            filter_complex,

            "-map",
            "[outv]",
            "-map",
            "[outa]",

            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",

            "-c:a",
            "aac",
            "-b:a",
            "128k",

            "-movflags",
            "+faststart",

            str(recap_path)
        ]

    try:

        subprocess.run(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

    except subprocess.CalledProcessError as error:

        error_message = error.stderr.decode(
            errors="ignore"
        ) if isinstance(error.stderr, bytes) else str(error.stderr)

        raise HTTPException(
            status_code=500,
            detail=f"Recap video creation failed: {error_message[-1000:]}"
        )

    if not recap_path.exists():
        raise HTTPException(
            status_code=500,
            detail="Recap video was not created."
        )

    return {
        "success": True,
        "video_id": video_id,
        "status": "completed",
        "message": "Recap video created successfully.",
        "recap_video": f"/recap/{video_id}",
        "video_url": f"/recap/{video_id}"
    }


# =========================
# GET FINAL RECAP VIDEO
# =========================

@app.get("/recap/{video_id}")
def get_recap(video_id: str):

    recap_path = OUTPUT_DIR / f"{video_id}_recap.mp4"

    if not recap_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Recap video not found."
        )

    return FileResponse(
        recap_path,
        media_type="video/mp4",
        filename=f"{video_id}_recap.mp4"
    )
