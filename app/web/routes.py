from fastapi import APIRouter, Request, Depends, Form, Query, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import requests
import os

from datetime import date, datetime, timezone, timedelta
from urllib.parse import unquote, quote
from sqlalchemy.orm import Session
from sqlalchemy import func, distinct, or_

from app.auth.models import User
from app.auth.category_level import (
    get_user_category_level, get_all_user_category_levels_as_list,
    sync_user_category_level, get_or_create_progress, is_fast_track,
)
from app.core.deps import get_current_user, get_admin, get_main_admin
from app.core.config import MAIN_ADMIN_USER_ID
from app.db.session import get_db, SessionLocal
from app.challenges.models import Challenge
from app.submissions.models import Submission, SubmissionInsight


def _is_production() -> bool:
    """
    Detect if we're running in production (Railway, Heroku, etc).
    Railway sets RAILWAY_ENVIRONMENT, other platforms set other vars.
    """
    return bool(
        os.getenv("RAILWAY_ENVIRONMENT") or
        os.getenv("RAILWAY_PROJECT_ID") or
        os.getenv("ENVIRONMENT", "").lower() == "production" or
        os.getenv("HEROKU_APP_NAME")
    )


def _api_base(request: Request) -> str:
    """
    Base URL for internal API calls.

    - Prefer explicit override via API_BASE (works on Railway / behind proxy).
    - Otherwise, fall back to localhost:8080 which matches the uvicorn server.
    - We intentionally do NOT rely on public domains or request.base_url here.
    """
    base = os.getenv("API_BASE", "").strip()
    if base:
        return base.rstrip("/")
    # Default: same container / local dev
    return "http://127.0.0.1:8080"


templates = Jinja2Templates(directory="templates")

# Inject PUBLIC_BASE_URL globally into all templates for Open Graph previews
# This ensures og:image and og:url use the public domain, not internal Railway URLs
def get_public_base_url() -> str:
    """Get public base URL from env var, or return empty string to use request.base_url"""
    public_url = os.getenv("PUBLIC_BASE_URL", "").strip()
    if public_url:
        # Ensure it ends with / for consistency
        return public_url.rstrip("/")
    return ""

# Add to template globals so it's available in all templates
templates.env.globals["public_base_url"] = get_public_base_url()

router = APIRouter(tags=["web"])


def _extract_error(r: requests.Response, fallback: str) -> str:
    """Try to show a useful backend error message on the HTML form."""
    try:
        j = r.json()
        if isinstance(j, dict):
            return j.get("detail") or j.get("message") or fallback
    except Exception:
        pass
    return fallback


# ======================================================
# SIGNUP
# ======================================================
@router.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request):
    return templates.TemplateResponse("signup.html", {"request": request, "error": None})


@router.post("/signup")
def signup_submit(
    request: Request,
    email: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
):
    try:
        r = requests.post(
            # Call the backend auth API, not the HTML route
            f"{_api_base(request)}/auth/signup",
            data={"email": email, "username": username, "password": password},
            allow_redirects=False,  # We expect a direct JSON response
            timeout=10,
        )
    except Exception as exc:
        # Network / connection level failure
        print("[WEB] /signup -> /auth/signup network error:", repr(exc), flush=True)
        return templates.TemplateResponse(
            "signup.html",
            {
                "request": request,
                "error": "Signup temporarily unavailable. Please try again shortly.",
            },
        )

    content_type = r.headers.get("content-type", "")

    if not (200 <= r.status_code < 300):
        # Detailed debug logging on failure
        print("[WEB] /signup -> /auth/signup FAILED status:", r.status_code, flush=True)
        print("[WEB] /auth/signup content-type:", content_type, flush=True)
        try:
            print("[WEB] /auth/signup body:", r.text[:200], flush=True)
        except Exception:
            pass

        detail = _extract_error(r, "Signup failed")
        return templates.TemplateResponse(
            "signup.html", {"request": request, "error": detail}
        )

    # Verify we really got JSON
    if "application/json" not in content_type.lower():
        print(
            "[WEB] /auth/signup unexpected Content-Type on success:",
            content_type,
            flush=True,
        )
        return templates.TemplateResponse(
            "signup.html",
            {
                "request": request,
                "error": "Signup failed: unexpected response from auth service.",
            },
        )

    # Happy path: redirect to login page
    return RedirectResponse(url="/login", status_code=303)


# ======================================================
# LOGIN
# ======================================================
@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login")
def login_submit(
    request: Request,
    email_or_username: str = Form(...),
    password: str = Form(...),
):
    try:
        r = requests.post(
            # Call the backend auth API, not the HTML route
            f"{_api_base(request)}/auth/login",
            data={"email_or_username": email_or_username, "password": password},
            allow_redirects=False,  # We expect a direct JSON response
            timeout=10,
        )
    except Exception as exc:
        # Network / connection level failure
        print("[WEB] /login -> /auth/login network error:", repr(exc), flush=True)
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "Login temporarily unavailable. Please try again shortly.",
            },
        )

    content_type = r.headers.get("content-type", "")

    # On failure, dump debug info
    if not (200 <= r.status_code < 300):
        print("[WEB] /login -> /auth/login FAILED status:", r.status_code, flush=True)
        print("[WEB] /auth/login content-type:", content_type, flush=True)
        try:
            print("[WEB] /auth/login body:", r.text[:200], flush=True)
        except Exception:
            pass
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": _extract_error(r, "Invalid credentials")},
        )

    # Ensure we really got JSON back
    if "application/json" not in content_type.lower():
        print(
            "[WEB] /auth/login unexpected Content-Type on success:",
            content_type,
            flush=True,
        )
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "Login failed: unexpected response from auth service.",
            },
        )

    try:
        j = r.json()
        # Support multiple possible token keys
        token = (
            j.get("access_token")
            or j.get("accessToken")
            or j.get("token")
        )
    except Exception as exc:
        print("[WEB] /auth/login JSON decode error:", repr(exc), flush=True)
        token = None

    if not token:
        print("[WEB] /auth/login missing access token in JSON:", j if "j" in locals() else None, flush=True)
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "Login failed: no access token returned from auth service.",
            },
        )

    response = RedirectResponse(url="/dashboard", status_code=303)

    # Cookie security: httpOnly always; secure in production; samesite lax
    secure_cookie = _is_production()
    print(f"[AUTH DEBUG] Setting access_token cookie: secure={secure_cookie} (is_production={_is_production()})", flush=True)

    # ✅ Most FastAPI auth deps expect "Bearer <token>"
    response.set_cookie(
        key="access_token",
        value=f"Bearer {token}",
        httponly=True,
        secure=secure_cookie,
        samesite="lax",
        path="/",
    )
    return response


