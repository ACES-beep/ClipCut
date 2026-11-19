from PIL import Image
import numpy as np
import base64
from pathlib import Path

src = Path(r"C:\Users\Sketchbook\.gemini\antigravity\brain\96d3144c-c6af-41be-b45c-a86f35162279\.user_uploaded\media_1795111312512_f7939872.png")
im = Image.open(src).convert("RGBA")
arr = np.array(im)

gray = np.mean(arr[:, :, :3], axis=2)
mask = (gray < 140).astype(np.uint8) * 255

coords = np.argwhere(mask > 0)
y0, x0 = coords.min(axis=0)
y1, x1 = coords.max(axis=0) + 1

cropped_mask = mask[y0:y1, x0:x1]
h, w = cropped_mask.shape

# White version
out_white = np.zeros((h, w, 4), dtype=np.uint8)
out_white[:, :, 0] = 255
out_white[:, :, 1] = 255
out_white[:, :, 2] = 255
out_white[:, :, 3] = cropped_mask

# Cyan version
out_cyan = np.zeros((h, w, 4), dtype=np.uint8)
out_cyan[:, :, 0] = 0
out_cyan[:, :, 1] = 240
out_cyan[:, :, 2] = 255
out_cyan[:, :, 3] = cropped_mask

im_white = Image.fromarray(out_white)
im_cyan = Image.fromarray(out_cyan)

target_h = 160
target_w = int(w * (target_h / h))
im_white_hq = im_white.resize((target_w, target_h), Image.Resampling.LANCZOS)
im_cyan_hq = im_cyan.resize((target_w, target_h), Image.Resampling.LANCZOS)

brand_dir = Path(r"C:\Users\Sketchbook\.gemini\antigravity\scratch\ClipCut\web\brand")
brand_dir.mkdir(parents=True, exist_ok=True)

cyan_png_path = brand_dir / "cat-mark-cyan.png"
white_png_path = brand_dir / "cat-mark-white.png"
fav_png_path = brand_dir / "favicon.png"

im_cyan_hq.save(cyan_png_path)
im_white_hq.save(white_png_path)
im_cyan_hq.save(fav_png_path)

b64_cyan = base64.b64encode(cyan_png_path.read_bytes()).decode("ascii")

svg_logo = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 280 72" width="140" height="36">
  <defs>
    <linearGradient id="brandGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#00F0FF"/>
      <stop offset="100%" stop-color="#A855F7"/>
    </linearGradient>
  </defs>
  <image href="data:image/png;base64,{b64_cyan}" x="4" y="6" width="60" height="60" preserveAspectRatio="xMidYMid meet"/>
  <text x="72" y="46" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif" font-size="30" font-weight="800" fill="#FFFFFF" letter-spacing="-0.5px">Clip<tspan fill="url(#brandGrad)">Cut</tspan></text>
</svg>'''

(brand_dir / "logo-light.svg").write_text(svg_logo, encoding="utf-8")
(brand_dir / "logo.svg").write_text(svg_logo, encoding="utf-8")

svg_fav = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <rect width="100" height="100" rx="22" fill="#0B0F17"/>
  <image href="data:image/png;base64,{b64_cyan}" x="12" y="12" width="76" height="76" preserveAspectRatio="xMidYMid meet"/>
</svg>'''

(brand_dir / "favicon.svg").write_text(svg_fav, encoding="utf-8")
print("Brand assets successfully generated!")
