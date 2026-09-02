import os
import uuid
import base64
import subprocess
from pathlib import Path

import httpx
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from faster_whisper import WhisperModel

app = FastAPI(title="Zero Recap")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
VISION_MODEL = os.getenv("VISION_MODEL", "qwen2.5vl:3b")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")

_whisper = None


def get_whisper():
    global _whisper

    if _whisper is None:
        _whisper = WhisperModel(
            WHISPER_MODEL,
            device="cpu",
            compute_type="int8",
        )

    return _whisper


def ffmpeg_path():
    possible = [
        "ffmpeg",
        "/usr/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
    ]

    for path in possible:
        try:
            subprocess.run(
                [path, "-version"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return path
        except Exception:
            pass

    raise RuntimeError("FFmpeg not found")


def run_ffmpeg(args):
    cmd = [ffmpeg_path(), "-y"] + args

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode(errors="ignore"))

    return result


def extract_audio(video_path: Path, audio_path: Path):
    run_ffmpeg([
        "-i", str(video_path),
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-c:a", "pcm_s16le",
        str(audio_path),
    ])


def extract_frame(video_path: Path, frame_path: Path, second: int):
    run_ffmpeg([
        "-ss", str(second),
        "-i", str(video_path),
        "-frames:v", "1",
        "-q:v", "3",
        str(frame_path),
    ])


def get_video_duration(video_path: Path):
    result = subprocess.run(
        [
            ffmpeg_path(),
            "-i",
            str(video_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    text = result.stderr.decode(errors="ignore")

    import re

    match = re.search(
        r"Duration:\s*(\d+):(\d+):([\d.]+)",
        text,
    )

    if not match:
        return 60

    hours = int(match.group(1))
    minutes = int(match.group(2))
    seconds = float(match.group(3))

    return int(hours * 3600 + minutes * 60 + seconds)


def transcribe(audio_path: Path):
    model = get_whisper()

    segments, info = model.transcribe(
        str(audio_path),
        beam_size=5,
    )

    text_parts = []

    for segment in segments:
        text_parts.append(segment.text.strip())

    return " ".join(text_parts).strip()


def image_to_base64(path: Path):
    return base64.b64encode(path.read_bytes()).decode("utf-8")


async def ask_ollama(prompt: str, images=None):
    content = []

    if images:
        content.append({
            "type": "text",
            "text": prompt,
        })

        for image in images:
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": "data:image/jpeg;base64," + image
                },
            })
    else:
        content = prompt

    payload = {
        "model": VISION_MODEL,
        "messages": [
            {
                "role": "user",
                "content": content,
            }
        ],
        "stream": False,
        "options": {
            "temperature": 0.4,
        },
    }

    async with httpx.AsyncClient(timeout=600) as client:
        response = await client.post(
            f"{OLLAMA_URL}/api/chat",
            json=payload,
        )

    if response.status_code != 200:
        raise RuntimeError(
            f"Ollama error: {response.text}"
        )

    data = response.json()

    return data["message"]["content"].strip()


async def create_story_script(transcript, images, style):
    prompt = f"""
You are the story editor for a Burmese movie recap channel.

Analyze the provided video frames and transcript.

Write a natural Burmese narration script for a movie/video recap.

Style: {style}

Requirements:
- Write in natural spoken Burmese.
- Explain the story clearly.
- Focus on important events.
- Mention important characters and what they do.
- Keep the story chronological.
- Do not invent events that are not supported by the video.
- Do not use English unless a name needs it.
- Do not include headings.
- Do not include bullet points.
- Write only the narration script.
- Make it suitable for AI voice narration.

Transcript:
{transcript}
"""

    return await ask_ollama(
        prompt,
        images=images,
    )


async def create_voice(text, output_path, voice="female"):
    try:
        import edge_tts
    except ImportError:
        raise RuntimeError(
            "edge-tts is not installed"
        )

    if voice == "male":
        voice_name = "my-MM-ThihaNeural"
    else:
        voice_name = "my-MM-NilarNeural"

    communicate = edge_tts.Communicate(
        text,
        voice_name,
    )

    await communicate.save(str(output_path))


def combine_video_audio(video_path, narration_path, output_path):
    run_ffmpeg([
        "-i", str(video_path),
        "-i", str(narration_path),
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac",
        "-shortest",
        str(output_path),
    ])


@app.get("/")
def root():
    return {
        "name": "Zero Recap",
        "status": "running",
        "mode": "local-ai",
    }


@app.get("/health")
def health():
    return {
        "ok": True,
        "mode": "local-ai",
        "ollama": OLLAMA_URL,
        "vision_model": VISION_MODEL,
        "whisper_model": WHISPER_MODEL,
    }


@app.post("/upload")
async def upload_video(file: UploadFile = File(...)):
    video_id = str(uuid.uuid4())

    extension = Path(file.filename or ".mp4").suffix

    if not extension:
        extension = ".mp4"

    video_path = DATA_DIR / f"{video_id}{extension}"

    content = await file.read()

    video_path.write_bytes(content)

    return {
        "video_id": video_id,
        "filename": file.filename,
        "size": len(content),
    }


@app.post("/create-recap/{video_id}")
async def create_recap(
    video_id: str,
    voice: str = "female",
    style: str = "natural",
):
    files = list(DATA_DIR.glob(f"{video_id}.*"))

    if not files:
        raise HTTPException(
            status_code=404,
            detail="Video not found",
        )

    video_path = files[0]

    audio_path = DATA_DIR / f"{video_id}_audio.wav"
    script_path = DATA_DIR / f"{video_id}_script.txt"
    narration_path = DATA_DIR / f"{video_id}_narration.mp3"
    output_path = DATA_DIR / f"{video_id}_recap.mp4"

    try:
        # 1. Extract audio
        extract_audio(
            video_path,
            audio_path,
        )

        # 2. Transcribe locally
        transcript = transcribe(
            audio_path
        )

        # 3. Extract a few visual frames
        duration = get_video_duration(
            video_path
        )

        frame_times = [
            0,
            max(1, duration // 4),
            max(1, duration // 2),
            max(1, (duration * 3) // 4),
        ]

        images = []

        for index, second in enumerate(frame_times):
            frame_path = DATA_DIR / (
                f"{video_id}_frame_{index}.jpg"
            )

            extract_frame(
                video_path,
                frame_path,
                second,
            )

            if frame_path.exists():
                images.append(
                    image_to_base64(frame_path)
                )

        # 4. AI creates Burmese story
        script = await create_story_script(
            transcript,
            images,
            style,
        )

        script_path.write_text(
            script,
            encoding="utf-8",
        )

        # 5. Burmese AI voice
        await create_voice(
            script,
            narration_path,
            voice,
        )

        # 6. Combine original video + Burmese narration
        combine_video_audio(
            video_path,
            narration_path,
            output_path,
        )

        return {
            "success": True,
            "video_id": video_id,
            "script": script,
            "download": f"/download/{video_id}",
        }

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": str(e),
            },
        )


@app.get("/script/{video_id}")
def get_script(video_id: str):
    path = DATA_DIR / f"{video_id}_script.txt"

    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail="Script not found",
        )

    return {
        "video_id": video_id,
        "script": path.read_text(
            encoding="utf-8"
        ),
    }


@app.get("/download/{video_id}")
def download(video_id: str):
    path = DATA_DIR / f"{video_id}_recap.mp4"

    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail="Recap video not found",
        )

    return FileResponse(
        path,
        media_type="video/mp4",
        filename=f"{video_id}_recap.mp4",
    )


@app.get("/video/{video_id}")
def video(video_id: str):
    files = list(DATA_DIR.glob(f"{video_id}.*"))

    if not files:
        raise HTTPException(
            status_code=404,
            detail="Video not found",
        )

    return FileResponse(
        files[0],
        media_type="video/mp4",
    )
