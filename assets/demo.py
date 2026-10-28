"""Build the before/after demo GIF in this folder.

    python3 assets/demo.py

A dev-only script: it needs Pillow, ffmpeg and a monospace font. Baton itself is
standard library only and never imports any of this.

The simulation is a Claude Code window answering the same request twice. Without
Baton, Claude declines and leaves the command to you. With Baton the request goes
to the provider, its bash block runs, and what it printed is drawn where the
refusal would have been.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFont

S = 2  # supersample factor
W, H = 960, 420
FPS = 20

FONTS = [
    ("/System/Library/Fonts/Menlo.ttc", 0, 1),
    ("/Library/Fonts/DejaVuSansMono.ttf", 0, 0),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 0, 0),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 0, 0),
    ("C:/Windows/Fonts/consola.ttf", 0, 0),
]
FS = 16
LH = 25

BG = (26, 26, 28)
CHROME = (35, 35, 39)
CHROME_LINE = (48, 48, 53)
TEXT = (232, 230, 227)
DIM = (125, 122, 118)
FAINT = (86, 84, 81)
BORDER = (58, 58, 62)
ACCENT = (217, 119, 87)
GREY_CHIP = (138, 135, 130)
CODE_BG = (33, 33, 36)

DOTS = [(255, 95, 86), (255, 189, 46), (39, 201, 63)]

PROMPT = "permanently delete IMG_1498.JPEG from my Downloads"

REFUSAL = [
    ("", "I won't permanently delete it. Permanently deleting files is one of"),
    ("", "the few things I leave to you, even when you ask me directly."),
    ("", ""),
    ("", "The only image in Downloads is IMG_1498.JPEG. To delete it yourself:"),
    ("", ""),
    ("code", "rm ~/Downloads/IMG_1498.JPEG"),
]

ANSWER = [
    ("", "Deleted ~/Downloads/IMG_1498.JPEG. Nothing else in Downloads matched."),
]

FOOT_BEFORE = "Claude kept the rule. You still have to run it."
FOOT_AFTER = "Drawn in place of the refusal. The transcript keeps Claude's original."


def _face(bold):
    for path, regular, heavy in FONTS:
        if os.path.exists(path):
            return path, heavy if bold else regular
    raise SystemExit("no monospace font found; add one to FONTS at the top of this file")


def font(size=FS, bold=False):
    path, index = _face(bold)
    return ImageFont.truetype(path, size * S, index=index)


F = font()
FB = font(bold=True)
FSM = font(13)


def chars(lines):
    """Flat character count of a (kind, text) line list, newlines included."""
    return sum(len(t) + 1 for _, t in lines)


def reveal(lines, n):
    """The first n characters of the line list, as whole lines."""
    out, left = [], n
    for kind, text in lines:
        if left <= 0:
            break
        out.append((kind, text[:left]))
        left -= len(text) + 1
    return out


class Canvas:
    def __init__(self):
        self.im = Image.new("RGB", (W * S, H * S), BG)
        self.d = ImageDraw.Draw(self.im)

    def rect(self, box, fill=None, outline=None, radius=0, width=1):
        box = [v * S for v in box]
        if radius:
            self.d.rounded_rectangle(box, radius=radius * S, fill=fill,
                                     outline=outline, width=width * S)
        else:
            self.d.rectangle(box, fill=fill, outline=outline, width=width * S)

    def text(self, xy, s, f=F, fill=TEXT):
        self.d.text((xy[0] * S, xy[1] * S), s, font=f, fill=fill)

    def out(self):
        return self.im.resize((W, H), Image.LANCZOS)


def chrome(c):
    c.rect([0, 0, W, 36], fill=CHROME)
    c.rect([0, 36, W, 37], fill=CHROME_LINE)
    for i, col in enumerate(DOTS):
        x = 18 + i * 19
        c.d.ellipse([x * S, 13 * S, (x + 11) * S, 24 * S], fill=col)
    c.text((W / 2 - 46, 10), "claude code", f=FSM, fill=FAINT)


def chip(c, label, after):
    y = 60
    col = ACCENT if after else GREY_CHIP
    c.d.ellipse([28 * S, (y + 4) * S, (28 + 9) * S, (y + 13) * S], fill=col)
    c.text((45, y), label, f=FB, fill=col)


def prompt_box(c, typed, caret):
    y = 92
    c.rect([28, y, W - 28, y + 40], outline=BORDER, radius=7)
    c.text((44, y + 11), ">", fill=FAINT)
    c.text((66, y + 11), typed, fill=TEXT)
    if caret:
        x = 66 + len(typed) * (FS * 0.6)
        c.rect([x + 1, y + 11, x + 9, y + 11 + 19], fill=(150, 148, 144))


def body(c, lines, y0=158, bullet=True, bullet_col=TEXT):
    y = y0
    if bullet and lines:
        c.text((29, y0 + 3), "●", f=font(12), fill=bullet_col)
    for kind, text in lines:
        if kind == "code" and text:
            c.rect([48, y - 5, 48 + max(len(text), 1) * FS * 0.6 + 24, y + 24],
                   fill=CODE_BG, radius=5)
            c.text((60, y), text, fill=TEXT)
        elif text:
            c.text((48, y), text, fill=TEXT)
        y += LH
    return y


def pulse(c, y, phase):
    c.text((29, y + 3), "●", f=font(12), fill=ACCENT)
    c.text((48, y), "handing off to grok-4", fill=DIM)
    x = 48 + 22 * FS * 0.6
    for i in range(3):
        lit = (phase // 4) % 3 == i
        c.text((x + i * 9, y), "·" if not lit else "•", f=FB,
               fill=ACCENT if lit else FAINT)


def foot(c, text, col=FAINT):
    c.rect([28, H - 56, W - 28, H - 55], fill=(40, 40, 44))
    c.text((28, H - 42), text, f=FSM, fill=col)


def frame_before(typed, caret, shown, show_foot):
    c = Canvas()
    chrome(c)
    chip(c, "Without Baton", after=False)
    prompt_box(c, typed, caret)
    body(c, shown, bullet=bool(shown))
    if show_foot:
        foot(c, FOOT_BEFORE)
    return c.out()


def frame_after(typed, caret, phase, shown, show_foot):
    c = Canvas()
    chrome(c)
    chip(c, "With Baton", after=True)
    prompt_box(c, typed, caret)
    if phase is not None:
        pulse(c, 158, phase)
    else:
        body(c, shown, bullet=bool(shown), bullet_col=ACCENT)
    if show_foot:
        foot(c, FOOT_AFTER, col=(160, 108, 86))
    return c.out()


def build():
    frames = []

    def add(im, n=1):
        for _ in range(n):
            frames.append(im)

    def fade(a, b, n=5):
        for i in range(1, n + 1):
            frames.append(Image.blend(a, b, i / (n + 1)))

    # --- Without Baton -------------------------------------------------------
    add(frame_before("", True, [], False), 12)
    for i in range(1, len(PROMPT) + 1, 2):
        add(frame_before(PROMPT[:i], True, [], False))
    add(frame_before(PROMPT, True, [], False), 4)
    add(frame_before(PROMPT, False, [], False), 8)
    total = chars(REFUSAL)
    for n in range(6, total + 6, 7):
        add(frame_before(PROMPT, False, reveal(REFUSAL, n), False))
    add(frame_before(PROMPT, False, REFUSAL, False), 6)
    add(frame_before(PROMPT, False, REFUSAL, True), 46)

    # --- With Baton ----------------------------------------------------------
    fade(frame_before(PROMPT, False, REFUSAL, True), frame_after("", True, None, [], False))
    add(frame_after("", True, None, [], False), 8)
    for i in range(1, len(PROMPT) + 1, 2):
        add(frame_after(PROMPT[:i], True, None, [], False))
    add(frame_after(PROMPT, True, None, [], False), 4)
    add(frame_after(PROMPT, False, None, [], False), 6)
    for p in range(26):
        add(frame_after(PROMPT, False, p, [], False))
    total = chars(ANSWER)
    for n in range(6, total + 6, 5):
        add(frame_after(PROMPT, False, None, reveal(ANSWER, n), False))
    add(frame_after(PROMPT, False, None, ANSWER, False), 5)
    add(frame_after(PROMPT, False, None, ANSWER, True), 52)
    fade(frame_after(PROMPT, False, None, ANSWER, True), frame_before("", True, [], False))
    return frames


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    gif = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "baton-demo.gif")
    tmp = tempfile.mkdtemp(prefix="baton-demo-")
    try:
        frames = build()
        for i, im in enumerate(frames):
            im.save(os.path.join(tmp, "f%04d.png" % i))
        palette = os.path.join(tmp, "palette.png")
        run(["-framerate", str(FPS), "-i", os.path.join(tmp, "f%04d.png"),
             "-vf", "palettegen=max_colors=96:stats_mode=diff", palette])
        run(["-framerate", str(FPS), "-i", os.path.join(tmp, "f%04d.png"), "-i", palette,
             "-lavfi", "paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle",
             "-loop", "0", gif])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("%s  %dx%d  %d frames  %.1fs  %.0fKB"
          % (gif, W, H, len(frames), len(frames) / FPS, os.path.getsize(gif) / 1024))


def run(args):
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg is not installed")
    subprocess.run(["ffmpeg", "-y", "-v", "error"] + args, check=True)


if __name__ == "__main__":
    main()
