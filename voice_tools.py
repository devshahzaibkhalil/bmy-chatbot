"""
voice_tools.py
Transcribes uploaded audio to text using the SpeechRecognition library.

No API key is required. By default this uses SpeechRecognition's free
Google Web Speech endpoint (recognize_google), which needs internet access
but no account/key/billing - it's the same free tier the library ships
with by default. For a fully offline setup, install `pocketsphinx` and set
BMY_VOICE_ENGINE=sphinx as an environment variable.

Only WAV/AIFF/FLAC are supported directly by SpeechRecognition. Browsers
typically record webm/ogg via MediaRecorder - convert to WAV client-side
or with ffmpeg/pydub before calling transcribe_audio() if you hit format
errors (see README).
"""

import os

import speech_recognition as sr

_ENGINE = os.environ.get("BMY_VOICE_ENGINE", "google")


def transcribe_audio(audio_path):
    """
    Returns { text, error }. text is '' and error is set if transcription
    fails (unsupported format, no speech detected, network unavailable, etc).
    Never raises - the chat flow should degrade gracefully.
    """
    recognizer = sr.Recognizer()
    try:
        with sr.AudioFile(audio_path) as source:
            audio = recognizer.record(source)
    except Exception as e:
        return {"text": "", "error": f"Could not read audio file (unsupported format?): {e}"}

    try:
        if _ENGINE == "sphinx":
            text = recognizer.recognize_sphinx(audio)
        else:
            text = recognizer.recognize_google(audio)
        return {"text": text, "error": None}
    except sr.UnknownValueError:
        return {"text": "", "error": "Could not understand the audio - please try speaking clearly."}
    except sr.RequestError as e:
        return {"text": "", "error": f"Speech recognition service unavailable: {e}"}
    except Exception as e:
        return {"text": "", "error": f"Transcription failed: {e}"}
