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
    # Prefer explicit override (useful if behind proxy), otherwise use current host.
    base = os.getenv("API_BASE", "").strip()
    if base:
        return base.rstrip("/")
    return str(request.base_url).rstrip("/")


templates = Jinja2Templates(directory="templates")
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
    r = requests.post(
        # Call the backend auth API, not the HTML route
        f"{_api_base(request)}/auth/signup",
        data={"email": email, "username": username, "password": password},
    )

    print("[WEB] /signup -> /auth/signup status:", r.status_code, flush=True)
    try:
        print("[WEB] /auth/signup body:", r.text[:300], flush=True)
    except Exception:
        pass

    if not (200 <= r.status_code < 300):
        detail = "Signup failed"
        try:
            j = r.json()
            detail = j.get("detail") or j.get("message") or detail
        except Exception:
            pass
        return templates.TemplateResponse(
            "signup.html", {"request": request, "error": detail}
        )

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
    r = requests.post(
        # Call the backend auth API, not the HTML route
        f"{_api_base(request)}/auth/login",
        data={"email_or_username": email_or_username, "password": password},
    )

    # ✅ Accept any 2xx
    if not (200 <= r.status_code < 300):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": _extract_error(r, "Invalid credentials")},
        )

    try:
        token = r.json().get("access_token")
    except Exception:
        token = None

    if not token:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Login failed (no access token returned)"},
        )

    response = RedirectResponse(url="/dashboard", status_code=303)

    # ✅ Most FastAPI auth deps expect "Bearer <token>"
    response.set_cookie(
        key="access_token",
        value=f"Bearer {token}",
        httponly=True,
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
        # Get the latest challenge the user has attempted (most recent submission)
        latest_submission = (
            db.query(Submission)
            .filter(Submission.user_id == user.id)
            .order_by(Submission.created_at.desc())
            .first()
        )

        if latest_submission:
            # Get the challenge from the latest submission
            challenge_r = requests.get(
                f"{_api_base(request)}/challenge/{latest_submission.challenge_id}",
                cookies=request.cookies,
            )
            if challenge_r.status_code == 200:
                challenge = challenge_r.json()
                challenge_id = (
                    latest_submission.challenge_id
                )  # Set challenge_id for consistency
            else:
                challenge = None
        else:
            # If user has no submissions yet, get today's challenge
            r = requests.get(f"{_api_base(request)}/challenge/today", cookies=request.cookies)
            challenge = r.json() if r.status_code == 200 else None

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
# LOGOUT
# ======================================================
@router.get("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("access_token")
    return response
