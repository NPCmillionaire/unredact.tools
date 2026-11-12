"""Render docs/demo.gif from real unredact-file output.

Runs each command under `script` (so the CLI sees a TTY and emits colors), then
replays it in a fake terminal: types the command, streams the output, pauses on
the interesting parts. Usage:  python docs/make_demo_gif.py WORKDIR OUT.gif
WORKDIR must contain vacation.jpg, court_filing.pdf, memo.docx
(see tests/make_fixtures.py).
"""
import re
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont

WORKDIR, OUT = sys.argv[1], sys.argv[2]
COLS, ROWS = 104, 34
FS = 15
FONT = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", FS)
BOLD = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", FS)
CW = round(FONT.getlength("M"))
LH = FS + 5
PAD, BAR = 18, 34
W, H = PAD * 2 + COLS * CW, BAR + PAD * 2 + ROWS * LH

BG = (13, 17, 23)
FG = (201, 209, 217)
PAL = {30: (110, 118, 129), 31: (255, 123, 114), 32: (63, 185, 80), 33: (210, 153, 34),
       34: (88, 166, 255), 35: (188, 140, 255), 36: (57, 197, 207), 37: FG, 97: (255, 255, 255)}
BGPAL = {41: (218, 54, 51)}

SCENES = [
    # (command, pause-after-output seconds, pause triggers: {substring: seconds})
    ("unredact-file --help", 1.2, {"╰": 3.2}),
    ("unredact-file scan vacation.jpg", 2.2, {"HIDDEN CONTENT": 2.6}),
    ("unredact-file scan court_filing.pdf", 2.0, {"FAILED REDACTION": 3.0}),
    ("unredact-file sanitize vacation.jpg court_filing.pdf memo.docx", 2.4, {}),
    ("unredact-file scan vacation.clean.jpg court_filing.clean.pdf memo.clean.docx", 3.5, {}),
]

SGR = re.compile(r"\x1b\[([0-9;]*)m")


def run(cmd):
    out = subprocess.run(["script", "-qc", cmd, "/dev/null"], cwd=WORKDIR,
                         capture_output=True, text=True, env={"COLUMNS": str(COLS), "UNREDACT_TIP": "3",
                              "PATH": subprocess.os.environ["PATH"]}).stdout
    return out.replace("\r", "").rstrip("\n").split("\n")