# ======================================================
# DASHBOARD
# ======================================================
@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    error: str = Query(None),
    level: int = Query(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    error_message = None
    if error == "no_challenge" and level:
        error_message = (
            f"No challenges available at level {level}. Please create challenges for this level first."
        )
    elif error == "no_more_challenges" and level:
        solved = request.query_params.get("solved", "0")
        required = request.query_params.get("required", "0")
        error_message = (
            f"All level {level} challenges completed! You've solved {solved}/{required} required challenges. "
            f"More challenges need to be added for level {level}."
        )
    elif error == "all_solved" and level:
        solved = request.query_params.get("solved", "0")
        required = request.query_params.get("required", "0")
        error_message = (
            f"Congratulations! You've solved {solved}/{required} level {level} challenges. "
            "You should level up soon!"
        )

    from app.auth.category_level import build_ui_progress_context
    ui_ctx = build_ui_progress_context(db, user.id)
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "username": user.username,
            "category_levels": ui_ctx["category_levels"],
            "next_goal": ui_ctx["next_goal"],
            "streak": user.streak,
            "verified": user.is_verified,
            "user": user,
            "error_message": error_message,
        },
    )


# ======================================================
# DAILY CHALLENGE (SHOW PAGE)
# ======================================================
@router.get("/challenge", response_class=HTMLResponse)
def daily_challenge(
    request: Request,
    edit: int = 0,
    error_message: str = Query(None),
    challenge_id: int = Query(None),
    main_category: str = Query(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Get list of ALL main categories that have challenges
    # First, let's check ALL challenges to see what categories exist
    all_categories_raw = (
        db.query(Challenge.main_category)
        .filter(Challenge.main_category.isnot(None))
        .distinct()
        .all()
    )
    print(f"[WEB DEBUG] Raw categories from DB (all challenges): {[c[0] for c in all_categories_raw if c[0]]}", flush=True)
    
    # Check pool challenges specifically
    pool_categories = (
        db.query(distinct(Challenge.main_category))
        .filter(
            Challenge.main_category.isnot(None),
            Challenge.challenge_date.is_(None),  # Only pool challenges
            or_(Challenge.is_active.is_(True), Challenge.is_active.is_(None)),
        )
        .order_by(Challenge.main_category)
        .all()
    )
    print(f"[WEB DEBUG] Pool challenge categories: {[c[0] for c in pool_categories if c[0]]}", flush=True)
    
    # Also check if there are categories in daily challenges
    daily_categories = (
        db.query(distinct(Challenge.main_category))
        .filter(
            Challenge.main_category.isnot(None),
            Challenge.challenge_date.isnot(None),  # Daily challenges
            or_(Challenge.is_active.is_(True), Challenge.is_active.is_(None)),
        )
        .all()
    )
    print(f"[WEB DEBUG] Daily challenge categories: {[c[0] for c in daily_categories if c[0]]}", flush=True)
    
    # For now, let's show ALL categories regardless of challenge_date
    # This ensures categories show up even if they're in daily challenges
    main_categories = (
        db.query(distinct(Challenge.main_category))
        .filter(
            Challenge.main_category.isnot(None),
            Challenge.main_category != "",
            or_(Challenge.is_active.is_(True), Challenge.is_active.is_(None)),
        )
        .order_by(Challenge.main_category)
        .all()
    )
    main_categories = [cat[0] for cat in main_categories if cat[0] and cat[0].strip()]
    print(f"[WEB DEBUG] Final categories list: {main_categories}", flush=True)
    
    challenge = None
    no_questions_message = ""
    selection_reason = None
    ui_ctx = None
    
    # Only show challenge if challenge_id is provided OR main_category is selected
    # Don't show challenge by default - user must select a category first
    if challenge_id:
        # If challenge_id is provided, first check if it's already solved
        # If solved, redirect to get a new unsolved challenge from the same category
        already_solved_check = (
            db.query(Submission)
            .filter(
                Submission.user_id == user.id,
                Submission.challenge_id == challenge_id,
                Submission.is_correct == 1,
            )
            .first()
        )
        
        if already_solved_check and not edit:
            # Challenge already solved - get a new one from the same category
            print(f"[CHALLENGE] Challenge {challenge_id} already solved, fetching new one", flush=True)
            challenge_r = requests.get(
                f"{_api_base(request)}/challenge/{challenge_id}", cookies=request.cookies
            )
            if challenge_r.status_code == 200:
                temp_challenge = challenge_r.json()
                temp_category = temp_challenge.get('main_category')
                if temp_category:
                    # Get next unsolved challenge from same category
                    from app.auth.category_level import get_next_challenge_for_category
                    next_selection = get_next_challenge_for_category(db, user.id, temp_category)
                    next_challenge_id = next_selection.get("challenge_id")
                    if next_challenge_id:
                        # Redirect to new challenge
                        return RedirectResponse(
                            url=f"/challenge?challenge_id={next_challenge_id}",
                            status_code=303
                        )
                    else:
                        # No more challenges - show selection page
                        return RedirectResponse(
                            url=f"/challenge?main_category={temp_category}",
                            status_code=303
                        )
        
        # Challenge not solved or in edit mode - load it
        challenge_r = requests.get(
            f"{_api_base(request)}/challenge/{challenge_id}", cookies=request.cookies
        )
        if challenge_r.status_code == 200:
            challenge = challenge_r.json()
    elif main_category:
        # Category selected — use new strict-level selection
        from app.auth.category_level import get_next_challenge_for_category
        category_normalized = main_category.strip()
        selection = get_next_challenge_for_category(db, user.id, category_normalized)
        challenge_id_from_category = selection.get("challenge_id")
        no_questions_message = selection.get("message", "")
        selection_reason = selection.get("reason")

        if challenge_id_from_category:
            challenge_id = challenge_id_from_category
            challenge_r = requests.get(
                f"{_api_base(request)}/challenge/{challenge_id}", cookies=request.cookies
            )
            if challenge_r.status_code == 200:
                challenge = challenge_r.json()
        print(f"[WEB] category='{category_normalized}' selection={selection.get('reason')} cid={challenge_id_from_category}", flush=True)

    today_completed = False
    previous_code = None
    challenge_already_solved = False
    progress_info = None

    # Daily cap reached for this category?
    if selection_reason == "DAILY_CAP_REACHED":
        today_completed = True

    # Check if this is a pool challenge (Learn More) or daily challenge
    is_pool_challenge = challenge_id is not None

    if challenge:
        # Only check today_completed if it's today's challenge (not force-learning/pool challenge or latest attempted)
        if not is_pool_challenge:
            check = requests.get(
                f"{_api_base(request)}/submission/check-today", cookies=request.cookies
            )
            if check.status_code == 200:
                today_completed = check.json().get("completed", False)

        # For pool challenges (or latest attempted challenge), check if this specific challenge is already solved and calculate progress
        if is_pool_challenge:
            # Check if user has solved this specific challenge correctly
            # Only set this flag in edit/improve mode - normal flow should never show solved challenges
            if edit:
                solved = (
                    db.query(Submission)
                    .filter(
                        Submission.user_id == user.id,
                        Submission.challenge_id == challenge_id,
                        Submission.is_correct == 1,
                    )
                    .first()
                )
                challenge_already_solved = solved is not None
            else:
                challenge_already_solved = False

            # Build UI progress context (F1/F5/F6)
            from app.auth.category_level import build_ui_progress_context
            _cat = (challenge.get("main_category") or main_category or "").strip() or None
            ui_ctx = build_ui_progress_context(db, user.id, _cat)
            _cur = ui_ctx.get("current")
            if _cur:
                progress_info = {"solved": _cur["solved"], "required": _cur["required"], "level": _cur["level"]}
            else:
                progress_info = None

            # If in edit mode, get the latest correct submission for this challenge to pre-fill
            if edit:
                latest_correct = (
                    db.query(Submission)
                    .filter(
                        Submission.user_id == user.id,
                        Submission.challenge_id == challenge_id,
                        Submission.is_correct == 1,
                    )
                    .order_by(Submission.created_at.desc())
                    .first()
                )
                if latest_correct:
                    previous_code = latest_correct.code

        if edit and not is_pool_challenge and today_completed:
            latest = requests.get(f"{_api_base(request)}/submission/latest", cookies=request.cookies)
            if latest.status_code == 200:
                submission_id = latest.json().get("submission_id")
                if submission_id:
                    sub = requests.get(
                        f"{_api_base(request)}/submission/{submission_id}?allow_incorrect=true",
                        cookies=request.cookies,
                    )
                    if sub.status_code == 200:
                        previous_code = sub.json().get("code")

    # Determine if this is a category-selected challenge
    is_category_challenge = main_category is not None and challenge_id is None
    
    # Ensure ui_ctx exists even when no challenge was loaded
    if not ui_ctx:
        from app.auth.category_level import build_ui_progress_context
        _cat = (main_category or "").strip() or None
        ui_ctx = build_ui_progress_context(db, user.id, _cat)

    return templates.TemplateResponse(
        "challenge.html",
        {
            "request": request,
            "challenge": challenge,
            "today_completed": today_completed and not edit,
            "challenge_already_solved": challenge_already_solved if (is_pool_challenge and edit) else False,
            "is_pool_challenge": is_pool_challenge or is_category_challenge,
            "progress_info": progress_info,
            "ui_ctx": ui_ctx,
            "previous_code": previous_code,
            "edit_mode": bool(edit),
            "error_message": error_message,
            "no_questions_message": no_questions_message,
            "user": user,
            "main_categories": main_categories,
            "selected_category": main_category,
        },
    )


# ======================================================
# DAILY CHALLENGE (SUBMIT FROM UI)
# ======================================================
@router.post("/challenge/submit-ui")
def submit_challenge_ui(
    request: Request,
    code: str = Form(...),
    challenge_id: int = Form(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # If challenge_id is provided, this is a force-learning challenge
    if challenge_id:
        r = requests.post(
            f"{_api_base(request)}/challenge/submit-force",
            data={"challenge_id": challenge_id, "code": code},
            cookies=request.cookies,
        )
        print(f"[WEB DEBUG] submit-force response status: {r.status_code}", flush=True)
        try:
            resp_body = r.json()
            print(f"[WEB DEBUG] submit-force response body keys: {list(resp_body.keys()) if isinstance(resp_body, dict) else 'not dict'}", flush=True)
            if isinstance(resp_body, dict) and 'correct' in resp_body:
                print(f"[WEB DEBUG] submit-force correct={resp_body.get('correct')}, submission_id={resp_body.get('submission_id')}", flush=True)
        except Exception:
            resp_body = None
            print(f"[WEB DEBUG] submit-force response not JSON: {r.text[:200]}", flush=True)

        if r.status_code != 200:
            print(f"[WEB DEBUG] submit-force FAILED with status {r.status_code}: {r.text[:300]}", flush=True)
            # Try to parse the response — it might still have correct=true (edge case)
            if resp_body and isinstance(resp_body, dict) and resp_body.get("correct"):
                # API returned non-200 but answer is correct — treat as success
                submission_id = resp_body.get("submission_id")
                if submission_id:
                    return RedirectResponse(url=f"/submission/{submission_id}/view?fresh=true", status_code=303)
            
            challenge_r = requests.get(
                f"{_api_base(request)}/challenge/{challenge_id}", cookies=request.cookies
            )
            challenge = challenge_r.json() if challenge_r.status_code == 200 else None
            main_cats = [
                row[0] for row in db.query(distinct(Challenge.main_category))
                .filter(
                    Challenge.main_category.isnot(None), Challenge.main_category != "",
                    or_(Challenge.is_active.is_(True), Challenge.is_active.is_(None)),
                )
                .order_by(Challenge.main_category)
                .all()
                if row[0] and str(row[0]).strip()
            ]
            error_detail = ""
            if resp_body and isinstance(resp_body, dict):
                error_detail = resp_body.get("detail", "")
            error_msg = error_detail if error_detail else (
                "Error submitting challenge. Please try again." if not challenge 
                else "Your answer is incorrect. Please try again!"
            )
            _cat_e = challenge.get("main_category") if challenge else None
            from app.auth.category_level import build_ui_progress_context
            _ui_ctx_e = build_ui_progress_context(db, user.id, _cat_e)
            return templates.TemplateResponse(
                "challenge.html",
                {
                    "request": request,
                    "challenge": challenge,
                    "today_completed": False,
                    "previous_code": code,
                    "edit_mode": True,
                    "error_message": error_msg,
                    "user": user,
                    "ui_ctx": _ui_ctx_e,
                    "main_categories": main_cats,
                    "selected_category": _cat_e,
                },
            )

        result = resp_body if resp_body else r.json()
        submission_id = result.get("submission_id")
        is_correct = result.get("correct", False)
        level_up = result.get("level_up", False)
        new_level = result.get("current_level", user.level)
        old_level = result.get("old_level", new_level - 1 if level_up else new_level)
        mentor_hint = result.get("mentor_hint")  # Extract mentor hint from API response

        # Debug: Log mentor hint for troubleshooting
        if mentor_hint:
            print(f"[WEB ROUTE DEBUG] Mentor hint received: {mentor_hint}", flush=True)
        else:
            print(f"[WEB ROUTE DEBUG] No mentor hint in response", flush=True)

        if is_correct:
            if level_up:
                _cat_param = ""
                # Get category from challenge for the toast
                try:
                    _ch = requests.get(f"{_api_base(request)}/challenge/{challenge_id}", cookies=request.cookies)
                    if _ch.status_code == 200:
                        _cat_param = f"&category={_ch.json().get('main_category', '')}"
                except Exception:
                    pass
                return RedirectResponse(
                    url=f"/submission/{submission_id}/view?fresh=true&level_up=true&new_level={new_level}&old_level={old_level}{_cat_param}",
                    status_code=303,
                )
            
            # Correct but NOT level up - get next challenge from same category/level
            try:
                # Get the current challenge to know its category
                _ch_r = requests.get(f"{_api_base(request)}/challenge/{challenge_id}", cookies=request.cookies)
                if _ch_r.status_code == 200:
                    current_challenge = _ch_r.json()
                    challenge_category = current_challenge.get('main_category')
                    
                    if challenge_category:
                        # Get next unsolved challenge from same category
                        from app.auth.category_level import get_next_challenge_for_category
                        next_selection = get_next_challenge_for_category(db, user.id, challenge_category)
                        next_challenge_id = next_selection.get("challenge_id")
                        
                        if next_challenge_id:
                            # Found next challenge - redirect to it with correct flag
                            print(f"[SUBMIT] Correct answer, loading next challenge {next_challenge_id} from same category", flush=True)
                            return RedirectResponse(
                                url=f"/challenge?challenge_id={next_challenge_id}&correct=1&prev_submission={submission_id}",
                                status_code=303
                            )
                        else:
                            # No more challenges available at this level
                            reason = next_selection.get("reason", "")
                            if reason == "DAILY_CAP_REACHED":
                                # Daily limit reached - show message
                                message = "✅ Correct! You've completed today's challenges. Come back tomorrow or enable Fast Track!"
                            elif reason == "ALL_SOLVED_AT_LEVEL":
                                # All challenges solved at this level
                                message = "✅ Correct! You've solved all available questions at this level. Wait for more to be added or try another category!"
                            else:
                                # No questions at level
                                message = "✅ Correct! Wait for Admin/Owner to add more questions at this level."
                            
                            print(f"[SUBMIT] No next challenge available: {reason}", flush=True)
                            # Show success message on current challenge page
                            from app.auth.category_level import build_ui_progress_context
                            _ui_ctx_success = build_ui_progress_context(db, user.id, challenge_category)
                            return templates.TemplateResponse(
                                "challenge.html",
                                {
                                    "request": request,
                                    "challenge": current_challenge,
                                    "user": user,
                                    "success_message": message,
                                    "today_completed": reason == "DAILY_CAP_REACHED",
                                    "selected_category": challenge_category,
                                    "ui_ctx": _ui_ctx_success,
                                    "main_categories": [
                                        row[0] for row in db.query(distinct(Challenge.main_category))
                                        .filter(
                                            Challenge.main_category.isnot(None), Challenge.main_category != "",
                                            or_(Challenge.is_active.is_(True), Challenge.is_active.is_(None)),
                                        )
                                        .order_by(Challenge.main_category)
                                        .all()
                                        if row[0] and str(row[0]).strip()
                                    ],
                                },
                            )
            except Exception as e:
                print(f"[SUBMIT ERROR] Failed to get next challenge: {e}", flush=True)
                # Fallback to old behavior
                return RedirectResponse(url=f"/submission/{submission_id}/view?fresh=true", status_code=303)
            
            # Fallback if no category
            return RedirectResponse(url=f"/submission/{submission_id}/view?fresh=true", status_code=303)
        else:
            challenge_r = requests.get(
                f"{_api_base(request)}/challenge/{challenge_id}", cookies=request.cookies
            )
            challenge = challenge_r.json() if challenge_r.status_code == 200 else None
            main_cats = [
                row[0] for row in db.query(distinct(Challenge.main_category))
                .filter(
                    Challenge.main_category.isnot(None), Challenge.main_category != "",
                    or_(Challenge.is_active.is_(True), Challenge.is_active.is_(None)),
                )
                .order_by(Challenge.main_category)
                .all()
                if row[0] and str(row[0]).strip()
            ]
            # Build ui_ctx for the re-rendered page
            _cat = challenge.get("main_category") if challenge else None
            from app.auth.category_level import build_ui_progress_context
            _ui_ctx = build_ui_progress_context(db, user.id, _cat)
            return templates.TemplateResponse(
                "challenge.html",
                {
                    "request": request,
                    "challenge": challenge,
                    "today_completed": False,
                    "previous_code": code,
                    "edit_mode": True,
                    "error_message": "Your answer is incorrect. Please try again!",
                    "expected_output": result.get("expected_output", ""),
                    "actual_output": result.get("actual_output", ""),
                    "ai_hint": result.get("ai_hint"),
                    "ai_hint_is_ai": result.get("ai_hint_is_ai", False),
                    "mentor_hint": mentor_hint,
                    "user": user,
                    "ui_ctx": _ui_ctx,
                    "main_categories": main_cats,
                    "selected_category": _cat,
                },
            )
    else:
        # Regular daily challenge submission
        r = requests.post(
            f"{_api_base(request)}/challenge/submit",
            data={"code": code},
            cookies=request.cookies,
        )
        if r.status_code != 200:
            return RedirectResponse(url="/challenge", status_code=303)

        result = r.json()
        submission_id = result.get("submission_id")
        is_correct = result.get("correct", False)
        level_up = result.get("level_up", False)
        new_level = result.get("new_level", user.level)
        old_level = result.get("old_level", user.level - 1 if level_up else user.level)
        mentor_hint = result.get("mentor_hint")  # Extract mentor hint from API response

        # Debug: Log mentor hint for troubleshooting
        if mentor_hint:
            print(f"[WEB ROUTE DEBUG] Mentor hint received (daily): {mentor_hint}", flush=True)
        else:
            print(f"[WEB ROUTE DEBUG] No mentor hint in response (daily)", flush=True)

        if is_correct:  # If the answer is correct, go to progress page
            if level_up:
                _cat_q = f"&category={result.get('category', '')}" if result.get('category') else ""
                return RedirectResponse(
                    url=f"/progress/{submission_id}?fresh=true&level_up=true&new_level={new_level}&old_level={old_level}{_cat_q}",
                    status_code=303,
                )
            return RedirectResponse(url=f"/progress/{submission_id}?fresh=true", status_code=303)
        else:
            challenge_r = requests.get(f"{_api_base(request)}/challenge/today", cookies=request.cookies)
            challenge = challenge_r.json() if challenge_r.status_code == 200 else None

            check_r = requests.get(
                f"{_api_base(request)}/submission/check-today", cookies=request.cookies
            )
            today_completed = False
            if check_r.status_code == 200:
                today_completed = check_r.json().get("completed", False)

            previous_code = code

            # Get categories for the template
            # Note: distinct and or_ are already imported at top of file
            main_categories = (
                db.query(distinct(Challenge.main_category))
                .filter(
                    Challenge.main_category.isnot(None),
                    Challenge.main_category != "",
                    Challenge.challenge_date.is_(None),
                    or_(Challenge.is_active.is_(True), Challenge.is_active.is_(None)),
                )
                .order_by(Challenge.main_category)
                .all()
            )
            main_categories = [cat[0] for cat in main_categories if cat[0]]
            
            # Build ui_ctx for wrong answer page
            from app.auth.category_level import build_ui_progress_context
            _cat_d = challenge.get("main_category") if challenge else None
            _ui_ctx_d = build_ui_progress_context(db, user.id, _cat_d)
            return templates.TemplateResponse(
                "challenge.html",
                {
                    "request": request,
                    "challenge": challenge,
                    "today_completed": today_completed,
                    "previous_code": previous_code,
                    "edit_mode": True,
                    "error_message": "Your answer is incorrect. Please try again!",
                    "expected_output": result.get("expected_output", ""),
                    "actual_output": result.get("actual_output", ""),
                    "ai_hint": result.get("ai_hint"),
                    "ai_hint_is_ai": result.get("ai_hint_is_ai", False),
                    "mentor_hint": mentor_hint,
                    "user": user,
                    "ui_ctx": _ui_ctx_d,
                    "main_categories": main_categories,
                    "selected_category": _cat_d,
                },
            )


# ======================================================
# JOURNEY PAGE (LATEST SUBMISSION PROGRESS)
# ======================================================
@router.get("/journey", response_class=HTMLResponse)
def journey_page(
    request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Show journey page with tree view by default, or detail if submission_id provided."""
    submission_id = request.query_params.get("submission_id")

    if submission_id:
        r = requests.get(f"{_api_base(request)}/submission/{submission_id}", cookies=request.cookies)
        if r.status_code == 200:
            submission = r.json()
            return templates.TemplateResponse(
                "progress.html", {"request": request, "submission": submission}
            )

    main_categories = (
        db.query(Challenge.main_category)
        .join(Submission, Submission.challenge_id == Challenge.id)
        .filter(
            Submission.user_id == user.id,
            Challenge.main_category.isnot(None),
            Challenge.main_category != "",
        )
        .distinct()
        .all()
    )
    main_categories = [cat[0] for cat in main_categories if cat[0]]

    return templates.TemplateResponse(
        "journey_layout.html",
        {"request": request, "submission": None, "user": user, "main_categories": main_categories},
    )


# ======================================================
# PROGRESS PAGE
# ======================================================
@router.get("/progress/", response_class=HTMLResponse)
def progress_redirect(request: Request, user: User = Depends(get_current_user)):
    """Redirect /progress/ to dashboard if no submission_id is provided."""
    return RedirectResponse(url="/dashboard", status_code=303)


@router.get("/progress/{submission_id}", response_class=HTMLResponse)
def submission_progress(request: Request, submission_id: int, user: User = Depends(get_current_user)):
    r = requests.get(f"{_api_base(request)}/submission/{submission_id}", cookies=request.cookies)
    if r.status_code != 200:
        return RedirectResponse(url="/dashboard", status_code=303)

    submission = r.json()

    if not submission.get("is_correct", False):
        return RedirectResponse(url="/dashboard", status_code=303)

    fresh = request.query_params.get("fresh") == "true"
    level_up = fresh and request.query_params.get("level_up") == "true"
    new_level = request.query_params.get("new_level")
    old_level = request.query_params.get("old_level")

    return templates.TemplateResponse(
        "progress.html",
        {
            "request": request,
            "submission": submission,
            "level_up": level_up,
            "new_level": int(new_level) if new_level else None,
            "old_level": int(old_level) if old_level else None,
            "fresh": fresh,
            "user": user,
        },
    )


@router.get("/submission/{submission_id}/view", response_class=HTMLResponse)
def submission_view(request: Request, submission_id: int, user: User = Depends(get_current_user)):
    r = requests.get(
        f"{_api_base(request)}/submission/{submission_id}?allow_incorrect=true", cookies=request.cookies
    )
    if r.status_code != 200:
        return RedirectResponse(url="/dashboard", status_code=303)

    submission = r.json()

    # Only show celebration overlay on fresh redirect right after submission,
    # NOT when revisiting from the journey page.
    fresh = request.query_params.get("fresh") == "true"
    level_up = fresh and request.query_params.get("level_up") == "true"
    new_level = request.query_params.get("new_level")
    old_level = request.query_params.get("old_level")

    return templates.TemplateResponse(
        "progress.html",
        {
            "request": request,
            "submission": submission,
            "level_up": level_up,
            "new_level": int(new_level) if new_level else None,
            "old_level": int(old_level) if old_level else None,
            "fresh": fresh,
            "user": user,
        },
    )


# ======================================================
# LOGOUT - CONFIRMATION PAGE
# ======================================================
@router.get("/logout", response_class=HTMLResponse)
def logout_confirm(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Show logout confirmation page.
    User must click "Yes" to actually log out.
    If not authenticated, redirect to login.
    """
    # Try to get current user, but don't fail if not authenticated
    try:
        user = get_current_user(request, db)
    except HTTPException:
        # User not authenticated, redirect to login
        return RedirectResponse(url="/login", status_code=303)
    
    return templates.TemplateResponse(
        "logout_confirm.html",
        {
            "request": request,
            "user": user,
        },
    )


# ======================================================
# LOGOUT - PERFORM LOGOUT
# ======================================================
@router.post("/logout")
def logout_perform(request: Request):
    """
    Perform the actual logout by deleting the access token cookie.
    """
    # Cookie security: must match the same settings used when setting the cookie
    secure_cookie = _is_production()
    
    response = RedirectResponse(url="/login", status_code=303)
    
    # Delete the cookie with the same settings used when setting it
    response.delete_cookie(
        key="access_token",
        path="/",
    )
    
    return response


# ======================================================
# FORCE LEARNING (POOL CHALLENGES)
# ======================================================
@router.get("/force-learning", response_class=HTMLResponse)
def force_learning_page(
    request: Request,
    main_category: str = Query(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Learn More: activates fast-track for the chosen category, then gets the next
    challenge at the user's CURRENT level (no daily cap, immediate serving).
    """
    from app.auth.category_level import enable_fast_track, get_next_challenge_for_category

    act = or_(Challenge.is_active.is_(True), Challenge.is_active.is_(None))
    all_active_cats = (
        db.query(Challenge.main_category)
        .filter(Challenge.main_category.isnot(None), Challenge.main_category != "", act)
        .distinct().all()
    )
    main_categories = [r[0].strip() for r in all_active_cats if r[0] and r[0].strip()]

    # If user chose a category → activate fast track and serve challenge
    if main_category and main_category.strip():
        cat = main_category.strip()
        enable_fast_track(db, user.id, cat)

        result = get_next_challenge_for_category(db, user.id, cat)
        cid = result.get("challenge_id")
        if cid:
            return RedirectResponse(url=f"/challenge?challenge_id={cid}", status_code=303)

        # No challenges available
        return templates.TemplateResponse(
            "force_learning_empty.html",
            {
                "request": request, "user": user,
                "main_categories": main_categories,
                "chosen_category": cat,
                "chosen_category_level": result.get("level", 1),
                "all_solved": result.get("reason") == "ALL_SOLVED_AT_LEVEL",
                "wait_message": result.get("message", ""),
            },
        )

    # No category chosen
    if not main_categories:
        return templates.TemplateResponse(
            "force_learning_empty.html",
            {"request": request, "user": user, "main_categories": [],
             "chosen_category": None, "chosen_category_level": None},
        )
    # Show category picker
    return templates.TemplateResponse(
        "force_learning_choose_category.html",
        {
            "request": request,
            "user": user,
            "main_categories": main_categories,
        },
    )


# ======================================================
# ADMIN PAGES (HTML) – create challenges & manage users
# ======================================================


@router.get("/admin/challenge/new", response_class=HTMLResponse)
def admin_new_challenge_page(
    request: Request,
    user: User = Depends(get_admin),
):
    """Show the admin challenge creation page."""
    today = date.today().isoformat()
    return templates.TemplateResponse(
        "admin_challenge.html",
        {
            "request": request,
            "user": user,
            "challenge": None,
            "today": today,
            "edit_mode": False,
            "error": None,
            "success": None,
        },
    )


@router.post("/admin/challenge/new", response_class=HTMLResponse)
def admin_create_challenge_submit(
    request: Request,
    level: int = Form(...),
    title: str = Form(...),
    description: str = Form(...),
    expected_output: str = Form(...),
    challenge_date: str = Form(None),
    main_category: str = Form(...),
    sub_category: str = Form(...),
    stage_order: int = Form(1),
    user: User = Depends(get_admin),
):
    """
    Handle admin challenge creation via the HTML form.
    Calls the internal /challenge/admin/create API endpoint.
    """
    payload = {
        "level": level,
        "title": title,
        "description": description,
        "expected_output": expected_output,
        "challenge_date": challenge_date or "",
        "main_category": main_category,
        "sub_category": sub_category,
        "stage_order": stage_order,
    }

    try:
        r = requests.post(
            f"{_api_base(request)}/challenge/admin/create",
            data=payload,
            cookies=request.cookies,
            allow_redirects=False,
            timeout=10,
        )
    except Exception as exc:
        print("[WEB] /admin/challenge/new network error:", repr(exc), flush=True)
        return templates.TemplateResponse(
            "admin_challenge.html",
            {
                "request": request,
                "user": user,
                "challenge": None,
                "today": date.today().isoformat(),
                "edit_mode": False,
                "error": "Failed to create challenge (network error). Please try again.",
                "success": None,
            },
        )

    content_type = r.headers.get("content-type", "")

    if not (200 <= r.status_code < 300):
        print("[WEB] /admin/challenge/new FAILED status:", r.status_code, flush=True)
        print("[WEB] /admin/challenge/new content-type:", content_type, flush=True)
        try:
            print("[WEB] /admin/challenge/new body:", r.text[:200], flush=True)
        except Exception:
            pass
        error = _extract_error(r, "Failed to create challenge.")
        return templates.TemplateResponse(
            "admin_challenge.html",
            {
                "request": request,
                "user": user,
                "challenge": None,
                "today": date.today().isoformat(),
                "edit_mode": False,
                "error": error,
                "success": None,
            },
        )

    if "application/json" not in content_type.lower():
        print(
            "[WEB] /admin/challenge/new unexpected Content-Type on success:",
            content_type,
            flush=True,
        )
        return templates.TemplateResponse(
            "admin_challenge.html",
            {
                "request": request,
                "user": user,
                "challenge": None,
                "today": date.today().isoformat(),
                "edit_mode": False,
                "error": "Challenge created, but response from API was unexpected.",
                "success": None,
            },
        )

    # Success
    return templates.TemplateResponse(
        "admin_challenge.html",
        {
            "request": request,
            "user": user,
            "challenge": None,
            "today": date.today().isoformat(),
            "edit_mode": False,
            "error": None,
            "success": "Challenge created successfully.",
        },
    )


@router.get("/admin/challenge/edit/{challenge_id}", response_class=HTMLResponse)
def admin_edit_challenge_page(
    request: Request,
    challenge_id: int,
    user: User = Depends(get_admin),
    db: Session = Depends(get_db),
):
    """Show the admin challenge edit page (Rewrite button from list)."""
    challenge = db.query(Challenge).filter(Challenge.id == challenge_id).first()
    if not challenge:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Challenge not found")
    today = date.today().isoformat()
    return templates.TemplateResponse(
        "admin_challenge.html",
        {
            "request": request,
            "user": user,
            "challenge": challenge,
            "today": today,
            "edit_mode": True,
            "error": None,
            "success": None,
        },
    )


@router.post("/admin/challenge/update/{challenge_id}", response_class=HTMLResponse)
def admin_update_challenge_submit(
    request: Request,
    challenge_id: int,
    level: int = Form(...),
    title: str = Form(...),
    description: str = Form(...),
    expected_output: str = Form(...),
    challenge_date: str = Form(None),
    main_category: str = Form(...),
    sub_category: str = Form(...),
    stage_order: int = Form(1),
    user: User = Depends(get_admin),
    db: Session = Depends(get_db),
):
    """Update an existing challenge (form submit from edit page)."""
    challenge = db.query(Challenge).filter(Challenge.id == challenge_id).first()
    if not challenge:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Challenge not found")
    parsed_date = None
    if challenge_date and challenge_date.strip():
        try:
            parsed_date = date.fromisoformat(challenge_date)
        except ValueError:
            pass
    challenge.level = level
    challenge.title = title
    challenge.description = description
    challenge.expected_output = expected_output
    challenge.challenge_date = parsed_date
    challenge.main_category = main_category.strip()
    challenge.sub_category = sub_category.strip()
    challenge.stage_order = stage_order
    db.add(challenge)
    db.commit()
    today = date.today().isoformat()
    return templates.TemplateResponse(
        "admin_challenge.html",
        {
            "request": request,
            "user": user,
            "challenge": challenge,
            "today": today,
            "edit_mode": True,
            "error": None,
            "success": "Challenge updated successfully.",
        },
    )


@router.get("/admin/challenges/list", response_class=HTMLResponse)
def admin_challenges_list_page(
    request: Request,
    user: User = Depends(get_admin),
    db: Session = Depends(get_db),
):
    """
    Show the admin challenges list page.

    The page uses JS to call /challenge/admin/list (API) for data.
    """
    main_categories = (
        db.query(Challenge.main_category)
        .filter(
            Challenge.main_category.isnot(None),
            Challenge.main_category != "",
        )
        .distinct()
        .all()
    )
    main_categories = [c[0] for c in main_categories if c[0]]

    return templates.TemplateResponse(
        "admin_challenges_list.html",
        {
            "request": request,
            "user": user,
            "main_categories": main_categories,
        },
    )


@router.get("/admin/challenge/delete/{challenge_id}", response_class=HTMLResponse)
def admin_delete_challenge_confirm(
    challenge_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_admin),
):
    """
    Show delete confirmation page for a challenge.
    User must click "Yes" to actually delete.
    """
    challenge = db.query(Challenge).filter(Challenge.id == challenge_id).first()
    
    if not challenge:
        # Challenge not found - redirect to list
        return RedirectResponse(url="/admin/challenges/list", status_code=303)
    
    return templates.TemplateResponse(
        "admin_challenge_delete_confirm.html",
        {
            "request": request,
            "user": user,
            "challenge": challenge,
        },
    )


@router.get("/admin/users", response_class=HTMLResponse)
def admin_users_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_main_admin),
):
    """Main admin-only user management page."""
    users = db.query(User).order_by(User.id.asc()).all()

    # A user is considered "online" if active within the last 5 minutes
    online_threshold = datetime.now(timezone.utc) - timedelta(minutes=5)

    users_data = []
    for u in users:
        cat_levels = get_all_user_category_levels_as_list(db, u.id, include_all_categories=False)
        levels_summary = ", ".join(f"{c['main_category']}: {c['level']}" for c in cat_levels[:5]) if cat_levels else "—"
        if len(cat_levels) > 5:
            levels_summary += f" (+{len(cat_levels) - 5} more)"

        # Determine online status
        is_online = False
        if u.last_active:
            last_active_utc = u.last_active
            # Ensure timezone-aware comparison
            if last_active_utc.tzinfo is None:
                last_active_utc = last_active_utc.replace(tzinfo=timezone.utc)
            is_online = last_active_utc >= online_threshold

        users_data.append({
            "id": u.id,
            "username": u.username,
            "email": u.email,
            "category_levels": levels_summary,
            "role": u.role,
            "is_main_admin": u.id == MAIN_ADMIN_USER_ID,
            "is_online": is_online,
            "last_active": u.last_active,
        })

    no_users_message = None
    if len(users_data) <= 1:
        no_users_message = "No other users found yet. Once users sign up, they will appear here."

    return templates.TemplateResponse(
        "admin_users.html",
        {
            "request": request,
            "user": user,
            "users": users_data,
            "no_users_message": no_users_message,
        },
    )


@router.post("/admin/users/{user_id}/promote")
def admin_promote_user(
    user_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_main_admin),
):
    """Promote a normal user to co-admin (main admin only)."""
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        return RedirectResponse(
            url="/admin/users?error=User+not+found", status_code=303
        )

    if target.id == MAIN_ADMIN_USER_ID:
        return RedirectResponse(
            url="/admin/users?error=Cannot+change+main+admin", status_code=303
        )

    target.role = "coadmin"
    db.commit()
    return RedirectResponse(
        url="/admin/users?success=User+promoted+to+co-admin", status_code=303
    )


@router.post("/admin/users/{user_id}/demote")
def admin_demote_user(
    user_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_main_admin),
):
    """Demote a co-admin back to normal user (main admin only)."""
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        return RedirectResponse(
            url="/admin/users?error=User+not+found", status_code=303
        )

    if target.id == MAIN_ADMIN_USER_ID:
        return RedirectResponse(
            url="/admin/users?error=Cannot+change+main+admin", status_code=303
        )

    target.role = "user"
    db.commit()
    return RedirectResponse(
        url="/admin/users?success=User+demoted+to+normal+user", status_code=303
    )


@router.get("/admin/users/{user_id}/reset", response_class=HTMLResponse)
def admin_reset_user_confirm(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_main_admin),
):
    """
    Show reset progress confirmation page for a user.
    User must click "Yes" to actually reset.
    Main admin only - can reset any user including themselves.
    """
    target = db.query(User).filter(User.id == user_id).first()
    
    if not target:
        return RedirectResponse(
            url="/admin/users?error=User+not+found", status_code=303
        )
    
    cat_levels = get_all_user_category_levels_as_list(db, target.id, include_all_categories=False)
    levels_summary = ", ".join(f"{c['main_category']}: {c['level']}" for c in cat_levels) if cat_levels else "—"
    return templates.TemplateResponse(
        "admin_user_reset_confirm.html",
        {
            "request": request,
            "user": user,
            "target_user": target,
            "category_levels_summary": levels_summary,
        },
    )


@router.post("/admin/users/{user_id}/reset")
def admin_reset_user(
    user_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_main_admin),
):
    """Reset a user's progress (submissions, per-category levels, streak); main admin only."""
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        return RedirectResponse(
            url="/admin/users?error=User+not+found", status_code=303
        )
    
    from app.submissions.models import Submission
    from app.auth.category_progress import UserCategoryProgress
    
    # Delete all submissions for this user
    db.query(Submission).filter(Submission.user_id == user_id).delete()
    
    # Delete all per-category progress and daily assignments
    db.query(UserCategoryProgress).filter(UserCategoryProgress.user_id == user_id).delete()
    from app.auth.category_progress import DailyAssignment
    db.query(DailyAssignment).filter(DailyAssignment.user_id == user_id).delete()
    
    # Reset legacy user.level and streak
    target.level = 1
    target.streak = 0
    
    db.commit()
    
    return RedirectResponse(
        url=f"/admin/users?success=User+{target.username}+progress+reset+successfully", status_code=303
    )


@router.post("/admin/users/{user_id}/delete")
def admin_delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_main_admin),
):
    """Delete a user (and their progress); main admin only."""
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        return RedirectResponse(
            url="/admin/users?error=User+not+found", status_code=303
        )

    if target.id == MAIN_ADMIN_USER_ID:
        return RedirectResponse(
            url="/admin/users?error=Cannot+delete+main+admin", status_code=303
        )

    db.delete(target)
    db.commit()
    return RedirectResponse(
        url="/admin/users?success=User+deleted+successfully", status_code=303
    )


# ======================================================
# ADMIN: AI STATUS (diagnostics)
# ======================================================
@router.get("/admin/ai-status")
def admin_ai_status(user: User = Depends(get_admin)):
    """Return OpenAI configuration status for admin debugging."""
    from app.ai.openai_client import key_present, key_fingerprint, get_last_error
    return {
        "key_present": key_present(),
        "key_fingerprint": key_fingerprint(),
        "last_error": get_last_error(),
    }
