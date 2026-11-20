"""Turns a time range into a vertical clip: 9:16 layout, bouncy word captions, hook title, cover."""
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import config, roman
from .media import NO_WINDOW, run

W, H = config.OUT_W, config.OUT_H

# ASS colours are &HBBGGRR. `size` is the ASS font size (line height); _Font turns it into real pixels.
STYLES = {
    "bounce": {"label": "Pop-In Bounce", "font": "Poppins ExtraBold", "size": 132, "base": "FFFFFF", "active": "00F0FF",
               "strong": "FFFFFF", "outline": 7, "pop": 115, "upper": True, "per": 3},
    "highlight": {"label": "Neon Highlight", "font": "Poppins ExtraBold", "size": 128, "base": "CCCCCC", "active": "00FFFF",
                  "strong": "00FF66", "outline": 8, "pop": 108, "upper": True, "per": 3},
    "onebyone": {"label": "One-By-One (Fast Speed)", "font": "Poppins ExtraBold", "size": 142, "base": "FFFFFF", "active": "00F0FF",
                 "strong": "FFCC00", "outline": 8, "pop": 118, "upper": True, "per": 1},
    "boxed": {"label": "Boxed Pop-In", "font": "Poppins ExtraBold", "size": 120, "base": "FFFFFF", "active": "00F0FF",
              "strong": "FFFFFF", "outline": 6, "pop": 110, "upper": True, "per": 3, "box": True},
    "hype": {"label": "Hype (Bold Punch)", "font": "Anton", "size": 152, "base": "FFFFFF", "active": "3C3CFF",
             "strong": "00D7FF", "outline": 7, "pop": 114, "upper": True, "per": 2},
    "clean": {"label": "Clean (Subtle)", "font": "Poppins ExtraBold", "size": 100, "base": "FFFFFF", "active": "6BD7FF",
              "strong": "FFFFFF", "outline": 5, "pop": 106, "upper": False, "per": 4},
}
HOOK = {"font": "Poppins ExtraBold", "size": 96, "ink": "14100E", "box": "FFFFFF", "pad_x": 36, "pad_y": 22, "radius": 26,
        "max_w": 900}
FONT_FILES = {"Poppins ExtraBold": "Poppins-ExtraBold.ttf", "Anton": "Anton-Regular.ttf", "Archivo Black": "ArchivoBlack-Regular.ttf"}
CAP_MAX_W = 960   # captions never run wider than this, whatever is said


class _Font:
    """Measures text exactly as the renderer will draw it, so every word can be placed by hand: the highlighted
    word can grow without pushing its neighbours, and the heading box fits its text."""
    _cache: dict = {}

    def __init__(self, name):
        import struct
        path = Path(__file__).resolve().parent / "fonts" / FONT_FILES.get(name, FONT_FILES["Poppins ExtraBold"])
        d = path.read_bytes()
        tables = {}
        for i in range(struct.unpack(">H", d[4:6])[0]):
            tag, _, off, _ = struct.unpack(">4sIII", d[12 + 16 * i: 28 + 16 * i])
            tables[tag] = off
        self.upm = struct.unpack(">H", d[tables[b"head"] + 18: tables[b"head"] + 20])[0]
        o = tables[b"OS/2"]
        self.asc, self.desc = struct.unpack(">HH", d[o + 74: o + 78])      # what the renderer sizes the font by
        self.cap = struct.unpack(">h", d[o + 88: o + 90])[0] or int(self.upm * 0.7)
        self.path = str(path)
        self._pil = {}

    @classmethod
    def get(cls, name):
        if name not in cls._cache:
            cls._cache[name] = cls(name)
        return cls._cache[name]

    def em(self, size):
        return size * self.upm / (self.asc + self.desc)

    def width(self, text, size):
        """Pixels wide at this ASS size."""
        try:
            from PIL import ImageFont
            if "f" not in self._pil:
                self._pil["f"] = ImageFont.truetype(self.path, 200)
            return self._pil["f"].getlength(text) * self.em(size) / 200
        except Exception:  # noqa: BLE001 - no measuring library: a fair estimate keeps clips rendering
            return len(text) * self.em(size) * 0.62

    def cap_shift(self, size):
        """How far below the anchor the middle of a capital letter sits when text is centred on a point."""
        return ((self.asc - self.desc) / 2 - self.cap / 2) * size / (self.asc + self.desc)


CAPTION_LEAD = 0.0  # measured: word stamps are within 0.15 s of the voice, so no shift is needed
def install_fonts():
    """Copy the bundled caption fonts next to the data, so captions look the same on every machine and server."""
    src = Path(__file__).resolve().parent / "fonts"
    for f in src.glob("*.ttf"):
        if not (config.FONTS_DIR / f.name).exists():
            shutil.copyfile(f, config.FONTS_DIR / f.name)


LAYOUTS = {"gameplay": "Gameplay + Floating Facecam (Adjustable)",
           "split": "Split screen: Facecam on top, Gameplay on bottom",
           "crop": "Fill the screen (centre crop)",
           "blur": "Full frame + blurred fill",
           "zoom": "Zoomed in + blurred fill"}


