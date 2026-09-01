import os
import uuid
import base64
import subprocess
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from openai import OpenAI


# =========================================================
# APP
# =========================================================

app = FastAPI(title="Zero Recap AI")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================================================
# DIRECTORIES
# =========================================================

BASE_DIR = Path(__file__).resolve().parent

UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
FRAME_DIR = BASE_DIR / "frames"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
FRAME_DIR.mkdir(parents=True, exist_ok=True)


# =========================================================
# OPENAI
# =========================================================

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not OPENAI_API_KEY:
    print("WARNING: OPENAI_API_KEY မတွေ့ပါ။ Render Environment Variable ထဲထည့်ပါ။")

client = OpenAI(
    api_key=OPENAI_API_KEY
) if OPENAI_API_KEY else None


TEXT_MODEL = os.getenv(
    "OPENAI_TEXT_MODEL",
    "gpt-5.6-luna"
)

TRANSCRIBE_MODEL = os.getenv(
    "OPENAI_TRANSCRIBE_MODEL",
    "gpt-4o-transcribe"
)

TTS_MODEL = os.getenv(
    "OPENAI_TTS_MODEL",
    "gpt-4o-mini-tts"
)

TTS_VOICE = os.getenv(
    "OPENAI_TTS_VOICE",
    "marin"
)


# =========================================================
# FFMPEG
# =========================================================

def get_ffmpeg():

    try:

        result = subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10
        )

        if result.returncode == 0:
            return "ffmpeg"

    except Exception:
        pass

    try:

        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()

    except Exception:

        raise HTTPException(
            status_code=500,
            detail="FFmpeg မတွေ့ပါ။ imageio-ffmpeg ကို requirements.txt ထဲထည့်ထားကြောင်း စစ်ပါ။"
        )


# =========================================================
# ROOT
# =========================================================

@app.get("/")
def home():

    return {
        "app": "Zero Recap AI",
        "status": "online",
        "message": "AI Burmese Recap Backend is running"
    }


# =========================================================
# HEALTH
# =========================================================

@app.get("/health")
def health():

    return {
        "status": "ok",
        "openai_configured": bool(OPENAI_API_KEY)
    }


# =========================================================
# UPLOAD
# =========================================================

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
        ".avi",
        ".m4v"
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


# =========================================================
# FIND VIDEO
# =========================================================

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


# =========================================================
# EXTRACT AUDIO
# =========================================================

def make_audio(video_id: str):

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
        "1",

        "-ar",
        "16000",

        "-codec:a",
        "libmp3lame",

        "-b:a",
        "64k",

        str(audio_path)
    ]

    process = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
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

    return audio_path


@app.post("/extract-audio/{video_id}")
def extract_audio(video_id: str):

    audio_path = make_audio(video_id)

    return {
        "success": True,
        "video_id": video_id,
        "audio_url": f"/audio/{video_id}"
    }


# =========================================================
# AUDIO DOWNLOAD
# =========================================================

@app.get("/audio/{video_id}")
def get_audio(video_id: str):

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


# =========================================================
# TRANSCRIBE VIDEO AUDIO
# =========================================================

def transcribe_audio(audio_path: Path):

    if not client:

        raise HTTPException(
            status_code=500,
            detail="OPENAI_API_KEY မသတ်မှတ်ထားပါ။"
        )

    try:

        with open(
            audio_path,
            "rb"
        ) as audio_file:

            result = client.audio.transcriptions.create(
                model=TRANSCRIBE_MODEL,
                file=audio_file,
                response_format="text"
            )

        return str(result)

    except Exception as e:

        print("TRANSCRIPTION ERROR:", e)

        raise HTTPException(
            status_code=500,
            detail=f"AI transcription failed: {str(e)}"
        )


# =========================================================
# EXTRACT VIDEO FRAMES
# =========================================================