def xterm256(n):
    if n < 16:
        return PAL.get(30 + n % 8, FG)
    if n >= 232:
        v = 8 + (n - 232) * 10
        return (v, v, v)
    n -= 16
    steps = [0, 95, 135, 175, 215, 255]
    return (steps[n // 36], steps[(n // 6) % 6], steps[n % 6])


def parse(line, style=None):
    """ANSI line -> list of (char, style) where style=(fg, bg, bold, dim)."""
    style = style or (FG, None, False, False)
    cells, pos = [], 0
    for m in SGR.finditer(line):
        cells += [(ch, style) for ch in line[pos:m.start()]]
        fg, bg, bold, dim = style
        codes = [int(c) if c else 0 for c in m.group(1).split(";")]
        while codes:
            code = codes.pop(0)
            if code == 38 and codes[:1] == [5]:
                fg = xterm256(codes[1])
                codes = codes[2:]
                continue
            if code == 0:
                fg, bg, bold, dim = FG, None, False, False
            elif code == 1:
                bold = True
            elif code == 2:
                dim = True
            elif code in PAL:
                fg = PAL[code]
            elif code in BGPAL:
                bg = BGPAL[code]
        style = (fg, bg, bold, dim)
        pos = m.end()
    cells += [(ch, style) for ch in line[pos:]]
    return cells, style


def wrap(cells):
    if not cells:
        return [[]]
    return [cells[i:i + COLS] for i in range(0, len(cells), COLS)]


class Term:
    def __init__(self):
        self.lines = []
        self.frames = []

    def push(self, cells):
        self.lines.extend(wrap(cells))
        self.lines = self.lines[-ROWS:]

    def set_last(self, cells):
        if self.lines:
            self.lines.pop()
        self.push(cells)

    def render(self, cursor=False):
        im = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(im)
        # window chrome
        d.rectangle([0, 0, W, BAR], fill=(22, 27, 34))
        for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
            d.ellipse([14 + i * 22, 11, 26 + i * 22, 23], fill=c)
        title = "unredact-file — see what your files give away"
        d.text(((W - FONT.getlength(title)) / 2, 9), title, font=FONT, fill=(139, 148, 158))
        y = BAR + PAD
        for row in self.lines:
            x = PAD
            for ch, (fg, bg, bold, dim) in row:
                if bg:
                    d.rectangle([x, y - 1, x + CW, y + LH - 3], fill=bg)
                col = tuple(int(c * 0.6 + b * 0.4) for c, b in zip(fg, BG)) if dim else fg
                top, bot = y - 3, y + LH - 3  # full cell, so block glyphs join up like a real terminal
                if ch == "█":
                    d.rectangle([x, top, x + CW - 1, bot - 1], fill=col)
                elif ch == "▀":
                    d.rectangle([x, top, x + CW - 1, (top + bot) // 2 - 1], fill=col)
                elif ch == "▄":
                    d.rectangle([x, (top + bot) // 2, x + CW - 1, bot - 1], fill=col)
                else:
                    d.text((x, y), ch, font=BOLD if bold else FONT, fill=col)
                x += CW
            y += LH
        if cursor:
            cx = PAD + (len(self.lines[-1]) if self.lines else 0) * CW
            d.rectangle([cx, y - LH, cx + CW - 2, y - 4], fill=(201, 209, 217))
        return im

    def snap(self, ms, cursor=False):
        self.frames.append((self.render(cursor), ms))


PROMPT = [(c, ((63, 185, 80), None, True, False)) for c in "➜ "] + \
         [(c, ((88, 166, 255), None, True, False)) for c in "demo "]


def main():
    t = Term()
    t.push(list(PROMPT))
    t.snap(900, cursor=True)
    for cmd, end_pause, triggers in SCENES:
        out = run(cmd)
        typed = list(PROMPT)
        for i, ch in enumerate(cmd):
            typed.append((ch, (FG, None, False, False)))
            t.set_last(typed)
            if i % 2 or i == len(cmd) - 1:
                t.snap(38, cursor=True)
        t.snap(350, cursor=True)
        style = None
        batch = 0
        pending = None  # pause lands one line after the trigger, so its detail is visible
        for line in out:
            cells, style = parse(line, style)
            t.push(cells)
            batch += 1
            if pending is not None:
                t.snap(int(pending * 1000))
                pending, batch = None, 0
                continue
            hit = next((s for k, s in triggers.items() if k in line), None)
            if hit:
                pending = hit
            elif batch >= 3:
                t.snap(45)
                batch = 0
        t.snap(int(end_pause * 1000))
        t.push([])
        t.push(list(PROMPT))
        t.snap(500, cursor=True)

    frames = [f for f, _ in t.frames]
    durs = [d for _, d in t.frames]
    # shared palette keeps colors stable and the file small
    # build one palette from a strip of sampled frames so every color (incl. the red badge) is in it
    sample = frames[::max(1, len(frames) // 12)]
    strip = Image.new("RGB", (W, H * len(sample)))
    for i, f in enumerate(sample):
        strip.paste(f, (0, H * i))
    pal = strip.quantize(colors=96, method=Image.Quantize.MEDIANCUT)
    q = [f.quantize(palette=pal, dither=Image.Dither.NONE) for f in frames]
    q[0].save(OUT, save_all=True, append_images=q[1:], duration=durs, loop=0,
              optimize=True, disposal=1)
    print(f"{len(q)} frames, {sum(durs) / 1000:.1f}s, {W}x{H}")


main()
