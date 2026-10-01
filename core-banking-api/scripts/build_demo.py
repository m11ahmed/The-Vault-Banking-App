"""Export an animated walkthrough from the verified browser screenshots."""
from pathlib import Path
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
IMAGES = ('banking-desktop.jpg', 'assistant-desktop.jpg', 'kyc-desktop.jpg', 'reviewer-desktop.jpg')


def main():
    frames = []
    for filename in IMAGES:
        with Image.open(ROOT / 'artifacts' / filename) as image:
            # A fixed view of each screenshot's top shows its real app heading.
            frame = image.convert('RGB').crop((0, 0, image.width, min(image.height, 900)))
            frame = ImageOps.pad(frame, (960, 675), color='#f6f7f3', method=Image.Resampling.LANCZOS, centering=(0, 0))
            frames.append(frame)
    output = ROOT / 'artifacts' / 'demo-walkthrough.gif'
    frames[0].save(output, save_all=True, append_images=frames[1:], duration=3500, loop=0, optimize=True)
    print(f'Created animated screenshot walkthrough: {output.name} ({output.stat().st_size:,} bytes)')


if __name__ == '__main__':
    main()
