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

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def get_ffmpeg():
    """
    Render မှာ FFmpeg executable ရှိရင် အဲဒါကိုသုံးမယ်။
    မရှိရင် imageio-ffmpeg ကနေ FFmpeg ရယူမယ်။
    """

    system_ffmpeg = "ffmpeg"

    try:
        result = subprocess.run(
            [system_ffmpeg, "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10
        )

        if result.returncode == 0:
            return system_ffmpeg

    except Exception:
        pass

    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()

    except Exception:
        raise HTTPException(
            status_code=500,
            detail="FFmpeg မတွေ့ပါ။ requirements.txt ထဲမှာ imageio-ffmpeg ပါကြောင်းစစ်ပါ။"
        )


@app.get("/")
def home():
    return {
        "app": "Zero Recap",
        "status": "online",
        "message": "Zero Recap backend is running"
    }


@app.get("/health")
def health():
    return {
        "status": "ok"
    }


# ==========================================
# UPLOAD
# ==========================================

@app.post("/upload")
async def upload_video(
    file: UploadFile = File(...)
):

    if not file.filename:
        raise HTTPException(
            status_code=400,
            detail="Video file မပါပါ။"
        )

    allowed_extensions = {
        ".mp4",
        ".mov",
        ".webm",
        ".mkv",
        ".avi"
    }

    extension = Path(
        file.filename
    ).suffix.lower()

    if extension not in allowed_extensions:

        raise HTTPException(
            status_code=400,
            detail="Supported video format မဟုတ်ပါ။"
        )

    video_id = str(uuid.uuid4())

    input_path = (
        UPLOAD_DIR /
        f"{video_id}{extension}"
    )

    try:

        with open(
            input_path,
            "wb"
        ) as buffer:

            while True:

                chunk = await file.read(
                    1024 * 1024
                )

                if not chunk:
                    break

                buffer.write(chunk)

    except Exception as e:

        if input_path.exists():
            input_path.unlink()

        raise HTTPException(
            status_code=500,
            detail=f"Upload failed: {str(e)}"
        )

    return {
        "success": True,
        "video_id": video_id,
        "filename": input_path.name
    }


# ==========================================
# FIND VIDEO
# ==========================================

def find_video(video_id: str):

    matches = list(
        UPLOAD_DIR.glob(
            f"{video_id}.*"
        )
    )

    if not matches:

        raise HTTPException(
            status_code=404,
            detail="Video not found."
        )

    return matches[0]


# ==========================================
# EXTRACT AUDIO
# ==========================================

@app.post("/extract-audio/{video_id}")
def extract_audio(
    video_id: str
):

    input_path = find_video(video_id)

    audio_path = (
        OUTPUT_DIR /
        f"{video_id}.mp3"
    )

    ffmpeg = get_ffmpeg()

    command = [
        ffmpeg,
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-ac",
        "2",
        "-ar",
        "44100",
        "-codec:a",
        "libmp3lame",
        "-q:a",
        "4",
        str(audio_path)
    ]

    try:

        process = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=f"FFmpeg error: {str(e)}"
        )

    if process.returncode != 0:

        print(process.stderr)

        raise HTTPException(
            status_code=500,
            detail="Audio extraction failed."
        )

    if not audio_path.exists():

        raise HTTPException(
            status_code=500,
            detail="Audio file မထွက်လာပါ။"
        )

    return {
        "success": True,
        "video_id": video_id,
        "audio_url": f"/audio/{video_id}"
    }


# ==========================================
# AUDIO
# ==========================================

@app.get("/audio/{video_id}")
def get_audio(
    video_id: str
):

    audio_path = (
        OUTPUT_DIR /
        f"{video_id}.mp3"
    )

    if not audio_path.exists():

        raise HTTPException(
            status_code=404,
            detail="Audio not found."
        )

    return FileResponse(
        path=audio_path,
        media_type="audio/mpeg",
        filename=audio_path.name
    )


# ==========================================
# CREATE RECAP VIDEO
# ==========================================

@app.post("/create-recap/{video_id}")
def create_recap(
    video_id: str
):

    input_path = find_video(video_id)

    recap_path = (
        OUTPUT_DIR /
        f"{video_id}_recap.mp4"
    )

    ffmpeg = get_ffmpeg()

    command = [
        ffmpeg,
        "-y",
        "-i",
        str(input_path),

        "-map",
        "0:v:0",

        "-map",
        "0:a:0?",

        "-c:v",
        "libx264",

        "-preset",
        "veryfast",

        "-crf",
        "28",

        "-c:a",
        "aac",

        "-b:a",
        "128k",

        "-movflags",
        "+faststart",

        str(recap_path)
    ]

    try:

        process = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=f"Recap processing error: {str(e)}"
        )

    if process.returncode != 0:

        print(process.stderr)

        raise HTTPException(
            status_code=500,
            detail="Recap Video ပြုလုပ်မအောင်မြင်ပါ။"
        )

    if not recap_path.exists():

        raise HTTPException(
            status_code=500,
            detail="Recap MP4 file မထွက်လာပါ။"
        )

    return {
        "success": True,
        "video_id": video_id,
        "status": "completed",
        "video_url": f"/download/{video_id}",
        "download_url": f"/download/{video_id}",
        "message": "Recap Video ပြီးပါပြီ။"
    }


# ==========================================
# DOWNLOAD FINAL RECAP
# ==========================================

@app.get("/download/{video_id}")
def download_recap(
    video_id: str
):

    recap_path = (
        OUTPUT_DIR /
        f"{video_id}_recap.mp4"
    )

    if not recap_path.exists():

        raise HTTPException(
            status_code=404,
            detail="Recap Video မတွေ့ပါ။"
        )

    return FileResponse(
        path=recap_path,
        media_type="video/mp4",
        filename="zero-recap.mp4",
        content_disposition_type="attachment"
    )


# ==========================================
# VIEW ORIGINAL VIDEO
# ==========================================

@app.get("/video/{video_id}")
def get_video(
    video_id: str
):

    input_path = find_video(video_id)

    return FileResponse(
        path=input_path,
        media_type="video/mp4"
    )