def extract_frames(
    video_path: Path,
    video_id: str,
    count: int = 8
):

    ffmpeg = get_ffmpeg()

    folder = FRAME_DIR / video_id

    folder.mkdir(
        parents=True,
        exist_ok=True
    )

    # Get duration
    probe = subprocess.run(
        [
            ffmpeg,
            "-i",
            str(video_path)
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    stderr = probe.stderr

    duration = 0.0

    import re

    match = re.search(
        r"Duration:\s*(\d+):(\d+):(\d+\.\d+)",
        stderr
    )

    if match:

        hours = int(match.group(1))
        minutes = int(match.group(2))
        seconds = float(match.group(3))

        duration = (
            hours * 3600
            + minutes * 60
            + seconds
        )

    if duration <= 0:

        return []

    # Don't send too many images to AI.
    count = min(
        max(count, 4),
        10
    )

    interval = duration / (count + 1)

    frame_paths = []

    for i in range(1, count + 1):

        timestamp = interval * i

        frame_path = (
            folder /
            f"frame_{i}.jpg"
        )

        command = [
            ffmpeg,
            "-y",

            "-ss",
            str(timestamp),

            "-i",
            str(video_path),

            "-frames:v",
            "1",

            "-vf",
            "scale=768:-2",

            "-q:v",
            "5",

            str(frame_path)
        ]

        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

        if (
            result.returncode == 0
            and frame_path.exists()
        ):

            frame_paths.append(
                frame_path
            )

    return frame_paths


# =========================================================
# AI SCRIPT GENERATION
# =========================================================

def create_burmese_script(
    transcript: str,
    frame_paths: list
):

    if not client:

        raise HTTPException(
            status_code=500,
            detail="OPENAI_API_KEY မသတ်မှတ်ထားပါ။"
        )

    content = []

    prompt = """
ဒီ video ကို မြန်မာဘာသာနဲ့ YouTube/TikTok style
AI Recap Video အတွက် ဇာတ်လမ်းပြန်ပြောတဲ့ script
ရေးပေးပါ။

အရေးကြီးတာတွေ -

1. Video ထဲက ဇာတ်လမ်းကို အဓိကထားပါ။
2. ဇာတ်ကောင်တွေ၊ ဖြစ်ရပ်တွေ၊ ပြဿနာ၊ အလှည့်အပြောင်း၊
   အဆုံးသတ်ကို နားလည်လွယ်အောင် ဆက်စပ်ရေးပါ။
3. မသေချာတဲ့အချက်ကို မဖန်တီးပါနဲ့။
4. Original dialogue ကို တိုက်ရိုက်မကူးပါနဲ့။
5. မြန်မာစကားပြောသလို သဘာဝကျကျရေးပါ။
6. စာဖတ်အသံထုတ်မှာဖြစ်လို့ emoji မထည့်ပါနဲ့။
7. Heading မထည့်ပါနဲ့။
8. Markdown မသုံးပါနဲ့။
9. Script တစ်ခုတည်းပဲ ပြန်ပေးပါ။
10. အရမ်းရှည်မရေးပါနဲ့။ 2-4 မိနစ်ခန့်
    narration လုပ်လို့ရမယ့် အရှည်ကို ရည်ရွယ်ပါ။

ဒီလိုပုံစံမျိုးဖြစ်ရမယ် -

"ဒီဇာတ်လမ်းကတော့...
အစပိုင်းမှာ...
ဒါပေမယ့်...
နောက်ပိုင်းမှာ...
နောက်ဆုံးမှာ..."

အသံဖတ်ရင် စိတ်ဝင်စားဖို့ကောင်းတဲ့
မြန်မာ narration style ဖြစ်အောင်ရေးပါ။
"""

    content.append(
        {
            "type": "input_text",
            "text": prompt
        }
    )

    content.append(
        {
            "type": "input_text",
            "text": (
                "\n\nVIDEO AUDIO TRANSCRIPT:\n"
                + transcript[:50000]
            )
        }
    )

    # Add selected video frames so AI can understand
    # visual scenes as well as dialogue.
    for frame_path in frame_paths:

        try:

            data = base64.b64encode(
                frame_path.read_bytes()
            ).decode("utf-8")

            content.append(
                {
                    "type": "input_image",
                    "image_url": (
                        "data:image/jpeg;base64,"
                        + data
                    ),
                    "detail": "low"
                }
            )

        except Exception as e:

            print(
                "FRAME READ ERROR:",
                e
            )

    try:

        response = client.responses.create(
            model=TEXT_MODEL,
            input=[
                {
                    "role": "user",
                    "content": content
                }
            ]
        )

        script = response.output_text.strip()

    except Exception as e:

        print(
            "SCRIPT GENERATION ERROR:",
            e
        )

        raise HTTPException(
            status_code=500,
            detail=f"AI script generation failed: {str(e)}"
        )

    if not script:

        raise HTTPException(
            status_code=500,
            detail="AI က script မထုတ်ပေးနိုင်ပါ။"
        )

    return script


# =========================================================
# SAVE SCRIPT
# =========================================================

def save_script(
    video_id: str,
    script: str
):

    script_path = (
        OUTPUT_DIR /
        f"{video_id}_script.txt"
    )

    script_path.write_text(
        script,
        encoding="utf-8"
    )

    return script_path


# =========================================================
# TTS
# =========================================================

def create_burmese_voice(
    video_id: str,
    script: str
):

    if not client:

        raise HTTPException(
            status_code=500,
            detail="OPENAI_API_KEY မသတ်မှတ်ထားပါ။"
        )

    voice_path = (
        OUTPUT_DIR /
        f"{video_id}_narration.mp3"
    )

    # TTS input has a 4096 character limit.
    # Keep a safe margin.
    text = script[:3800]

    try:

        response = client.audio.speech.create(
            model=TTS_MODEL,
            voice=TTS_VOICE,
            input=text,
            response_format="mp3",
            instructions=(
                "Speak naturally in Burmese (Myanmar language). "
                "Use a clear, warm documentary narrator voice. "
                "Speak at a moderate pace. "
                "Make the narration sound like a movie recap."
            )
        )

        response.stream_to_file(
            voice_path
        )

    except Exception as e:

        print(
            "TTS ERROR:",
            e
        )

        raise HTTPException(
            status_code=500,
            detail=f"Burmese AI voice generation failed: {str(e)}"
        )

    if not voice_path.exists():

        raise HTTPException(
            status_code=500,
            detail="AI narration audio မထွက်လာပါ။"
        )

    return voice_path


# =========================================================
# CREATE FINAL RECAP VIDEO
# =========================================================

def render_recap_video(
    video_id: str,
    video_path: Path,
    narration_path: Path
):

    ffmpeg = get_ffmpeg()

    recap_path = (
        OUTPUT_DIR /
        f"{video_id}_recap.mp4"
    )

    # Keep original video image.
    # Remove original audio.
    # Put AI Burmese narration as the main audio.
    #
    # If original audio exists, keep it very quietly
    # underneath the narration.

    command = [
        ffmpeg,
        "-y",

        "-i",
        str(video_path),

        "-i",
        str(narration_path),

        "-map",
        "0:v:0",

        "-map",
        "1:a:0",

        "-c:v",
        "libx264",

        "-preset",
        "veryfast",

        "-crf",
        "27",

        "-pix_fmt",
        "yuv420p",

        "-c:a",
        "aac",

        "-b:a",
        "128k",

        "-shortest",

        "-movflags",
        "+faststart",

        str(recap_path)
    ]

    process = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if process.returncode != 0:

        print(
            process.stderr
        )

        raise HTTPException(
            status_code=500,
            detail="Final Recap Video ပြုလုပ်မအောင်မြင်ပါ။"
        )

    if not recap_path.exists():

        raise HTTPException(
            status_code=500,
            detail="Final Recap MP4 မထွက်လာပါ။"
        )

    return recap_path


# =========================================================
# CREATE RECAP
# =========================================================

@app.post("/create-recap/{video_id}")
def create_recap(
    video_id: str
):

    if not client:

        raise HTTPException(
            status_code=500,
            detail=(
                "OPENAI_API_KEY မတွေ့ပါ။ "
                "Render → Environment → OPENAI_API_KEY "
                "ထည့်ပါ။"
            )
        )

    video_path = find_video(
        video_id
    )

    try:

        print(
            f"[{video_id}] STEP 1: Extracting audio..."
        )

        audio_path = make_audio(
            video_id
        )

        print(
            f"[{video_id}] STEP 2: Transcribing..."
        )

        transcript = transcribe_audio(
            audio_path
        )

        print(
            f"[{video_id}] STEP 3: Extracting video frames..."
        )

        frame_paths = extract_frames(
            video_path,
            video_id,
            count=8
        )

        print(
            f"[{video_id}] STEP 4: AI writing Burmese script..."
        )

        script = create_burmese_script(
            transcript,
            frame_paths
        )

        script_path = save_script(
            video_id,
            script
        )

        print(
            f"[{video_id}] STEP 5: Generating Burmese AI voice..."
        )

        narration_path = create_burmese_voice(
            video_id,
            script
        )

        print(
            f"[{video_id}] STEP 6: Rendering final video..."
        )

        recap_path = render_recap_video(
            video_id,
            video_path,
            narration_path
        )

        print(
            f"[{video_id}] DONE!"
        )

        return {
            "success": True,
            "video_id": video_id,
            "status": "completed",

            "script": script,

            "script_url": (
                f"/script/{video_id}"
            ),

            "audio_url": (
                f"/narration/{video_id}"
            ),

            "video_url": (
                f"/download/{video_id}"
            ),

            "download_url": (
                f"/download/{video_id}"
            ),

            "message": (
                "AI က video ဇာတ်လမ်းကို "
                "နားလည်ပြီး မြန်မာ script ရေးကာ "
                "မြန်မာ AI အသံနဲ့ Recap Video "
                "ပြုလုပ်ပြီးပါပြီ။"
            )
        }

    except HTTPException:
        raise

    except Exception as e:

        print(
            "CREATE RECAP ERROR:",
            repr(e)
        )

        raise HTTPException(
            status_code=500,
            detail=f"AI Recap failed: {str(e)}"
        )


# =========================================================
# SCRIPT
# =========================================================

@app.get("/script/{video_id}")
def get_script(
    video_id: str
):

    script_path = (
        OUTPUT_DIR /
        f"{video_id}_script.txt"
    )

    if not script_path.exists():

        raise HTTPException(
            status_code=404,
            detail="Script not found."
        )

    return {
        "video_id": video_id,
        "script": script_path.read_text(
            encoding="utf-8"
        )
    }


# =========================================================
# NARRATION
# =========================================================

@app.get("/narration/{video_id}")
def get_narration(
    video_id: str
):

    narration_path = (
        OUTPUT_DIR /
        f"{video_id}_narration.mp3"
    )

    if not narration_path.exists():

        raise HTTPException(
            status_code=404,
            detail="Narration not found."
        )

    return FileResponse(
        path=narration_path,
        media_type="audio/mpeg",
        filename="burmese-narration.mp3"
    )


# =========================================================
# DOWNLOAD FINAL VIDEO
# =========================================================

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
        filename="zero-recap-ai.mp4",
        content_disposition_type="attachment"
    )


# =========================================================
# VIEW ORIGINAL VIDEO
# =========================================================

@app.get("/video/{video_id}")
def get_video(
    video_id: str
):

    input_path = find_video(
        video_id
    )

    extension = input_path.suffix.lower()

    media_type = "video/mp4"

    if extension == ".webm":
        media_type = "video/webm"

    elif extension == ".mov":
        media_type = "video/quicktime"

    return FileResponse(
        path=input_path,
        media_type=media_type
    )
