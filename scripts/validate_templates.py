#!/usr/bin/env python3
"""
Validate Jinja2 templates for syntax errors.
Run this before deploying to catch template issues early.

Usage:
    python scripts/validate_templates.py
"""

import sys
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, TemplateSyntaxError
import re

def validate_templates():
    """Validate all Jinja templates in the templates directory"""
    base_dir = Path(__file__).parent.parent
    templates_dir = base_dir / "templates"
    
    if not templates_dir.exists():
        print(f"Error: Templates directory not found: {templates_dir}")
        return False
    
    env = Environment(loader=FileSystemLoader(str(templates_dir)))
    errors = []

    # Get all HTML template files
    template_files = list(templates_dir.glob("*.html"))
    
    if not template_files:
        print("No template files found.")
        return False
    
    print(f"Validating {len(template_files)} template(s)...\n")

    # Guardrail: disallow Python built-ins inside Jinja templates
    builtin_functions = ["str", "int", "len", "dict", "list", "bool", "float", "type", "getattr"]

    for template_file in sorted(template_files):
        source = template_file.read_text(encoding="utf-8")
        for name in builtin_functions:
            # \b ensures we match the function name, not substrings like parseInt
            pattern = r"\b" + re.escape(name) + r"\s*\("
            if re.search(pattern, source):
                msg = f"Python builtin '{name}()' used in template {template_file.name}. Move this logic into Python or use Jinja filters."
                print(f"ERROR {template_file.name}")
                print(f"  {msg}")
                errors.append((template_file.name, msg))

    # Mock context for templates that need variables
    class MockRequest:
        def __init__(self):
            self.base_url = "https://example.com"
            self.url = type('obj', (object,), {'path': '/'})()
    
    mock_context = {
        "request": MockRequest(),
        "user": None,
    }

    for template_file in sorted(template_files):
        # Skip further checks if we already recorded an error for this file
        if any(t[0] == template_file.name for t in errors):
            continue
        try:
            # Try to load and parse the template
            template = env.get_template(template_file.name)
            # Force compilation to catch syntax errors
            # Use mock context to avoid undefined variable errors
            try:
                template.render(**mock_context)
            except Exception:
                # Missing variables are OK - we just want syntax validation
                pass
            print(f"OK {template_file.name}")
        except TemplateSyntaxError as e:
            print(f"ERROR {template_file.name}")
            print(f"  Error: {e.message}")
            print(f"  Line {e.lineno}: {e}")
            errors.append((template_file.name, e))
        except Exception as e:
            # Check if it's a syntax-related error
            error_str = str(e).lower()
            if any(
                keyword in error_str
                for keyword in ["unexpected end", "missing endif", "missing endfor", "missing endblock", "syntax"]
            ):
                print(f"ERROR {template_file.name}")
                print(f"  Error: {e}")
                errors.append((template_file.name, e))
            else:
                # Other errors (like missing variables) are OK for syntax validation
                print(f"OK {template_file.name} (has undefined variables - OK)")
    
    print()
    if errors:
        print(f"ERROR: Found {len(errors)} template error(s):")
        for filename, error in errors:
            print(f"  - {filename}: {error}")
        return False
    else:
        print("SUCCESS: All templates are syntactically valid!")
        return True

if __name__ == "__main__":
    success = validate_templates()
    sys.exit(0 if success else 1)

