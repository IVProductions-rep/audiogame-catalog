"""
Suoni del catalogo, sintetizzati da zero (nessun campione esterno, nessun diritto da gestire).

    python tools/make_sounds.py [cartella_uscita]

Scrive <uscita>/ui/*.ogg|mp3 e <uscita>/amb/*.ogg|mp3. Per sostituire un suono gia
pubblicato si cambia NOME (es. move_v2) e lo si scrive nel catalog.json: i client tengono
i file in cache per nome e non li riscaricano mai.
"""
import os
import subprocess
import sys

import numpy as np
from scipy.io import wavfile
from scipy.signal import butter, sosfilt, fftconvolve

SR = 44100
RNG = np.random.default_rng(7)
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..")


def t(sec):
    return np.arange(int(SR * sec)) / SR


def env(n, a, d, curve=4.0):
    """Attacco lineare a secondi, poi decadimento esponenziale fino a n campioni."""
    e = np.ones(n)
    na = max(1, int(a * SR))
    e[:na] = np.linspace(0, 1, na)
    rest = n - na
    if rest > 0:
        e[na:] = np.exp(-curve * np.linspace(0, 1, rest) * (rest / SR) / max(d, 1e-3))
    return e


def bell(f, sec, decay, partials=((1, 1), (2.01, 0.35), (3.02, 0.12), (4.2, 0.05))):
    x = t(sec)
    s = sum(a * np.sin(2 * np.pi * f * m * x) * np.exp(-x * (1 + m) / decay) for m, a in partials)
    return s * env(len(x), 0.004, sec)


def filt(x, kind, f):
    sos = butter(4, f, btype=kind, fs=SR, output="sos")
    return sosfilt(sos, x)


def noise(sec):
    return RNG.standard_normal(int(SR * sec))


def brown(n):
    b = np.cumsum(RNG.standard_normal(n))
    b -= np.convolve(b, np.ones(4410) / 4410, mode="same")  # niente deriva
    return b / (np.abs(b).max() + 1e-9)


def reverb(x, sec=1.2, wet=0.25):
    ir = noise(sec) * np.exp(-np.linspace(0, 6, int(SR * sec)))
    ir = filt(ir, "lowpass", 5000)
    ir /= np.sqrt((ir ** 2).sum())
    y = np.concatenate([x, np.zeros(len(ir))])
    w = fftconvolve(x, ir)
    w = np.pad(w, (0, max(0, len(y) - len(w))))[: len(y)]
    return (1 - wet) * y + wet * w * 0.6


def place(dst, src, at):
    i = int(at * SR)
    end = min(len(dst), i + len(src))
    dst[i:end] += src[: end - i]


def norm(x, peak_db=-1.0):
    p = np.abs(x).max()
    return x if p == 0 else x / p * 10 ** (peak_db / 20)