def _ts(t):
    t = max(0.0, t)
    h, m = int(t // 3600), int(t % 3600 // 60)
    return f"{h}:{m:02d}:{t % 60:05.2f}"


def _clean(text):
    return re.sub(r"[\x00-\x1f\x7f]+", " ", re.sub(r"[{}\\]", "", str(text))).strip()


def _key(word):
    return re.sub(r"[^a-z0-9']", "", word.lower())


def _groups(words, per):
    out, cur = [], []
    for w in words:
        if cur and (len(cur) >= per or sum(len(x["w"]) for x in cur) + len(w["w"]) > 16
                    or w["s"] - cur[-1]["e"] > 0.45 or cur[-1]["w"][-1:] in ".?!"):
            out.append(cur)
            cur = []
        cur.append(w)
    if cur:
        out.append(cur)
    return out


def geometry(src_w, src_h, layout):
    """Where the video sits in the 1080x1920 frame, and where hook and captions go."""
    if src_h >= src_w and layout not in ("crop", "gameplay", "split"):
        layout = "crop"  # already vertical or square: nothing to gain from a blurred fill
    if layout == "blur":
        fg_h = min(H, int(round(W * src_h / src_w / 2)) * 2)
    elif layout == "zoom":
        fg_h = 1000
    elif layout == "split":
        return layout, H, 756, 1480
    else:
        fg_h = H
    top = (H - fg_h) // 2
    if layout in ("crop", "gameplay"):
        return layout, fg_h, 300, 1380
    return layout, fg_h, max(230, top - 190), min(1640, top + fg_h + 180)


def _hook_events(hook, dur, y, template="none"):
    """The heading / viral template card: X post card, breaking news chyron, tiktok hook, or clean box."""
    if not hook or not str(hook).strip():
        return []
    words = hook.split()
    if not words:
        return []
    span = f"{_ts(0)},{_ts(dur)}"
    n = lambda v: f"{v:.0f}"

    if template == "twitter":
        # 𝕏 / Twitter Post Card (Clean white card with avatar, handle, verified badge, and tweet text)
        bw, bh = 940, 240
        x0, y0, r = (W - bw) / 2, y - bh / 2, 24
        x1, y1 = x0 + bw, y0 + bh
        box = (f"m {n(x0 + r)} {n(y0)} l {n(x1 - r)} {n(y0)} b {n(x1)} {n(y0)} {n(x1)} {n(y0)} {n(x1)} {n(y0 + r)} "
               f"l {n(x1)} {n(y1 - r)} b {n(x1)} {n(y1)} {n(x1)} {n(y1)} {n(x1 - r)} {n(y1)} "
               f"l {n(x0 + r)} {n(y1)} b {n(x0)} {n(y1)} {n(x0)} {n(y1)} {n(x0)} {n(y1 - r)} "
               f"l {n(x0)} {n(y0 + r)} b {n(x0)} {n(y0)} {n(x0)} {n(y0)} {n(x0 + r)} {n(y0)}")
        ev = [
            f"Dialogue: 3,{span},Hook,,0,0,0,,{{\\an7\\pos(0,0)\\fad(140,0)\\c&HFFFFFF&\\p1}}{box}{{\\p0}}",
            # Avatar circle
            f"Dialogue: 4,{span},Hook,,0,0,0,,{{\\an5\\pos({n(x0 + 64)},{n(y0 + 56)})\\c&HF09B1D&\\fs48}}●",
            f"Dialogue: 5,{span},Hook,,0,0,0,,{{\\an5\\pos({n(x0 + 64)},{n(y0 + 56)})\\c&HFFFFFF&\\fs26\\b1}}𝕏",
            # Name, verified badge, and handle
            f"Dialogue: 4,{span},Hook,,0,0,0,,{{\\an4\\pos({n(x0 + 104)},{n(y0 + 44)})\\c&H14100E&\\fs38\\b1}}ClipCut {{\\c&HF09B1D&}}✓",
            f"Dialogue: 4,{span},Hook,,0,0,0,,{{\\an4\\pos({n(x0 + 104)},{n(y0 + 74)})\\c&H716453&\\fs26}}@viral_moments",
        ]
        # Tweet body text
        f = _Font.get("Poppins ExtraBold")
        lines = [" ".join(words)]
        size = 46
        if f.width(lines[0], size) > (bw - 100) and len(words) > 1:
            cut = min(range(1, len(words)), key=lambda i: max(f.width(" ".join(words[:i]), size), f.width(" ".join(words[i:]), size)))
            lines = [" ".join(words[:cut]), " ".join(words[cut:])]
        for i, line in enumerate(lines):
            line_y = y0 + 124 + i * 50
            ev.append(f"Dialogue: 4,{span},Hook,,0,0,0,,{{\\an4\\pos({n(x0 + 48)},{n(line_y)})\\c&H0F1419&\\fs{size}\\b1}}{line}")
        return ev

    if template == "breaking_news":
        # 🚨 Breaking News Lower-Third Chyron
        bar_y = max(y, 1420)
        ev = [
            # Red headline banner
            f"Dialogue: 3,{span},Hook,,0,0,0,,{{\\an7\\pos(0,{n(bar_y - 68)})\\fad(120,0)\\c&H1E1AE6&\\p1}}m 0 0 l {W} 0 l {W} 68 l 0 68{{\\p0}}",
            f"Dialogue: 4,{span},Hook,,0,0,0,,{{\\an4\\pos(48,{n(bar_y - 34)})\\c&HFFFFFF&\\fs42\\b1}}🚨 BREAKING NEWS",
            # Dark ticker bar
            f"Dialogue: 3,{span},Hook,,0,0,0,,{{\\an7\\pos(0,{n(bar_y)})\\fad(120,0)\\c&H0A0A0A&\\p1}}m 0 0 l {W} 0 l {W} 88 l 0 88{{\\p0}}",
            f"Dialogue: 4,{span},Hook,,0,0,0,,{{\\an4\\pos(48,{n(bar_y + 44)})\\c&H00F0FF&\\fs48\\b1}}{hook}",
        ]
        return ev

    if template == "tiktok_hook":
        # 💬 TikTok Retention Banner
        bw, bh = 920, 110
        x0, y0, r = (W - bw) / 2, y - bh / 2, 26
        x1, y1 = x0 + bw, y0 + bh
        box = (f"m {n(x0 + r)} {n(y0)} l {n(x1 - r)} {n(y0)} b {n(x1)} {n(y0)} {n(x1)} {n(y0)} {n(x1)} {n(y0 + r)} "
               f"l {n(x1)} {n(y1 - r)} b {n(x1)} {n(y1)} {n(x1)} {n(y1)} {n(x1 - r)} {n(y1)} "
               f"l {n(x0 + r)} {n(y1)} b {n(x0)} {n(y1)} {n(x0)} {n(y1)} {n(x0)} {n(y1 - r)} "
               f"l {n(x0)} {n(y0 + r)} b {n(x0)} {n(y0)} {n(x0)} {n(y0)} {n(x0 + r)} {n(y0)}")
        ev = [
            f"Dialogue: 3,{span},Hook,,0,0,0,,{{\\an7\\pos(0,0)\\fad(140,0)\\c&H0D1117&\\bord4\\3c&H00F0FF&\\p1}}{box}{{\\p0}}",
            f"Dialogue: 4,{span},Hook,,0,0,0,,{{\\an5\\pos({W // 2},{n(y)})\\fad(140,0)\\c&HFFFFFF&\\fs52\\b1}}🔥 {hook}",
        ]
        return ev

    # Standard clean Hook
    f, size = _Font.get(HOOK["font"]), HOOK["size"]
    lines = [" ".join(words)]
    if f.width(lines[0], size) > HOOK["max_w"] and len(words) > 1:      # two lines, split where they come out most even
        cut = min(range(1, len(words)), key=lambda i: max(f.width(" ".join(words[:i]), size), f.width(" ".join(words[i:]), size)))
        lines = [" ".join(words[:cut]), " ".join(words[cut:])]
    widest = max(f.width(l, size) for l in lines)
    if widest > HOOK["max_w"]:
        size = max(40, int(size * HOOK["max_w"] / widest))
        widest = max(f.width(l, size) for l in lines)
    line_h = f.em(size) * 1.16
    bw, bh = widest + 2 * HOOK["pad_x"], line_h * len(lines) + 2 * HOOK["pad_y"]
    x0, y0, r = (W - bw) / 2, y - bh / 2, min(HOOK["radius"], bh / 2)
    x1, y1 = x0 + bw, y0 + bh
    box = (f"m {n(x0 + r)} {n(y0)} l {n(x1 - r)} {n(y0)} b {n(x1)} {n(y0)} {n(x1)} {n(y0)} {n(x1)} {n(y0 + r)} "
           f"l {n(x1)} {n(y1 - r)} b {n(x1)} {n(y1)} {n(x1)} {n(y1)} {n(x1 - r)} {n(y1)} "
           f"l {n(x0 + r)} {n(y1)} b {n(x0)} {n(y1)} {n(x0)} {n(y1)} {n(x0)} {n(y1 - r)} "
           f"l {n(x0)} {n(y0 + r)} b {n(x0)} {n(y0)} {n(x0)} {n(y0)} {n(x0 + r)} {n(y0)}")
    span = f"{_ts(0)},{_ts(dur)}"
    ev = [f"Dialogue: 3,{span},Hook,,0,0,0,,{{\\an7\\pos(0,0)\\fad(160,0)\\c&H{HOOK['box']}&\\p1}}{box}{{\\p0}}"]
    for i, line in enumerate(lines):
        cy = y - line_h * (len(lines) - 1) / 2 + i * line_h - f.cap_shift(size)
        ev.append(f"Dialogue: 4,{span},Hook,,0,0,0,,{{\\an5\\pos({W // 2},{cy:.0f})\\fad(160,0)\\fs{size}}}{line}")
    return ev


def _generate_title_card_image(hook_text: str, template: str, hook_y: int, work_dir: Path) -> str:
    """Renders the title banner / template card as a high-resolution 32-bit RGBA PNG overlay with full color emojis."""
    from PIL import Image, ImageDraw, ImageFont
    import unicodedata

    if not hook_text or not hook_text.strip():
        return None

    words = hook_text.strip().split()
    if not words:
        return None

    def is_emoji_char(ch):
        cat = unicodedata.category(ch)
        code = ord(ch)
        if cat in ('So', 'Sk'):
            return True
        if 0x1F300 <= code <= 0x1FAFF or 0x2600 <= code <= 0x27BF or 0xFE00 <= code <= 0xFE0F:
            return True
        return False

    def split_text_emojis(text):
        runs = []
        curr = ''
        curr_is_em = None
        for ch in text:
            em = is_emoji_char(ch)
            if curr_is_em is None:
                curr_is_em = em
                curr += ch
            elif em == curr_is_em:
                curr += ch
            else:
                runs.append((curr, curr_is_em))
                curr = ch
                curr_is_em = em
        if curr:
            runs.append((curr, curr_is_em))
        return runs

    font_dir = Path(__file__).resolve().parent / "fonts"
    poppins_font = str(font_dir / "Poppins-ExtraBold.ttf")

    def get_emoji_font(size):
        for name in ('seguiemj.ttf', 'Apple Color Emoji.ttf', 'NotoColorEmoji.ttf'):
            try:
                return ImageFont.truetype(name, size)
            except Exception:
                pass
        return ImageFont.load_default()

    def draw_runs(d, runs, start_x, y, f_txt, f_em, color):
        try:
            txt_bb = f_txt.getbbox('ABCDEFGHIJKLMNOPQRSTUVWXYZ')
            cap_center = (txt_bb[1] + txt_bb[3]) / 2
        except Exception:
            cap_center = 0

        x = start_x
        for chunk, is_em in runs:
            font = f_em if is_em else f_txt
            if is_em:
                try:
                    em_bb = font.getbbox(chunk)
                    em_center = (em_bb[1] + em_bb[3]) / 2 if em_bb else 0
                    y_off = int(round(cap_center - em_center))
                except Exception:
                    y_off = 0
                d.text((x, y + y_off), chunk, font=font, embedded_color=True)
            else:
                d.text((x, y), chunk, font=font, fill=color)
            x += font.getlength(chunk)
        return x - start_x

    def runs_width(runs, f_txt, f_em):
        return sum(f_em.getlength(r[0]) if r[1] else f_txt.getlength(r[0]) for r in runs)

    im = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)

    if template == "twitter":
        bw, bh = 940, 240
        x0, y0 = (W - bw) // 2, int(hook_y - bh // 2)
        x1, y1 = x0 + bw, y0 + bh
        d.rounded_rectangle((x0, y0, x1, y1), radius=24, fill=(255, 255, 255, 255))
        av_cx, av_cy, av_r = x0 + 64, y0 + 56, 26
        d.ellipse((av_cx - av_r, av_cy - av_r, av_cx + av_r, av_cy + av_r), fill=(240, 155, 29, 255))
        f_av = ImageFont.truetype(poppins_font, 26)
        d.text((av_cx - 8, av_cy - 16), "𝕏", font=f_av, fill=(255, 255, 255, 255))
        f_hdr = ImageFont.truetype(poppins_font, 30)
        f_sub = ImageFont.truetype(poppins_font, 22)
        d.text((x0 + 104, y0 + 34), "ClipCut ✓", font=f_hdr, fill=(20, 16, 14, 255))
        d.text((x0 + 104, y0 + 68), "@viral_moments", font=f_sub, fill=(113, 100, 83, 255))
        f_txt = ImageFont.truetype(poppins_font, 38)
        f_em = get_emoji_font(38)
        lines = [" ".join(words)]
        if runs_width(split_text_emojis(lines[0]), f_txt, f_em) > (bw - 100) and len(words) > 1:
            cut = min(range(1, len(words)), key=lambda i: max(
                runs_width(split_text_emojis(" ".join(words[:i])), f_txt, f_em),
                runs_width(split_text_emojis(" ".join(words[i:])), f_txt, f_em)
            ))
            lines = [" ".join(words[:cut]), " ".join(words[cut:])]
        for i, line in enumerate(lines):
            line_y = y0 + 116 + i * 48
            runs = split_text_emojis(line)
            draw_runs(d, runs, x0 + 48, line_y, f_txt, f_em, (15, 20, 25, 255))

    elif template == "breaking_news":
        bar_y = max(int(hook_y), 1420)
        d.rectangle((0, bar_y - 68, W, bar_y), fill=(230, 26, 30, 255))
        f_bn = ImageFont.truetype(poppins_font, 38)
        f_bn_em = get_emoji_font(38)
        draw_runs(d, split_text_emojis("🚨 BREAKING NEWS"), 48, bar_y - 56, f_bn, f_bn_em, (255, 255, 255, 255))
        d.rectangle((0, bar_y, W, bar_y + 88), fill=(10, 10, 10, 255))
        f_txt = ImageFont.truetype(poppins_font, 44)
        f_em = get_emoji_font(42)
        draw_runs(d, split_text_emojis(hook_text), 48, bar_y + 20, f_txt, f_em, (0, 240, 255, 255))

    elif template == "tiktok_hook":
        f_txt = ImageFont.truetype(poppins_font, 44)
        f_em = get_emoji_font(42)
        lines = [" ".join(words)]
        if runs_width(split_text_emojis(lines[0]), f_txt, f_em) > 860 and len(words) > 1:
            cut = min(range(1, len(words)), key=lambda i: max(
                runs_width(split_text_emojis(" ".join(words[:i])), f_txt, f_em),
                runs_width(split_text_emojis(" ".join(words[i:])), f_txt, f_em)
            ))
            lines = [" ".join(words[:cut]), " ".join(words[cut:])]
        bw = 920
        bh = 60 + len(lines) * 54
        x0, y0 = (W - bw) // 2, int(hook_y - bh // 2)
        x1, y1 = x0 + bw, y0 + bh
        d.rounded_rectangle((x0, y0, x1, y1), radius=26, fill=(13, 17, 23, 255), outline=(0, 240, 255, 255), width=4)
        for i, line in enumerate(lines):
            runs = split_text_emojis(line)
            lw = runs_width(runs, f_txt, f_em)
            lx = x0 + (bw - lw) // 2
            ly = y0 + 26 + i * 54
            draw_runs(d, runs, lx, ly, f_txt, f_em, (255, 255, 255, 255))

    else:
        # Standard Minimal Clean Box
        f_txt = ImageFont.truetype(poppins_font, 46)
        f_em = get_emoji_font(44)
        lines = [" ".join(words)]
        if runs_width(split_text_emojis(lines[0]), f_txt, f_em) > 840 and len(words) > 1:
            cut = min(range(1, len(words)), key=lambda i: max(
                runs_width(split_text_emojis(" ".join(words[:i])), f_txt, f_em),
                runs_width(split_text_emojis(" ".join(words[i:])), f_txt, f_em)
            ))
            lines = [" ".join(words[:cut]), " ".join(words[cut:])]
        line_runs = [split_text_emojis(l) for l in lines]
        line_widths = [runs_width(r, f_txt, f_em) for r in line_runs]
        max_lw = max(line_widths)
        pad_x, pad_y = 38, 22
        bw = max(260, min(1000, int(max_lw + pad_x * 2)))
        line_h = 56
        bh = int(len(lines) * line_h + pad_y * 2 - 8)
        x0, y0 = (W - bw) // 2, int(hook_y - bh // 2)
        x1, y1 = x0 + bw, y0 + bh
        d.rounded_rectangle((x0 + 4, y0 + 6, x1 + 4, y1 + 6), radius=26, fill=(0, 0, 0, 60))
        d.rounded_rectangle((x0, y0, x1, y1), radius=26, fill=(255, 255, 255, 255))
        for i, runs in enumerate(line_runs):
            lw = line_widths[i]
            lx = x0 + (bw - lw) // 2
            ly = y0 + pad_y + i * line_h - 2
            draw_runs(d, runs, lx, ly, f_txt, f_em, (20, 16, 14, 255))

    card_path = work_dir / "card.png"
    im.save(card_path)
    return "card.png"


