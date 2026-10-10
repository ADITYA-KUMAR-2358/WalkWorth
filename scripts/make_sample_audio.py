"""Voices the sample notes with Windows' offline speech synthesizer so the
pipeline has real audio to transcribe. SYNTHETIC: a computer voice reading
invented lines, labelled as such everywhere. Output is committed, so this only
needs to run again if the sample lines change (Windows only).

    python scripts/make_sample_audio.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from make_sample_fixture import NOTES  # noqa: E402

OUT = ROOT / "fixtures" / "sinhagad-sample" / "audio"

PS = r"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.Rate = -1
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$s.SetOutputToWaveFile($args[1], $fmt)
$s.Speak($args[0])
$s.Dispose()
"""


def to_ogg(wav: Path, ogg: Path) -> float:
    import av

    with av.open(str(wav)) as src, av.open(str(ogg), "w", format="ogg") as dst:
        out = dst.add_stream("libopus", rate=48000, layout="mono")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=48000)
        samples = 0
        for frame in src.decode(audio=0):
            samples += frame.samples
            for rf in resampler.resample(frame):
                for packet in out.encode(rf):
                    dst.mux(packet)
        for packet in out.encode(None):
            dst.mux(packet)
        return samples / 16000


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "say.ps1"
        script.write_text(PS, encoding="utf-8")
        for i, (_, _, text) in enumerate(NOTES, 1):
            wav = Path(tmp) / f"{i}.wav"
            subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), text, str(wav)],
                           check=True)
            dur = to_ogg(wav, OUT / f"note-{i:02d}.ogg")
            print(f"note-{i:02d}.ogg  {dur:.1f} s")


if __name__ == "__main__":
    main()
