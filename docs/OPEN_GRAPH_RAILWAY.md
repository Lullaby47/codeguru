# Open Graph Previews on Railway

## Problem

When deployed on Railway behind a reverse proxy, `request.base_url` returns internal URLs (e.g., `http://127.0.0.1:8080`), which are not publicly accessible. This causes WhatsApp/Telegram link previews to fail because:

1. `og:image` points to an internal URL that bots cannot fetch
2. `og:url` shows the internal domain instead of the public Railway domain

## Solution

Use the `PUBLIC_BASE_URL` environment variable to override `request.base_url` for Open Graph meta tags.

## Setup

### 1. Add Railway Environment Variable

In your Railway project settings, add:

```
PUBLIC_BASE_URL=https://your-app-name.up.railway.app
```

**Important**: 
- Use `https://` (not `http://`)
- Do NOT include a trailing slash
- Use your actual Railway public domain

### 2. How It Works

- `app/web/routes.py` injects `PUBLIC_BASE_URL` globally into all Jinja templates via `templates.env.globals`
- `templates/base.html` checks if `public_base_url` is set:
  - **If set**: Uses it for `og:url` and `og:image` (public domain)
  - **If not set**: Falls back to `request.base_url` (works for local dev)

### 3. Static Files

The `/static/share.png` image is served via FastAPI's `StaticFiles` mount in `app/main.py`:

```python
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
```

This makes `/static/share.png` publicly accessible at:
- `https://your-app-name.up.railway.app/static/share.png`

**No authentication required** - static files bypass all route dependencies.

## Testing

### Local Development

1. **Without `PUBLIC_BASE_URL`**: 
   - OG tags use `request.base_url` (e.g., `http://127.0.0.1:8080`)
   - Works fine for local testing

2. **With `PUBLIC_BASE_URL` set**:
   - OG tags use the public URL
   - Useful for testing before deploying

### Production (Railway)

1. Set `PUBLIC_BASE_URL=https://your-app-name.up.railway.app` in Railway
2. Deploy
3. Test preview using:
   - [Facebook Sharing Debugger](https://developers.facebook.com/tools/debug/)
   - [opengraph.xyz](https://www.opengraph.xyz/)
   - Share URL on WhatsApp/Telegram

### Expected Result

When sharing `https://your-app-name.up.railway.app/login` on WhatsApp, you should see:

- **Title**: "CodeGuru — Secure Portal"
- **Description**: "Official CodeGuru login and dashboard. Secure access to your account."
- **Image**: Professional CodeGuru card (1200x630px)
- **URL**: Your public Railway domain

## Troubleshooting

### Preview still shows domain only

1. **Check environment variable**:
   ```bash
   # In Railway, verify PUBLIC_BASE_URL is set correctly
   echo $PUBLIC_BASE_URL
   ```

2. **Verify image is accessible**:
   - Visit `https://your-app-name.up.railway.app/static/share.png` in browser
   - Should show the image (not 404)

3. **Clear cache**:
   - WhatsApp/Telegram cache previews aggressively
   - Use Facebook Sharing Debugger to force refresh
   - Or wait 24-48 hours for cache to expire

### Image returns 404

- Check that `static/share.png` exists in your repo
- Verify `app/main.py` has `StaticFiles` mount
- Ensure Railway build includes the `static/` directory

### Still using internal URL

- Restart Railway service after setting `PUBLIC_BASE_URL`
- Check that the env var is set in the correct Railway service/environment
- Verify template is using `public_base_url` (check rendered HTML source)

## Code Reference

- **Global injection**: `app/web/routes.py` line ~35-45
- **Template usage**: `templates/base.html` line ~9-18
- **Static mount**: `app/main.py` line ~21-23

