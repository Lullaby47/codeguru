#!/usr/bin/env python3
"""
Generate share.png for Open Graph previews.
Creates a professional CodeGuru card image.
"""

from pathlib import Path
import sys

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("⚠️  PIL/Pillow not found. Install it with: pip install pillow")
    print("Alternatively, use an online tool to convert static/share.svg to PNG")
    sys.exit(1)

def generate_share_image():
    """Generate a professional CodeGuru share image"""
    base_dir = Path(__file__).parent.parent
    png_path = base_dir / "static" / "share.png"
    
    # Create image: 1200x630 (Open Graph recommended size)
    width, height = 1200, 630
    img = Image.new('RGB', (width, height), color='#1e1e1e')
    draw = ImageDraw.Draw(img)
    
    # Try to use system fonts, fallback to default
    try:
        # On Windows
        import platform
        if platform.system() == 'Windows':
            font_path = "C:/Windows/Fonts/arial.ttf"
            if Path(font_path).exists():
                title_font = ImageFont.truetype(font_path, 72)
                subtitle_font = ImageFont.truetype(font_path, 32)
                desc_font = ImageFont.truetype(font_path, 24)
                accent_font = ImageFont.truetype(font_path, 18)
            else:
                raise FileNotFoundError
        else:
            # Linux/Mac - try common paths
            title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 72)
            subtitle_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 32)
            desc_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
            accent_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
    except:
        # Fallback to default font
        title_font = ImageFont.load_default()
        subtitle_font = ImageFont.load_default()
        desc_font = ImageFont.load_default()
        accent_font = ImageFont.load_default()
    
    # Draw subtle grid pattern
    grid_color = (62, 62, 62)  # #3e3e3e
    for x in range(0, width, 40):
        draw.line([(x, 0), (x, height)], fill=grid_color, width=1)
    for y in range(0, height, 40):
        draw.line([(0, y), (width, y)], fill=grid_color, width=1)
    
    # Main content
    x_start = 100
    y_start = 150
    
    # Title: CODEGURU
    draw.text((x_start, y_start), "CODEGURU", fill='#00ff66', font=title_font)
    
    # Subtitle: Secure Developer Portal
    draw.text((x_start, y_start + 90), "Secure Developer Portal", fill='#cccccc', font=subtitle_font)
    
    # Description
    desc_text = "Official CodeGuru login and dashboard. Secure access to your account."
    draw.text((x_start, y_start + 170), desc_text, fill='#999999', font=desc_font)
    
    # Button-style element: "Open Official App"
    button_x = x_start
    button_y = y_start + 250
    button_width = 350
    button_height = 60
    # Button background
    draw.rounded_rectangle(
        [(button_x, button_y), (button_x + button_width, button_y + button_height)],
        radius=12,
        fill='#00ff66',
        outline=None
    )
    # Button text
    button_text = "Open Official App"
    bbox = draw.textbbox((0, 0), button_text, font=subtitle_font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    button_text_x = button_x + (button_width - text_width) // 2
    button_text_y = button_y + (button_height - text_height) // 2
    draw.text((button_text_x, button_text_y), button_text, fill='#1e1e1e', font=subtitle_font)
    
    # Code icon box (right side)
    box_x = 800
    box_y = 100
    box_size = 180
    # Box background with rounded corners (simulated)
    draw.rectangle(
        [(box_x, box_y), (box_x + box_size, box_y + box_size)],
        fill='#252526',
        outline='#00ff66',
        width=3
    )
    # Code icon: </>
    code_text = "</>"
    # Use a larger font for the code icon
    try:
        code_font = ImageFont.truetype(font_path, 60) if 'font_path' in locals() and Path(font_path).exists() else title_font
    except:
        code_font = title_font
    
    # Get text dimensions for centering
    bbox = draw.textbbox((0, 0), code_text, font=code_font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    text_x = box_x + (box_size - text_width) // 2
    text_y = box_y + (box_size - text_height) // 2
    draw.text((text_x, text_y), code_text, fill='#00ff66', font=code_font)
    
    # Bottom accent bar
    bar_y = height - 50
    draw.rectangle([(0, bar_y), (width, height)], fill='#00ff66')
    draw.text((100, bar_y + 25), "OFFICIAL PORTAL", fill='#1e1e1e', anchor="lm", font=accent_font)
    
    # Save
    img.save(png_path, 'PNG', quality=95)
    print(f"Generated {png_path}")
    print(f"   Size: {width}x{height}px (Open Graph recommended)")
    return True

if __name__ == "__main__":
    generate_share_image()
