"""Create the fictional, visibly invalid OCR fixture. Run from any directory."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent


def font(size):
    for candidate in ("C:/Windows/Fonts/arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


image = Image.new("RGB", (1600, 950), "#f4f7fc")
draw = ImageDraw.Draw(image)
draw.rounded_rectangle((55, 55, 1545, 895), radius=32, fill="white", outline="#ced8e6", width=3)
draw.rounded_rectangle((85, 85, 1515, 215), radius=20, fill="#fff1e8")
draw.text((122, 120), "SYNTHETIC DEMO - NOT VALID", font=font(62), fill="#9b3419")
draw.text((120, 295), "Document type: CNIC", font=font(58), fill="#12233e")
draw.text((120, 415), "Full Name: Ayesha Khan", font=font(64), fill="#12233e")
draw.text((120, 545), "ID Number: 42101-1234567-1", font=font(58), fill="#12233e")
draw.line((120, 710, 1470, 710), fill="#ced8e6", width=2)
draw.text((120, 758), "Fictional portfolio test data.", font=font(36), fill="#53627a")
draw.text((120, 813), "Not an identity document. No issuing authority.", font=font(34), fill="#53627a")
image.save(HERE / "demo-id.png")
print(HERE / "demo-id.png")