def build_ass(words, start, end, hook, style_name, highlight, hook_y, cap_y, template="none") -> str:
    st = STYLES.get(style_name, STYLES["bounce"])
    words = [dict(w, s=w["s"] - CAPTION_LEAD, e=w["e"] - CAPTION_LEAD) for w in words]
    strong = {_key(h) for h in highlight if _key(h)}
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{st['font']},{st['size']},&H00{st['base']},&H00{st['base']},&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,{st['outline']},0,5,0,0,0,1
Style: Hook,{HOOK['font']},{HOOK['size']},&H00{HOOK['ink']},&H00{HOOK['ink']},&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    dur = end - start
    ev = _hook_events(_clean(hook), dur, hook_y, template=template)
    f = _Font.get(st["font"])
    inside = [dict(w, w=_clean(w["w"])) for w in words if w["s"] >= start - 0.05 and w["s"] < end]
    inside = sorted((w for w in inside if w["w"]), key=lambda w: w["s"])
    for i, w in enumerate(inside[:-1]):
        w["e"] = max(w["s"], min(w["e"], inside[i + 1]["s"]))
    groups = _groups(inside, st["per"])
    for gi, g in enumerate(groups):
        g_start = max(0.0, g[0]["s"] - start)
        nxt_s = max(g_start, (groups[gi + 1][0]["s"] - start) if gi + 1 < len(groups) else dur)
        # Smart hold duration:
        # Instead of vanishing 0.25s after speech stops, hold the group caption during natural pauses
        # for up to 1.5s (or until next group / end of clip), so captions stay visible and don't flicker off into dead void.
        natural_end = g[-1]["e"] - start
        g_end = min(max(natural_end + 2.2, g_start + 1.2), nxt_s)
        if g_end - g_start < 0.02:
            continue
        texts = []
        for x in g:
            t = re.sub(r"[.,;:]+$", "", x["w"])
            texts.append(t.upper() if st["upper"] else t)
        if not all(roman.drawable(t) for t in texts):
            # A script the caption font cannot draw (Japanese, Thai, Chinese...): its words cannot be measured, so
            # they are not placed one by one. The line is drawn whole and centred, in a font the system has for it.
            line = " ".join(texts)
            fs = max(44, min(int(st["size"] * 0.7), int(1500 / max(1, len(line)) * 1.6)))
            ev.append(f"Dialogue: 1,{_ts(g_start)},{_ts(g_end)},Cap,,60,60,0,,{{\\an5\\pos({W // 2},{cap_y:.0f})\\q0\\fs{fs}}}{line}")
            continue
        size = st["size"]
        gap = f.width("n n", size) - 2 * f.width("n", size) + (2 * st["outline"] + 6) * size / st["size"]   # a space, plus room for both outlines
        total = sum(f.width(t, size) for t in texts) + gap * (len(texts) - 1)
        if total > CAP_MAX_W:                                  # a long phrase: shrink it to fit, never run off the frame
            size = max(36, int(size * CAP_MAX_W / total))
            gap = f.width("n n", size) - 2 * f.width("n", size) + (2 * st["outline"] + 6) * size / st["size"]   # a space, plus room for both outlines
            total = sum(f.width(t, size) for t in texts) + gap * (len(texts) - 1)
        cy = cap_y - f.cap_shift(size)
        x = (W - total) / 2
        p = st["pop"]
        for i, (w, t) in enumerate(zip(g, texts)):
            wd = f.width(t, size)
            p = max(104, min(st["pop"], 100 + int(2200 / max(wd, 1))))   # long words grow less, so they never touch their neighbours
            pos = f"\\an5\\pos({x + wd / 2:.0f},{cy:.0f})\\fs{size}"
            a = max(g_start, w["s"] - start)
            b = (g[i + 1]["s"] - start) if i + 1 < len(g) else g_end
            base = st["strong"] if _key(w["w"]) in strong else st["base"]

            if st.get("box") and b - a >= 0.02:
                n = lambda v: f"{v:.0f}"
                bx0, by0 = x - 14, cy - size * 0.55
                bx1, by1 = x + wd + 14, cy + size * 0.55
                br = 12
                box_shape = (f"m {n(bx0 + br)} {n(by0)} l {n(bx1 - br)} {n(by0)} b {n(bx1)} {n(by0)} {n(bx1)} {n(by0)} {n(bx1)} {n(by0 + br)} "
                             f"l {n(bx1)} {n(by1 - br)} b {n(bx1)} {n(by1)} {n(bx1)} {n(by1)} {n(bx1 - br)} {n(by1)} "
                             f"l {n(bx0 + br)} {n(by1)} b {n(bx0)} {n(by1)} {n(bx0)} {n(by1)} {n(bx0)} {n(by1 - br)} "
                             f"l {n(bx0)} {n(by0 + br)} b {n(bx0)} {n(by0)} {n(bx0)} {n(by0)} {n(bx0 + br)} {n(by0)}")
                ev.append(f"Dialogue: 1,{_ts(a)},{_ts(b)},Cap,,0,0,0,,{{\\an7\\pos(0,0)\\c&H0A0D14&\\bord2\\3c&H00F0FF&\\p1}}{box_shape}{{\\p0}}")

            x += wd + gap
            if a - g_start >= 0.02:                                                   # waiting to be said
                ev.append(f"Dialogue: 1,{_ts(g_start)},{_ts(a)},Cap,,0,0,0,,{{{pos}\\c&H{base}&}}{t}")
            if b - a >= 0.02:                                                         # being said: colour and a quick pop, in front
                ev.append(f"Dialogue: 2,{_ts(a)},{_ts(b)},Cap,,0,0,0,,{{{pos}\\c&H{st['active']}&\\fscx94\\fscy94"
                          f"\\t(0,90,\\fscx{p}\\fscy{p})\\t(90,200,\\fscx{p - 5}\\fscy{p - 5})}}{t}")
            if g_end - b >= 0.02:                                                     # said
                ev.append(f"Dialogue: 1,{_ts(b)},{_ts(g_end)},Cap,,0,0,0,,{{{pos}\\c&H{base}&\\fscx{p - 5}\\fscy{p - 5}"
                          f"\\t(0,90,\\fscx100\\fscy100)}}{t}")
    return head + "\n".join(ev) + "\n"