def save(rel, x, peak_db=-1.0):
    x = norm(np.asarray(x, dtype=np.float64), peak_db)
    if x.ndim == 1:
        x = np.stack([x, x], axis=1)
    path = os.path.join(OUT, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    wav = path + ".wav"
    wavfile.write(wav, SR, (x * 32767).astype(np.int16))
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", wav, "-c:a", "libvorbis", "-q:a", "5", path + ".ogg"], check=True)
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", wav, "-c:a", "libmp3lame", "-b:a", "160k", path + ".mp3"], check=True)
    os.remove(wav)
    print(rel, f"{len(x) / SR:.2f}s")


def fade(x, a=0.005, r=0.02):
    n = len(x)
    na, nr = int(a * SR), int(r * SR)
    x = x.copy()
    if na: x[:na] *= np.linspace(0, 1, na)
    if nr: x[-nr:] *= np.linspace(1, 0, nr)
    return x


# ---------------- UI ----------------

def ui_move():
    x = t(0.07)
    body = np.sin(2 * np.pi * 1250 * x) * 0.6 + np.sin(2 * np.pi * 2600 * x) * 0.25
    click = filt(noise(0.07), "bandpass", [1800, 6000]) * 0.25
    return fade((body + click) * np.exp(-x / 0.012), 0.001, 0.01)


def ui_edge():
    x = t(0.18)
    f = 110 * np.exp(-x * 6)
    s = np.sin(2 * np.pi * np.cumsum(f) / SR)
    s += filt(noise(0.18), "lowpass", 400) * 0.5
    return fade(s * np.exp(-x / 0.05), 0.002, 0.03)


def ui_confirm():
    out = np.zeros(int(SR * 0.45))
    place(out, bell(659.25, 0.3, 0.35), 0)
    place(out, bell(987.77, 0.4, 0.45), 0.08)
    return fade(reverb(out, 0.6, 0.18), 0.001, 0.05)


def ui_back():
    out = np.zeros(int(SR * 0.4))
    place(out, bell(880, 0.25, 0.3) * 0.8, 0)
    place(out, bell(587.33, 0.35, 0.35) * 0.8, 0.07)
    return fade(reverb(out, 0.5, 0.15), 0.001, 0.05)


def whoosh(sec, up=True):
    n = int(SR * sec)
    w = noise(sec)
    out = np.zeros(n)
    steps = 40
    for k in range(steps):
        a, b = k * n // steps, (k + 1) * n // steps
        p = k / steps if up else 1 - k / steps
        fc = 400 + 3000 * p
        out[a:b] = filt(w, "bandpass", [fc * 0.7, fc * 1.3])[a:b]
    return out * np.sin(np.pi * np.linspace(0, 1, n)) ** 2


def ui_open():
    sec = 1.0
    out = whoosh(sec, True) * 0.5
    for f, d in ((261.63, 0.0), (329.63, 0.06), (392.0, 0.12), (587.33, 0.18)):
        place(out, bell(f, sec - d, 0.8) * 0.35, d + 0.2)
    return fade(reverb(out, 1.0, 0.25), 0.01, 0.2)


def ui_close():
    sec = 0.9
    out = whoosh(sec, False) * 0.5
    for f, d in ((587.33, 0.0), (392.0, 0.06), (329.63, 0.12), (261.63, 0.18)):
        place(out, bell(f, sec - d, 0.7) * 0.3, d + 0.1)
    return fade(reverb(out, 1.0, 0.25), 0.01, 0.2)


def ui_new():
    out = np.zeros(int(SR * 0.5))
    for i, f in enumerate((1567.98, 2093.0, 2637.0, 3135.96)):
        place(out, bell(f, 0.25, 0.15) * (0.6 - i * 0.08), i * 0.045)
    return fade(reverb(out, 0.5, 0.25), 0.001, 0.06)


def ui_buy():
    out = np.zeros(int(SR * 0.7))
    place(out, bell(1975.53, 0.5, 0.5), 0)
    place(out, bell(2637.02, 0.6, 0.6), 0.07)
    place(out, filt(noise(0.3), "highpass", 6000) * np.exp(-t(0.3) / 0.05) * 0.15, 0.07)
    return fade(reverb(out, 0.7, 0.2), 0.001, 0.08)


def ui_error():
    x = t(0.35)
    s = np.sin(2 * np.pi * 196 * x) + 0.6 * np.sin(2 * np.pi * 207.65 * x)
    s = filt(s, "lowpass", 1200)
    return fade(s * np.exp(-x / 0.12), 0.003, 0.05)


# ---------------- ambiente notturno ----------------

def amb_bed(sec=64.0):
    """Vento leggero con raffiche lente, diverso tra i due canali; loop senza giunta."""
    n = int(SR * sec)
    xf = int(SR * 4)  # dissolvenza incrociata per chiudere il loop
    total = n + xf
    chans = []
    for ch in range(2):
        b = brown(total)
        p = filt(RNG.standard_normal(total), "bandpass", [250, 1600])
        # raffiche: somma di sinusoidi lente a fase casuale
        tt = np.arange(total) / SR
        g = sum(np.sin(2 * np.pi * f * tt + RNG.uniform(0, 6.28)) * a
                for f, a in ((0.031, 1.0), (0.067, 0.6), (0.13, 0.35), (0.21, 0.2)))
        g = (g - g.min()) / (g.max() - g.min())
        # filtro che si apre con la raffica: due bande mescolate
        lo = filt(p, "lowpass", 500)
        hi = filt(p, "bandpass", [700, 2200])
        w = lo * (0.6 + 0.4 * g) + hi * (g ** 2) * 0.6 + filt(b, "lowpass", 120) * 0.8
        chans.append(w * (0.35 + 0.65 * g))
    x = np.stack(chans, axis=1)
    head, tail = x[:xf], x[n:]
    ramp = np.linspace(0, 1, xf)[:, None]
    x = x[:n].copy()
    x[:xf] = head * ramp + tail * (1 - ramp)
    return x


def amb_leaves(sec, density):
    n = int(SR * sec)
    out = np.zeros(n)
    k = 0
    while k < density * sec:
        at = RNG.uniform(0, sec - 0.2)
        d = RNG.uniform(0.02, 0.12)
        g = filt(noise(d), "bandpass", [RNG.uniform(1500, 3000), RNG.uniform(5000, 9000)]) * env(int(SR * d), 0.003, d)
        place(out, g * RNG.uniform(0.2, 1.0), at)
        k += 1
    out *= np.sin(np.pi * np.linspace(0, 1, n)) ** 1.5
    st = np.stack([out, np.roll(out, 300) * 0.8], axis=1)
    return reverb_st(st, 0.8, 0.2)


def reverb_st(st, sec, wet):
    return np.stack([reverb(st[:, 0], sec, wet), reverb(st[:, 1], sec, wet)], axis=1)


def amb_owl(pattern):
    out = np.zeros(int(SR * 3.5))
    at = 0.1
    for dur, f in pattern:
        x = t(dur)
        vib = 1 + 0.01 * np.sin(2 * np.pi * 5.5 * x)
        fr = f * vib * (1 - 0.06 * x / dur)
        ph = 2 * np.pi * np.cumsum(fr) / SR
        s = np.sin(ph) + 0.25 * np.sin(2 * ph) + 0.08 * np.sin(3 * ph)
        s += filt(noise(dur), "bandpass", [f * 0.8, f * 1.6]) * 0.08
        e = np.sin(np.pi * np.linspace(0, 1, len(x))) ** 0.8
        place(out, s * e, at)
        at += dur + 0.12
    out = filt(out, "lowpass", 1400)
    return fade(reverb(out, 1.8, 0.4), 0.01, 0.3)


def amb_bird(notes, base):
    out = np.zeros(int(SR * 2.5))
    at = 0.05
    for i in range(notes):
        d = RNG.uniform(0.06, 0.14)
        x = t(d)
        f0 = base * RNG.uniform(0.9, 1.15)
        sweep = f0 * (1 + 0.35 * np.sin(np.pi * x / d)) * (1 + 0.04 * np.sin(2 * np.pi * 38 * x))
        s = np.sin(2 * np.pi * np.cumsum(sweep) / SR) * np.sin(np.pi * np.linspace(0, 1, len(x))) ** 2
        place(out, s * RNG.uniform(0.6, 1.0), at)
        at += d + RNG.uniform(0.04, 0.16)
    out = filt(out, "highpass", 1500)
    return fade(reverb(out, 1.4, 0.35), 0.005, 0.3)


def amb_cricket(sec=3.0):
    out = np.zeros(int(SR * sec))
    f = RNG.uniform(4200, 4800)
    at = 0.1
    while at < sec - 0.3:
        for p in range(3):
            x = t(0.012)
            place(out, np.sin(2 * np.pi * f * x) * np.sin(np.pi * np.linspace(0, 1, len(x))), at + p * 0.02)
        at += RNG.uniform(0.28, 0.4)
    out *= np.sin(np.pi * np.linspace(0, 1, len(out))) ** 0.5
    return fade(reverb(out, 1.0, 0.3), 0.01, 0.2)


def amb_branch():
    out = np.zeros(int(SR * 1.2))
    for i in range(RNG.integers(3, 6)):
        d = RNG.uniform(0.005, 0.02)
        place(out, filt(noise(d), "bandpass", [600, 3500]) * env(int(SR * d), 0.001, d / 2), 0.05 + i * RNG.uniform(0.03, 0.12))
    return fade(reverb(out, 1.0, 0.3), 0.001, 0.2)


if __name__ == "__main__":
    save("ui/move", ui_move(), -3)
    save("ui/edge", ui_edge(), -3)
    save("ui/confirm", ui_confirm(), -3)
    save("ui/back", ui_back(), -4)
    save("ui/open", ui_open(), -3)
    save("ui/close", ui_close(), -4)
    save("ui/new", ui_new(), -5)
    save("ui/buy", ui_buy(), -3)
    save("ui/error", ui_error(), -4)

    save("amb/night_bed", amb_bed(), -3)
    save("amb/leaves1", amb_leaves(2.5, 26), -3)
    save("amb/leaves2", amb_leaves(3.5, 18), -3)
    save("amb/owl1", amb_owl([(0.35, 380), (0.6, 360)]), -3)
    save("amb/owl2", amb_owl([(0.25, 400), (0.25, 395), (0.7, 370)]), -3)
    save("amb/bird1", amb_bird(4, 3300), -3)
    save("amb/bird2", amb_bird(2, 2600), -3)
    save("amb/cricket1", amb_cricket(), -6)
    save("amb/branch1", amb_branch(), -3)
