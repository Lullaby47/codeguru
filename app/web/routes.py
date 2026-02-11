from fastapi import APIRouter, Request, Depends, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import requests
import os

from datetime import date
from urllib.parse import unquote
from sqlalchemy.orm import Session
from sqlalchemy import func, distinct, or_

from app.auth.models import User
from app.core.deps import get_current_user, get_admin, get_main_admin
from app.core.config import MAIN_ADMIN_USER_ID
from app.db.session import get_db, SessionLocal
from app.challenges.models import Challenge
from app.submissions.models import Submission, SubmissionInsight


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
    secure_cookie = os.getenv("ENVIRONMENT", "").lower() == "production"

    # ✅ Most FastAPI auth deps expect "Bearer <token>"
    response.set_cookie(
        key="access_token",
        value=f"Bearer {token}",
        httponly=True,
        secure=secure_cookie,
        samesite="lax",
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

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "username": user.username,
            "level": user.level,
            "streak": user.streak,
            "verified": user.is_verified,
            "user": user,  # Pass user object for profile
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
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # If challenge_id is provided (from force-learning), get that specific challenge
    if challenge_id:
        challenge_r = requests.get(
            f"{_api_base(request)}/challenge/{challenge_id}", cookies=request.cookies
        )
        if challenge_r.status_code == 200:
            challenge = challenge_r.json()
        else:
            challenge = None
    else:
        # Always get today's level-appropriate challenge (not latest submission)
        # This ensures users get a fresh challenge every 24 hours based on their level
        r = requests.get(f"{_api_base(request)}/challenge/today", cookies=request.cookies)
        if r.status_code == 200:
            challenge = r.json()
        else:
            challenge = None

    today_completed = False
    previous_code = None
    challenge_already_solved = False
    progress_info = None

    # Check if this is a pool challenge (Learn More) or daily challenge
    # If challenge_id was set from latest submission, treat it as pool challenge
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

            # Calculate progress for current level
            current_level = user.level
            solved_count = (
                db.query(func.count(distinct(Submission.challenge_id)))
                .join(Challenge, Challenge.id == Submission.challenge_id)
                .filter(
                    Submission.user_id == user.id,
                    Submission.is_correct == 1,
                    Challenge.level == current_level,
                )
                .scalar()
            ) or 0
            required_count = current_level
            progress_info = {
                "solved": solved_count,
                "required": required_count,
                "level": current_level,
            }

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

    return templates.TemplateResponse(
        "challenge.html",
        {
            "request": request,
            "challenge": challenge,
            "today_completed": today_completed and not edit and not is_pool_challenge,
            "challenge_already_solved": challenge_already_solved if is_pool_challenge else False,
            "is_pool_challenge": is_pool_challenge,
            "progress_info": progress_info,
            "previous_code": previous_code,
            "edit_mode": bool(edit),
            "error_message": error_message,
            "user": user,
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
):
    # If challenge_id is provided, this is a force-learning challenge
    if challenge_id:
        r = requests.post(
            f"{_api_base(request)}/challenge/submit-force",
            data={"challenge_id": challenge_id, "code": code},
            cookies=request.cookies,
        )
        if r.status_code != 200:
            # Get challenge data to show error
            challenge_r = requests.get(
                f"{_api_base(request)}/challenge/{challenge_id}", cookies=request.cookies
            )
            challenge = challenge_r.json() if challenge_r.status_code == 200 else None
            return templates.TemplateResponse(
                "challenge.html",
                {
                    "request": request,
                    "challenge": challenge,
                    "today_completed": False,
                    "previous_code": code,
                    "edit_mode": True,
                    "error_message": "Your answer is incorrect. Please try again!"
                    if challenge
                    else "Error submitting challenge.",
                    "user": user,
                },
            )

        result = r.json()
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
            # If user leveled up, add level up info to URL
            if level_up:
                return RedirectResponse(
                    url=f"/submission/{submission_id}/view?level_up=true&new_level={new_level}&old_level={old_level}",
                    status_code=303,
                )
            return RedirectResponse(url=f"/submission/{submission_id}/view", status_code=303)
        else:
            # Get challenge data to show error
            challenge_r = requests.get(
                f"{_api_base(request)}/challenge/{challenge_id}", cookies=request.cookies
            )
            challenge = challenge_r.json() if challenge_r.status_code == 200 else None
            return templates.TemplateResponse(
                "challenge.html",
                {
                    "request": request,
                    "challenge": challenge,
                    "today_completed": False,
                    "previous_code": code,
                    "edit_mode": True,
                    "error_message": "Your answer is incorrect. Please try again!",
                    "mentor_hint": mentor_hint,  # Pass mentor hint to template
                    "user": user,
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
                response = RedirectResponse(
                    url=f"/progress/{submission_id}?level_up=true&new_level={new_level}&old_level={old_level}",
                    status_code=303,
                )
                return response
            return RedirectResponse(url=f"/progress/{submission_id}", status_code=303)
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

            return templates.TemplateResponse(
                "challenge.html",
                {
                    "request": request,
                    "challenge": challenge,
                    "today_completed": today_completed,
                    "previous_code": previous_code,
                    "edit_mode": True,
                    "error_message": "Your answer is incorrect. Please try again!",
                    "mentor_hint": mentor_hint,
                    "user": user,
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

    level_up = request.query_params.get("level_up") == "true"
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

    level_up = request.query_params.get("level_up") == "true"
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
            "user": user,
        },
    )


# ======================================================
# LOGOUT - CONFIRMATION PAGE
# ======================================================
@router.get("/logout", response_class=HTMLResponse)
def logout_confirm(
    request: Request,
    user: User = Depends(get_current_user),
):
    """
    Show logout confirmation page.
    User must click "Yes" to actually log out.
    """
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
    secure_cookie = os.getenv("ENVIRONMENT", "").lower() == "production"
    
    response = RedirectResponse(url="/login", status_code=303)
    
    # Delete the cookie with the same settings used when setting it
    response.delete_cookie(
        key="access_token",
        httponly=True,
        secure=secure_cookie,
        samesite="lax",
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
    Learn More: user picks main category, then gets a pool challenge for their level.
    - No main_category: show category picker (categories that have pool challenges at user's level).
    - With main_category: fetch next unsolved challenge in that category; redirect to challenge or show empty.
    """
    from sqlalchemy import or_
    level = user.level
    # Pool challenges at this level (same filter as API: active or NULL is_active)
    act = or_(Challenge.is_active.is_(True), Challenge.is_active.is_(None))
    pool_at_level = (
        db.query(Challenge.main_category)
        .filter(
            Challenge.level == level,
            Challenge.challenge_date.is_(None),
            act,
            Challenge.main_category.isnot(None),
            Challenge.main_category != "",
        )
        .distinct()
        .all()
    )
    main_categories = [r[0] for r in pool_at_level if r[0] and r[0].strip()]

    # If user already chose a category, try to get next challenge in that category
    if main_category and main_category.strip():
        try:
            r = requests.get(
                f"{_api_base(request)}/challenge/next/{level}",
                params={"main_category": main_category.strip()},
                cookies=request.cookies,
                timeout=10,
            )
            if r.status_code == 200:
                result = r.json()
                challenge_id = result.get("challenge_id")
                if challenge_id:
                    return RedirectResponse(
                        url=f"/challenge?challenge_id={challenge_id}",
                        status_code=303,
                    )
        except Exception as exc:
            print(f"[WEB] /force-learning API error: {repr(exc)}", flush=True)
        # No challenge in this category - show empty with option to pick another
        return templates.TemplateResponse(
            "force_learning_empty.html",
            {
                "request": request,
                "user": user,
                "main_categories": main_categories,
                "chosen_category": main_category.strip(),
            },
        )

    # No category chosen: show category picker (or try any category and redirect)
    if not main_categories:
        return templates.TemplateResponse(
            "force_learning_empty.html",
            {
                "request": request,
                "user": user,
                "main_categories": [],
                "chosen_category": None,
            },
        )
    # Single category: auto-use it and redirect if we get a challenge
    if len(main_categories) == 1:
        try:
            r = requests.get(
                f"{_api_base(request)}/challenge/next/{level}",
                params={"main_category": main_categories[0]},
                cookies=request.cookies,
                timeout=10,
            )
            if r.status_code == 200 and r.json().get("challenge_id"):
                return RedirectResponse(
                    url=f"/challenge?challenge_id={r.json()['challenge_id']}",
                    status_code=303,
                )
        except Exception:
            pass
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

    users_data = [
        {
            "id": u.id,
            "username": u.username,
            "email": u.email,
            "level": getattr(u, "level", None),
            "role": u.role,
            "is_main_admin": u.id == MAIN_ADMIN_USER_ID,
        }
        for u in users
    ]

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
