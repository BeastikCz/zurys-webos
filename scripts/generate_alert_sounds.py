"""Generate original farm-themed WAV stingers for the ZURYS stream overlay."""

from __future__ import annotations

import math
import random
import struct
import wave
from pathlib import Path


RATE = 44_100
OUT = Path(__file__).resolve().parents[1] / "web" / "audio" / "alerts"
RNG = random.Random(7821)


def track(seconds: float) -> list[float]:
    return [0.0] * int(RATE * seconds)


def env(pos: float, duration: float, attack: float = 0.02, release: float = 0.25) -> float:
    if pos < 0 or pos >= duration:
        return 0.0
    a = min(1.0, pos / max(attack, 1e-5))
    r = min(1.0, (duration - pos) / max(release, 1e-5))
    return a * r


def add_tone(
    dst: list[float], start: float, duration: float, freq: float, gain: float,
    kind: str = "sine", end_freq: float | None = None, tremolo: float = 0.0,
) -> None:
    first = int(start * RATE)
    count = int(duration * RATE)
    phase = 0.0
    for i in range(count):
        idx = first + i
        if idx >= len(dst):
            break
        p = i / RATE
        ratio = p / duration
        f = freq if end_freq is None else freq * ((end_freq / freq) ** ratio)
        phase += 2 * math.pi * f / RATE
        if kind == "saw":
            value = 2 * ((phase / (2 * math.pi)) % 1) - 1
        elif kind == "square":
            value = 1.0 if math.sin(phase) >= 0 else -1.0
        elif kind == "triangle":
            value = 2 * abs(2 * ((phase / (2 * math.pi)) % 1) - 1) - 1
        else:
            value = math.sin(phase)
        mod = 1.0 if not tremolo else 0.72 + 0.28 * math.sin(2 * math.pi * tremolo * p)
        dst[idx] += value * gain * env(p, duration) * mod


def add_bell(dst: list[float], start: float, freq: float, gain: float = 0.28) -> None:
    for harmonic, level, length in ((1, 1.0, 0.95), (2.02, 0.52, 0.72), (3.01, 0.25, 0.46), (4.16, 0.13, 0.35)):
        add_tone(dst, start, length, freq * harmonic, gain * level, "sine")


def add_noise(dst: list[float], start: float, duration: float, gain: float, decay: float = 2.0) -> None:
    first = int(start * RATE)
    count = int(duration * RATE)
    smooth = 0.0
    for i in range(count):
        idx = first + i
        if idx >= len(dst):
            break
        p = i / count
        smooth = smooth * 0.92 + RNG.uniform(-1, 1) * 0.08
        dst[idx] += smooth * gain * ((1 - p) ** decay) * env(i / RATE, duration, 0.01, 0.18)


def add_coin(dst: list[float], start: float = 0.0) -> None:
    add_tone(dst, start, 0.18, 1046, 0.20, "sine", 1568)
    add_tone(dst, start + 0.13, 0.34, 1568, 0.22, "sine", 2093)
    add_bell(dst, start + 0.14, 784, 0.08)


def normalize(samples: list[float], peak: float = 0.88) -> list[float]:
    high = max(abs(x) for x in samples) or 1.0
    scale = peak / high
    return [max(-1.0, min(1.0, x * scale)) for x in samples]


def write(name: str, samples: list[float]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    data = normalize(samples)
    with wave.open(str(OUT / name), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(b"".join(struct.pack("<h", int(x * 32767)) for x in data))


def coins() -> None:
    dst = track(1.15)
    add_coin(dst, 0.04)
    add_coin(dst, 0.52)
    write("coins.wav", dst)


def rooster_bell() -> None:
    dst = track(1.75)
    for i, freq in enumerate((880, 1175, 1046, 1397)):
        add_tone(dst, 0.03 + i * 0.11, 0.19, freq, 0.11, "saw", freq * 1.15)
    add_tone(dst, 0.49, 0.28, 659, 0.10, "triangle", 988)
    add_bell(dst, 0.72, 392, 0.22)
    write("rooster-bell.wav", dst)


def accordion() -> None:
    dst = track(1.75)
    melody = (523, 659, 784, 659, 698, 880, 784, 659)
    for i, freq in enumerate(melody):
        at = 0.03 + i * 0.16
        add_tone(dst, at, 0.25, freq, 0.11, "saw", tremolo=7)
        add_tone(dst, at, 0.25, freq / 2, 0.07, "triangle", tremolo=7)
    add_bell(dst, 1.32, 523, 0.13)
    write("accordion.wav", dst)


def barn_bell() -> None:
    dst = track(1.45)
    add_bell(dst, 0.03, 392, 0.32)
    add_bell(dst, 0.45, 523, 0.24)
    write("barn-bell.wav", dst)


def tractor() -> None:
    dst = track(1.85)
    for i in range(14):
        add_tone(dst, i * 0.075, 0.10, 67 + (i % 2) * 9, 0.10, "saw", 54 + (i % 2) * 5)
    add_noise(dst, 0.0, 1.1, 0.22, 1.2)
    add_bell(dst, 0.88, 392, 0.22)
    add_coin(dst, 1.15)
    write("tractor.wav", dst)


def polka() -> None:
    dst = track(2.05)
    melody = (659, 784, 880, 784, 659, 523, 659, 784, 988, 784)
    for i, freq in enumerate(melody):
        at = 0.02 + i * 0.15
        add_tone(dst, at, 0.20, freq, 0.10, "triangle")
        bass = 131 if i % 4 == 0 else 196
        add_tone(dst, at, 0.11, bass, 0.09, "triangle")
        if i % 2:
            add_tone(dst, at, 0.10, bass * 2, 0.05, "square")
    add_bell(dst, 1.55, 523, 0.18)
    write("polka.wav", dst)


def legend() -> None:
    dst = track(2.95)
    for freq in (261, 329, 392):
        add_tone(dst, 0.02, 0.90, freq, 0.10, "saw")
    for freq in (329, 415, 494):
        add_tone(dst, 0.58, 0.92, freq, 0.11, "saw")
    for freq in (392, 494, 587, 784):
        add_tone(dst, 1.18, 1.15, freq, 0.095, "triangle")
    add_coin(dst, 1.28)
    add_coin(dst, 1.72)
    add_noise(dst, 1.32, 1.45, 0.50, 0.8)
    add_tone(dst, 1.32, 1.42, 55, 0.25, "sine", 32)
    write("legend-harvest.wav", dst)


def nice() -> None:
    dst = track(1.35)
    add_tone(dst, 0.02, 0.30, 392, 0.15, "square", 330)
    add_tone(dst, 0.28, 0.38, 330, 0.16, "square", 210)
    add_noise(dst, 0.67, 0.11, 0.34, 0.2)
    add_tone(dst, 0.78, 0.22, 880, 0.13, "sine", 1175)
    add_tone(dst, 0.96, 0.25, 1175, 0.10, "sine", 1568)
    write("nice.wav", dst)


if __name__ == "__main__":
    coins()
    rooster_bell()
    accordion()
    barn_bell()
    tractor()
    polka()
    legend()
    nice()
    print(f"Generated 8 alert sounds in {OUT}")
