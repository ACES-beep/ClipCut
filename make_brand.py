from PIL import Image, ImageFilter
import numpy as np
from pathlib import Path
import base64

src = Path(r"C:\Users\Sketchbook\.gemini\antigravity\brain\96d3144c-c6af-41be-b45c-a86f35162279\.user_uploaded\media_1795111312512_f7939872.png")
im = Image.open(src).convert("RGBA")
arr = np.array(im)

# Filter for dark cat body (ignore white paper and faint grid lines)
mask = ((arr[:, :, 0] < 140) & (arr[:, :, 1] < 140) & (arr[:, :, 2] < 140)).astype(np.uint8) * 255

coords = np.argwhere(mask > 0)
y0, x0 = coords.min(axis=0)
y1, x1 = coords.max(axis=0) + 1

cropped_mask = mask[y0:y1, x0:x1]
h, w = cropped_mask.shape

# Make high resolution version
target_h = 160
target_w = int(w * (target_h / h))
mask_img = Image.fromarray(cropped_mask).resize((target_w, target_h), Image.Resampling.LANCZOS)
mask_arr = np.array(mask_img)

mw, mh = mask_img.size

# Cyan version (#00F0FF)
cyan_arr = np.zeros((mh, mw, 4), dtype=np.uint8)
cyan_arr[:, :, 0] = 0
cyan_arr[:, :, 1] = 240
cyan_arr[:, :, 2] = 255
cyan_arr[:, :, 3] = mask_arr
im_cyan = Image.fromarray(cyan_arr)

# White version
white_arr = np.zeros((mh, mw, 4), dtype=np.uint8)
white_arr[:, :, 0] = 255
white_arr[:, :, 1] = 255
white_arr[:, :, 2] = 255
white_arr[:, :, 3] = mask_arr
im_white = Image.fromarray(white_arr)

brand_dir = Path(r"C:\Users\Sketchbook\.gemini\antigravity\scratch\ClipCut\web\brand")
brand_dir.mkdir(parents=True, exist_ok=True)

im_cyan.save(brand_dir / "cat-mark-cyan.png")
im_white.save(brand_dir / "cat-mark-white.png")
im_cyan.resize((64, 64), Image.Resampling.LANCZOS).save(brand_dir / "favicon.png")

b64 = base64.b64encode((brand_dir / "cat-mark-cyan.png").read_bytes()).decode("ascii")

svg_logo = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 60" width="140" height="35">
  <defs>
    <linearGradient id="cyanPurple" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#00F0FF"/>
      <stop offset="100%" stop-color="#A855F7"/>
    </linearGradient>
  </defs>
  <image href="data:image/png;base64,{b64}" x="0" y="6" width="50" height="48" preserveAspectRatio="xMidYMid meet"/>
  <text x="58" y="41" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif" font-size="26" font-weight="900" fill="#FFFFFF" letter-spacing="-0.5px">Clip<tspan fill="#00F0FF">Cut</tspan></text>
</svg>'''

(brand_dir / "logo-light.svg").write_text(svg_logo, encoding="utf-8")
(brand_dir / "logo.svg").write_text(svg_logo, encoding="utf-8")

svg_fav = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <rect width="100" height="100" rx="22" fill="#0B0F17"/>
  <image href="data:image/png;base64,{b64}" x="10" y="10" width="80" height="80" preserveAspectRatio="xMidYMid meet"/>
</svg>'''

(brand_dir / "favicon.svg").write_text(svg_fav, encoding="utf-8")
print("Regenerated brand assets successfully!")
