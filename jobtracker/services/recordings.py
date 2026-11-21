"""Interview recordings: upload → estimate → transcribe (speaker labels) → you pick your voice → debrief.

1. upload(): the audio is kept in <company folder>/recordings/; its length is measured (ffprobe) and the cost
   of each step is estimated before anything is spent.
2. transcribe() (background): ffmpeg shrinks it to mono speech quality (under the API's 25 MB), then OpenAI
   transcribes it with speaker labels (JOBTRACKER_TRANSCRIBE_MODEL). If that model is gone (OpenAI shuts the
   diarize model down on 2027-02-26) or refuses, a text-only model is used and the debrief tells the speakers
   apart from the content.
3. choose_speaker(): with labels, the user confirms which voice is theirs (the app guesses: the interviewer is
   the one asking questions).
4. analyze() (background): the cheap model writes the debrief: summary, what they said about the role, next
   steps, each question with the user's answer graded /10 and what was missed, strengths, what to improve, and
   a thank-you email. Each graded answer goes to the practice log (source "interview"), and later preps read
   the debriefs.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from .. import config, prices, users
from ..logs import log
from ..models import Debrief, PracticeAttempt, Recording, RecordingBook, TranscriptSegment
from ..storage import UserDocument
from . import companies, files, llm, practice, push, usage

store = UserDocument("recordings.json", RecordingBook)
AUDIO_TYPES = {".m4a", ".mp3", ".wav", ".ogg", ".oga", ".opus", ".webm", ".mp4", ".mpeg", ".mpga", ".flac", ".aac",
               ".3gp", ".amr", ".mov", ".mkv"}
API_LIMIT_MB = 24          # OpenAI accepts files up to 25 MB
WORDS_PER_MINUTE = 140     # speech, for estimates
TOKENS_PER_WORD = 2.0      # generous: Hebrew and other non-Latin scripts take more tokens per word

ANALYSIS = """You are an expert interview coach. Below is the transcript of a real job interview of the candidate
for {position} at {company} (stage: {stage}). {speakers}

Write the debrief:
- summary: how the interview went, in a few lines.
- about_the_role: everything the interviewers said about the company, team, role, process, salary or timeline.
- next_steps: what happens next, as said in the interview ("" if not mentioned).
- questions: every question the candidate was asked, in order. For each: the question, a short summary of the
  candidate's answer, its kind (technical, behavioral, HR, case…), the broad capability it tests (reuse one of:
  {capabilities}; a short new name only if none fits), a precise topic, a fair score /10 for the answer as a
  demanding interviewer would grade it, what a strong answer would have added (`missed`), and one or two lines of
  feedback. Grade only what the candidate actually said.
