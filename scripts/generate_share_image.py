#!/usr/bin/env python3
"""
Generate share.png from share.svg for Open Graph previews.
Requires: cairosvg or pillow + svglib
"""

import os
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

def generate_png_from_svg():
    """Convert share.svg to share.png"""
    base_dir = Path(__file__).parent.parent
    svg_path = base_dir / "static" / "share.svg"
    png_path = base_dir / "static" / "share.png"
    
    if not svg_path.exists():
        print(f"Error: {svg_path} not found")
        return False
    
    try:
        # Try cairosvg first (better quality)
        import cairosvg
        cairosvg.svg2png(
            url=str(svg_path),
            write_to=str(png_path),
            output_width=1200,
            output_height=630
        )
        print(f"✅ Generated {png_path} using cairosvg")
        return True
    except ImportError:
        try:
            # Fallback to svglib + pillow
            from svglib.svglib import svg2rlg
            from reportlab.graphics import renderPM
            
            drawing = svg2rlg(str(svg_path))
            renderPM.drawToFile(drawing, str(png_path), fmt='PNG', dpi=72)
            print(f"✅ Generated {png_path} using svglib+pillow")
            return True
        except ImportError:
            print("⚠️  Neither cairosvg nor svglib+pillow found.")
            print("Install one of them:")
            print("  pip install cairosvg")
            print("  OR")
            print("  pip install svglib pillow reportlab")
            print("\nAlternatively, you can:")
            print("1. Open share.svg in a browser")
            print("2. Take a screenshot or use an online SVG to PNG converter")
            print("3. Save as share.png in the static/ directory")
            return False

if __name__ == "__main__":
    generate_png_from_svg()

