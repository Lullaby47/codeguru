# Open Graph / Social Media Previews

This app includes Open Graph and Twitter Card meta tags for professional link previews when sharing URLs on social media platforms (WhatsApp, Telegram, Discord, Facebook, Twitter, etc.).

## Implementation

- **Meta tags**: Added to `templates/base.html` (applies to all pages)
- **Share image**: `static/share.png` (1200x630px - Open Graph recommended size)
- **Dynamic URLs**: Uses `request.base_url` to automatically generate correct URLs for any domain

## Meta Tags Included

- `og:title`: "CodeGuru — Secure Portal"
- `og:description`: "Official CodeGuru login and dashboard. Secure access to your account."
- `og:image`: Points to `/static/share.png` with full absolute URL
- `og:url`: Current page URL
- `og:type`: "website"
- `twitter:card`: "summary_large_image"
- All Twitter equivalents of Open Graph tags

## Regenerating Share Image

If you need to regenerate the share image:

```bash
python scripts/generate_share_image_simple.py
```

This will create/update `static/share.png` with:
- Dark background matching app theme
- Large "CODEGURU" title in green
- "Secure Developer Portal" subtitle
- Description text
- "Open Official App" button-style element
- Code icon (</>) in styled box

## Refreshing Cached Previews

Social media platforms cache link previews. To refresh:

### WhatsApp/Telegram
- Use their link preview debuggers (if available)
- Or wait 24-48 hours for cache to expire
- Or change the image filename and update meta tags

### Facebook
- Use [Facebook Sharing Debugger](https://developers.facebook.com/tools/debug/)
- Enter your URL and click "Scrape Again"

### Twitter/X
- Use [Twitter Card Validator](https://cards-dev.twitter.com/validator)
- Enter your URL to preview and refresh

### Discord
- Discord caches aggressively
- Change the image filename to force refresh
- Or wait for cache expiration (can take days)

### General Tips
1. **Test your preview**: Use [opengraph.xyz](https://www.opengraph.xyz/) to see how your link appears
2. **Image requirements**: 
   - Minimum: 600x315px
   - Recommended: 1200x630px
   - Max file size: 8MB (but keep it under 1MB for fast loading)
3. **HTTPS required**: All images must be served over HTTPS
4. **Absolute URLs**: Always use full absolute URLs (https://...) in meta tags

## Static Files

Static files are served via FastAPI's `StaticFiles` mount in `app/main.py`:

```python
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
```

This ensures `/static/share.png` is accessible at the correct URL.