- strengths and to_improve: the main points, concrete and actionable.
- thank_you_email: a short, warm, specific thank-you email from the candidate to the interviewer(s).
Write the text in {language}; keep the JSON field names, `kind` and `capability` in English. The transcript may
contain recognition errors: interpret them sensibly. Never invent what was not said."""


class RecordingUnavailable(RuntimeError):
    pass


# ------------------------------------------------------------------- storage

def list_for(company: str) -> list[Recording]:
    company_id = companies.resolve(company).id
    return sorted((r for r in store.load().recordings if r.company_id == company_id), key=lambda r: r.uploaded_at)


def get(recording_id: str) -> Recording:
    found = next((r for r in store.load().recordings if r.id == recording_id), None)
    if found is None:
        raise companies.NotFound(f"Recording {recording_id!r} not found")
    return found


def _save(rec: Recording) -> Recording:
    with store.transaction() as book:
        book.recordings = [r for r in book.recordings if r.id != rec.id] + [rec]
    return rec


def _update(recording_id: str, **fields) -> Recording:
    return _save(get(recording_id).model_copy(update=fields))


def delete(recording_id: str) -> None:
    """Delete a recording, its audio file and the practice answers graded from it."""
    rec = get(recording_id)
    audio = users.output_dir() / rec.file
    if audio.exists():
        audio.unlink()
    with store.transaction() as book:
        book.recordings = [r for r in book.recordings if r.id != recording_id]
    with practice.store.transaction() as book:
        book.attempts = [a for a in book.attempts if a.recording_id != recording_id]


# ------------------------------------------------------------------ estimates

def estimate(duration_s: float) -> dict[str, float | None]:
    """Cost before spending: transcription (per minute) and analysis (tokens from the expected transcript)."""
    minutes = duration_s / 60
    transcript_tokens = int(minutes * WORDS_PER_MINUTE * TOKENS_PER_WORD)
    analysis = usage.estimate_text(config.OPENAI_MODEL, transcript_tokens + 2_500, 3_000 + transcript_tokens // 6)
    transcription = prices.audio_cost("openai", config.TRANSCRIBE_MODEL, duration_s)
    return {"transcription": transcription, "analysis": round(analysis, 4) if analysis is not None else None}


# --------------------------------------------------------------------- upload

def _tool(name: str) -> str:
    path = shutil.which(getattr(config, f"{name.upper()}_BIN", name))
    if not path:
        raise RecordingUnavailable(f"{name} is not installed on the server (needed to read audio files).")
    return path


def duration(path: Path) -> float:
    """Length in seconds. Recordings made in a browser (WebM) often have no length in their header: then the
    audio is decoded once and the last timestamp is read."""
    done = subprocess.run([_tool("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of",
                           "default=noprint_wrappers=1:nokey=1", str(path)], capture_output=True, text=True, timeout=60)
    try:
        return float(done.stdout.strip())
    except ValueError:
        pass
    decoded = subprocess.run([_tool("ffmpeg"), "-v", "error", "-nostats", "-progress", "pipe:1", "-i", str(path),
                              "-vn", "-f", "null", "-"], capture_output=True, text=True, timeout=600)
    times = re.findall(r"^out_time_(?:us|ms)=(\d+)$", decoded.stdout, re.MULTILINE)  # both are microseconds
    if not times or not int(times[-1]):
        raise RecordingUnavailable("Could not read this audio file (unsupported or damaged).")
    return int(times[-1]) / 1_000_000


def upload(company: str, filename: str, content: bytes, step: str = "") -> Recording:
    c = companies.resolve(company)
    suffix = Path(filename).suffix.lower()
    if suffix not in AUDIO_TYPES:
        allowed = ", ".join(sorted(AUDIO_TYPES))
        raise ValueError(f"Unsupported file type {suffix or '(none)'}: use an audio file ({allowed}).")
    if len(content) > config.MAX_RECORDING_MB * 1024 * 1024:
        raise ValueError(f"File too large (max {config.MAX_RECORDING_MB} MB).")
    node = next((n for n in c.process.nodes if step and (n.id == step or n.name.lower() == step.lower())), None)
    folder = files.output_folder(c.name) / "recordings"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    target = folder / f"{stamp}_{files.safe_name(Path(filename).stem)}{suffix}"
    target.write_bytes(content)
    try:
        seconds = duration(target)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return _save(Recording(company_id=c.id, step=node.id if node else "", step_name=node.name if node else "",
                           file=files.relative(target), original_name=filename, duration_s=round(seconds, 1),
                           estimate_usd=estimate(seconds)))


# ---------------------------------------------------------------- transcribe

def transcribe(recording_id: str, language: str = "") -> Recording:
    """Start the transcription in the background (then the speaker choice, or the analysis)."""
    rec = get(recording_id)
    if rec.status in ("transcribing", "analyzing"):
        return rec
    if not llm.openai_available():
        raise RecordingUnavailable("Transcription needs OPENAI_API_KEY (Claude can't transcribe audio).")
    rec = _update(recording_id, status="transcribing", error="", language=language)
    _background(rec, _transcribe, "recording.transcribe")
    return rec


def _background(rec: Recording, job, feature: str) -> None:
    """Run `job(recording_id)` in a thread, as the current user; costs are billed to `feature`."""
    user = users.current()

    def run():
        with users.acting_as(user), usage.feature(feature):
            try:
                job(rec.id)
            except Exception as e:  # shown on the recording
                log.warning("Recording %s failed: %s", rec.id, e)
                _update(rec.id, status="error", error=str(e)[:2_000])
    threading.Thread(target=run, daemon=True, name=f"recording-{rec.id}").start()


def _compress(source: Path, tmp: Path, seconds: float) -> Path:
    """Mono 16 kHz MP3 at the highest bitrate that fits the API limit (speech stays clear down to 16 kbps)."""
    kbps = min(48, int(API_LIMIT_MB * 8 * 1024 / max(seconds, 1)))
    if kbps < 16:
        raise RecordingUnavailable(f"Recording too long ({seconds / 3600:.1f} h): split it into parts under 3 hours.")
    out = tmp / "speech.mp3"
    done = subprocess.run([_tool("ffmpeg"), "-y", "-v", "error", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000",
                           "-b:a", f"{kbps}k", str(out)], capture_output=True, text=True, timeout=1_800)
    if done.returncode != 0 or not out.exists():
        raise RecordingUnavailable(f"Could not convert the audio: {done.stderr.strip()[-500:]}")
    return out


def _transcribe(recording_id: str) -> None:
    import openai

    rec = get(recording_id)
    with tempfile.TemporaryDirectory(prefix="rec-") as tmp, usage.tally() as spent:
        audio = _compress(users.output_dir() / rec.file, Path(tmp), rec.duration_s)
        client = llm.client()
        segments, model, diarized = [], config.TRANSCRIBE_MODEL, True
        try:
            with audio.open("rb") as f:
                result = client.audio.transcriptions.create(model=model, file=f, response_format="diarized_json",
                                                            chunking_strategy="auto")
            segments = [TranscriptSegment(speaker=str(getattr(s, "speaker", "") or ""), start=getattr(s, "start", 0) or 0,
                                          end=getattr(s, "end", 0) or 0, text=(s.text or "").strip())
                        for s in (getattr(result, "segments", None) or []) if (s.text or "").strip()]
        except (openai.NotFoundError, openai.BadRequestError, openai.PermissionDeniedError) as e:
            log.warning("Transcription with %s failed (%s): falling back to %s", model, e, config.TRANSCRIBE_FALLBACK_MODEL)
            model, diarized = config.TRANSCRIBE_FALLBACK_MODEL, False
            with audio.open("rb") as f:
                result = client.audio.transcriptions.create(model=model, file=f)
            segments = [TranscriptSegment(text=(result.text or "").strip())]
        usage.record_audio(model, rec.duration_s)
        cost = {"transcription": usage.sum_cost(spent)}
    speakers = sorted({s.speaker for s in segments if s.speaker})
    diarized = diarized and len(speakers) >= 2
    if not any(s.text for s in segments):
        raise RecordingUnavailable("The transcription is empty: is there speech in this recording?")
    rec = _update(recording_id, segments=segments, model=model, diarized=diarized, me=guess_me(segments),
                  cost_usd={**rec.cost_usd, **cost}, status="choose_speaker" if diarized else "analyzing")
    if diarized:
        push.notify("Which voice is yours?", "The recording is transcribed: pick your voice to get the debrief.",
                    f"/#/company/{rec.company_id}", tag=f"recording:{rec.id}")
    if not diarized:
        _analyze(recording_id)


def guess_me(segments: list[TranscriptSegment]) -> str:
    """The candidate is usually the speaker who asks the fewest questions and talks the most."""
    stats: dict[str, list[int]] = {}
    for s in segments:
        if s.speaker:
            q, words = stats.setdefault(s.speaker, [0, 0])
            stats[s.speaker] = [q + s.text.count("?"), words + len(s.text.split())]
    if len(stats) < 2:
        return ""
    return min(stats, key=lambda k: (stats[k][0] / max(stats[k][1], 1), -stats[k][1]))


def choose_speaker(recording_id: str, me: str) -> Recording:
    """Confirm which speaker label is the user, then analyze (in the background)."""
    rec = get(recording_id)
    if me not in {s.speaker for s in rec.segments}:
        raise ValueError(f"No speaker {me!r} in this recording")
    rec = _update(recording_id, me=me, status="analyzing", error="")
    _background(rec, _analyze, "recording.analyze")
    return rec


def reanalyze(recording_id: str, language: str | None = None) -> Recording:
    rec = get(recording_id)
    if not rec.segments:
        raise ValueError("Transcribe the recording first")
    rec = _update(recording_id, status="analyzing", error="", **({"language": language} if language is not None else {}))
    _background(rec, _analyze, "recording.analyze")
    return rec


# -------------------------------------------------------------------- analyze

def transcript_text(rec: Recording) -> str:
    if not rec.diarized:
        return "\n".join(s.text for s in rec.segments)
    lines, last = [], None
    for s in rec.segments:
        who = "CANDIDATE" if s.speaker == rec.me else f"INTERVIEWER {s.speaker}"
        if who == last:
            lines[-1] += " " + s.text
        else:
            lines.append(f"[{who}] {s.text}")
        last = who
    return "\n".join(lines)


def _analyze(recording_id: str) -> None:
    rec = get(recording_id)
    c = companies.get_company(rec.company_id)
    speakers = ("Speakers are labelled: [CANDIDATE] is the candidate, [INTERVIEWER …] the interviewers."
                if rec.diarized else "The transcript has no speaker labels: tell the interviewer(s) and the candidate "
                "apart from the content (the interviewer asks, the candidate answers).")
    instructions = ANALYSIS.format(position=c.position or "the role", company=c.name, stage=rec.step_name or "unknown",
                                   speakers=speakers, capabilities=", ".join(practice.known_capabilities()),
                                   language=rec.language or "the language of the interview")
    with usage.tally() as spent:
        debrief = llm.structured(instructions, transcript_text(rec), Debrief)
    path = files.output_folder(c.name) / f"Debrief_{files.safe_name(rec.step_name or 'interview')}_{rec.uploaded_at[:10]}.md"
    files.archive_previous(path)
    path.write_text(markdown(c.name, rec, debrief), encoding="utf-8")
    # graded answers -> practice log (replacing those of a previous analysis of this recording)
    with practice.store.transaction() as book:
        book.attempts = [a for a in book.attempts if a.recording_id != recording_id]
    known = practice.known_capabilities()
    for q in debrief.questions:
        attempt = PracticeAttempt(
            company_id=c.id, company=c.name, exercise=q.question[:160], kind=q.kind, topic=q.topic,
            capability=practice.assign(q.capability, q.topic, q.kind, q.question, known),
            score=max(0, min(q.score, 10)), missed=q.missed, feedback=q.feedback, source="interview",
            recording_id=recording_id, at=rec.uploaded_at)
        with practice.store.transaction() as book:
            book.attempts.append(attempt)
    average = sum(q.score for q in debrief.questions) / len(debrief.questions) if debrief.questions else None
    push.notify(f"Debrief ready: {c.name}", f"{len(debrief.questions)} questions"
                + (f", average {average:.1f}/10" if average is not None else "") + ". Thank-you email included.",
                f"/#/company/{c.id}", tag=f"recording:{recording_id}")
    _update(recording_id, debrief=debrief, debrief_file=files.relative(path), status="done",
            cost_usd={**get(recording_id).cost_usd, "analysis": usage.sum_cost(spent)})


def markdown(company: str, rec: Recording, d: Debrief) -> str:
    out = [f"# Debrief – {company}" + (f" · {rec.step_name}" if rec.step_name else ""), f"*{rec.uploaded_at[:10]}*",
           "", "## Summary", d.summary, "", "## About the role", d.about_the_role, "", "## Next steps",
           d.next_steps or "Not mentioned.", "", "## Questions"]
    for i, q in enumerate(d.questions, 1):
        out += ["", f"### {i}. {q.question}", f"*{q.kind} · {q.capability} · {q.topic}* — **{q.score}/10**", "",
                f"**Your answer:** {q.answer_summary}", "", f"**Feedback:** {q.feedback}"]
        if q.missed:
            out += ["", "**Missed:**", *[f"- {m}" for m in q.missed]]
    out += ["", "## Strengths", *[f"- {s}" for s in d.strengths], "", "## To improve",
            *[f"- {s}" for s in d.to_improve], "", "## Thank-you email", "", d.thank_you_email, ""]
    return "\n".join(out)


def summary_for_prep(company_id: str) -> str:
    """What past interviews at this company showed, for the next prep."""
    done = [r for r in store.load().recordings if r.company_id == company_id and r.debrief]
    parts = []
    for r in sorted(done, key=lambda r: r.uploaded_at):
        d = r.debrief
        parts += [f"Interview {r.uploaded_at[:10]} ({r.step_name or 'stage unknown'}): {d.summary}",
                  "Questions asked: " + "; ".join(f"{q.question} ({q.score}/10)" for q in d.questions),
                  "To improve: " + "; ".join(d.to_improve), f"Next steps: {d.next_steps or 'unknown'}",
                  f"About the role: {d.about_the_role}", ""]
    return "\n".join(parts).strip()


def view(rec: Recording) -> dict:
    data = rec.model_dump(mode="json")
    speakers: dict[str, dict] = {}
    for s in rec.segments:
        if s.speaker:
            info = speakers.setdefault(s.speaker, {"speaker": s.speaker, "words": 0, "samples": []})
            info["words"] += len(s.text.split())
            if len(info["samples"]) < 3 and len(s.text) > 25:
                info["samples"].append(s.text[:200])
    data["speakers"] = list(speakers.values())
    data["segments_count"] = len(rec.segments)
    data.pop("segments")  # big; recording.transcript returns it
    return data


def transcript(recording_id: str) -> dict:
    rec = get(recording_id)
    return {"id": rec.id, "diarized": rec.diarized, "me": rec.me, "text": transcript_text(rec) if rec.segments else ""}