PIP_W = 760            # default width for facecam card in the 1080-wide vertical frame
PIP_MARGIN = 26


def _norm_box(box, src_w=1920, src_h=1080):
    if not box or len(box) != 4:
        return None
    try:
        raw_x, raw_y, raw_w, raw_h = [float(v) for v in box]
        if raw_w <= 8 or raw_h <= 8:
            return None
        # Scale from 1920x1080 studio canvas space to actual video resolution
        scale_x = src_w / 1920.0
        scale_y = src_h / 1080.0
        x = int(round(raw_x * scale_x))
        y = int(round(raw_y * scale_y))
        w = int(round(raw_w * scale_x))
        h = int(round(raw_h * scale_y))

        x = max(0, min(src_w - 10, x))
        y = max(0, min(src_h - 10, y))
        w = max(10, min(src_w - x, w))
        h = max(10, min(src_h - y, h))
        return (x // 2) * 2, (y // 2) * 2, (w // 2) * 2, (h // 2) * 2
    except (ValueError, TypeError):
        return None


def _filter(layout, fg_h, facecam=None, game_area=None, facecam_pos=None, src_w=1920, src_h=1080, mask_file=None):
    fc_box = _norm_box(facecam, src_w, src_h)
    gm_box = _norm_box(game_area, src_w, src_h)

    if layout == "split":
        # Split screen: Facecam on top half (720px), Game on bottom (1200px)
        TOP_H = 720
        BOT_H = H - TOP_H
        if fc_box:
            fx, fy, fw, fh = fc_box
            fc_f = f"[0:v]crop={fw}:{fh}:{fx}:{fy},scale={W}:{TOP_H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{TOP_H}:(in_w-{W})/2:0,setsar=1[top];"
        else:
            fc_f = f"[0:v]scale={W}:{TOP_H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{TOP_H}:(in_w-{W})/2:0,setsar=1[top];"

        if gm_box:
            gx, gy, gw, gh = gm_box
            gm_f = f"[0:v]crop={gw}:{gh}:{gx}:{gy},scale={W}:{BOT_H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{BOT_H},setsar=1[bot];"
        else:
            gm_f = f"[0:v]scale={W}:{BOT_H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{BOT_H},setsar=1[bot];"

        return f"[0:v]fps={config.OUT_FPS},split=2[in_fc][in_gm];" + fc_f.replace("[0:v]", "[in_fc]") + gm_f.replace("[0:v]", "[in_gm]") + "[top][bot]vstack=inputs=2[v0];"

    if layout == "gameplay" and fc_box:
        # Background is main game (cropped to game_area if set, else center crop)
        # Foreground is floating facecam placed at facecam_pos
        fx, fy, fw, fh = fc_box
        if gm_box:
            gx, gy, gw, gh = gm_box
            base_crop = f"crop={gw}:{gh}:{gx}:{gy},scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H},setsar=1"
        else:
            base_crop = f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H},setsar=1"

        if facecam_pos and len(facecam_pos) == 4:
            px, py, pw, ph = [int(round(float(v))) for v in facecam_pos]
            pw = max(100, min(W, pw))
            ph = max(60, min(H, ph))
            px = max(0, min(W - pw, px))
            py = max(0, min(H - ph, py))
        else:
            # Reference layout style: Centered floating card near top
            pw = min(760, W - 40)
            ph = max(40, int(round(pw * fh / fw)))
            px = (W - pw) // 2
            py = 120

        pw = (pw // 2) * 2
        ph = (ph // 2) * 2

        if mask_file:
            return (f"[0:v]fps={config.OUT_FPS},split=2[base_in][fc_in];"
                    f"[base_in]{base_crop}[b];"
                    f"[fc_in]crop={fw}:{fh}:{fx}:{fy},scale={pw}:{ph}:flags=lanczos,setsar=1,format=yuva420p[fc_scaled];"
                    f"movie={mask_file}[fc_mask];"
                    f"[fc_scaled][fc_mask]alphamerge[p];"
                    f"[b][p]overlay={px}:{py}[v0];")
        return (f"[0:v]fps={config.OUT_FPS},split=2[base_in][fc_in];"
                f"[base_in]{base_crop}[b];"
                f"[fc_in]crop={fw}:{fh}:{fx}:{fy},scale={pw}:{ph}:flags=lanczos,setsar=1[p];"
                f"[b][p]overlay={px}:{py}[v0];")

    if layout in ("crop", "gameplay"):
        if gm_box:
            gx, gy, gw, gh = gm_box
            return f"[0:v]fps={config.OUT_FPS},crop={gw}:{gh}:{gx}:{gy},scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H},setsar=1[v0];"
        return f"[0:v]fps={config.OUT_FPS},scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H},setsar=1[v0];"

    return (f"[0:v]fps={config.OUT_FPS},split=2[a][b];"
            f"[a]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=6:2,"
            f"scale={W}:{H},eq=brightness=-0.14:saturation=1.15[bg];"
            f"[b]scale={W}:{fg_h}:force_original_aspect_ratio=increase,crop={W}:{fg_h}[fg];"
            f"[bg][fg]overlay=0:(H-h)/2,setsar=1[v0];")


def _hook_y(layout, hook_y, facecam=None, facecam_pos=None):
    """Where the heading goes. In the gameplay layout the webcam sits on the video, so the heading
    sits nicely right below the facecam card (matching reference orientation)."""
    if layout == "gameplay" and facecam and len(facecam) == 4 and facecam[2] > 8:
        if facecam_pos and len(facecam_pos) == 4:
            py = int(facecam_pos[1])
            ph = int(facecam_pos[3])
        else:
            py = 120
            ph = int(round(PIP_W * facecam[3] / facecam[2]))
        if py < 900:  # facecam is in upper half of video
            return py + ph + 42
    elif layout == "split":
        return 720
    return hook_y


_qsv_ok = None
RENDER_SECONDS = 600      # one clip's video encode; far beyond normal, so a frozen encoder is stopped, not waited on


def render_clip(src: Path, out_dir: Path, name: str, clip: dict, words: list, src_w: int, src_h: int) -> dict:
    """Writes <name>.mp4, <name>.jpg and <name>.ass into out_dir. Returns file names."""
    out_dir.mkdir(parents=True, exist_ok=True)
    start, end = clip["start"], clip["end"]
    layout, fg_h, hook_y, cap_y = geometry(src_w, src_h, clip.get("layout", "gameplay"))
    face = clip.get("facecam")
    game_area = clip.get("game_area")
    facecam_pos = clip.get("facecam_pos")
    if layout == "gameplay" and not (face and len(face) == 4):
        layout = "crop"     # no webcam found: the game fills the screen
    hook_y = _hook_y(layout, hook_y, face, facecam_pos)
    show_title = clip.get("show_title", clip.get("show_hook", True))
    hook_text = (clip.get("hook") or clip.get("title") or "") if show_title else ""
    template = clip.get("template", "none")
    work = Path(tempfile.mkdtemp(prefix="pk"))
    try:
        card_png = None
        if hook_text and hook_text.strip():
            try:
                card_png = _generate_title_card_image(hook_text.strip(), template, hook_y, work)
            except Exception:
                card_png = None
        ass = build_ass(words if clip.get("captions", True) else [], start, end,
                        "" if card_png else hook_text, clip.get("style", "bounce"),
                        clip.get("highlight_words", []), hook_y, cap_y, template=template)
        return _render_in(work, src, out_dir, name, start, end, layout, fg_h, ass,
                          face=face, game_area=game_area, facecam_pos=facecam_pos,
                          src_w=src_w, src_h=src_h, card_png=card_png)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _render_in(work: Path, src: Path, out_dir: Path, name: str, start, end, layout, fg_h, ass: str,
               face=None, game_area=None, facecam_pos=None, src_w=1920, src_h=1080, card_png=None) -> dict:
    global _qsv_ok
    (work / f"{name}.ass").write_text(ass, encoding="utf-8")
    (work / "f").mkdir()
    for f in config.FONTS_DIR.glob("*.ttf"):
        shutil.copyfile(f, work / "f" / f.name)

    # Generate rounded corner mask for facecam if in gameplay layout
    mask_file = None
    if layout == "gameplay" and face and len(face) == 4 and face[2] > 8:
        try:
            from PIL import Image, ImageDraw
            if facecam_pos and len(facecam_pos) == 4:
                pw, ph = int(facecam_pos[2]), int(facecam_pos[3])
            else:
                pw = min(PIP_W, W - 40)
                ph = max(40, int(round(pw * face[3] / face[2])))
            pw = (pw // 2) * 2
            ph = (ph // 2) * 2
            rad = min(28, pw // 8, ph // 8)
            im = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
            draw = ImageDraw.Draw(im)
            draw.rounded_rectangle((0, 0, pw - 1, ph - 1), radius=rad, fill=(255, 255, 255, 255))
            m_path = work / "facecam_mask.png"
            im.save(m_path)
            mask_file = "facecam_mask.png"
        except Exception:
            mask_file = None

    fc = _filter(layout, fg_h, facecam=face, game_area=game_area, facecam_pos=facecam_pos,
                 src_w=src_w, src_h=src_h, mask_file=mask_file)
    if card_png and (work / card_png).is_file():
        fc += f"movie={card_png}[card];[v0][card]overlay=0:0[v_card];[v_card]ass={name}.ass:fontsdir=f[v]"
    else:
        fc += f"[v0]ass={name}.ass:fontsdir=f[v]"
    base = ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.2f}", "-t", f"{end - start:.2f}", "-i", str(src),
            "-filter_complex", fc, "-map", "[v]", "-map", "0:a:0", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart", "-pix_fmt"]
    encoders = []
    if _qsv_ok is not False:
        encoders.append(("qsv", ["nv12", "-c:v", "h264_qsv", "-global_quality", "19", "-preset", "medium", "-b:v", "8M"]))
    encoders.append(("x264", ["yuv420p", "-c:v", "libx264", "-crf", "18", "-preset", "faster", "-b:v", "8M", "-maxrate", "12M", "-bufsize", "16M"]))
    err = None
    for label, enc in encoders:
        p = subprocess.run(base + enc + [f"{name}.mp4"], cwd=work, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", creationflags=NO_WINDOW, timeout=RENDER_SECONDS)
        if p.returncode == 0:
            if label == "qsv":
                _qsv_ok = True
            break
        err = p.stderr[-1200:]
        if label == "qsv":
            _qsv_ok = False
    else:
        raise RuntimeError(f"Render failed: {err}")
    cover_t = min(1.2, (end - start) / 2)
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{cover_t:.2f}", "-i", f"{name}.mp4", "-frames:v", "1",
         "-q:v", "3", f"{name}.jpg"], cwd=work)
    for ext in ("mp4", "jpg", "ass"):
        shutil.move(str(work / f"{name}.{ext}"), str(out_dir / f"{name}.{ext}"))
    return {"file": f"{name}.mp4", "cover": f"{name}.jpg", "layout": layout}
