from fastapi import APIRouter, Request, Depends, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import requests

import os

def _api_base(request: Request) -> str:
    # Prefer explicit override (useful if behind proxy), otherwise use current host.
    base = os.getenv("API_BASE", "").strip()
    if base:
        return base.rstrip("/")
    return str(request.base_url).rstrip("/")

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

templates = Jinja2Templates(directory="templates")
router = APIRouter(tags=["web"])



# ======================================================
# SIGNUP
# ======================================================
@router.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request):
    return templates.TemplateResponse(
        "signup.html", {"request": request, "error": None}
    )


@router.post("/signup")
def signup_submit(
    request: Request, email: str = Form(...), username: str = Form(...), password: str = Form(...)
):
    r = requests.post(
        f"{_api_base(request)}/auth/signup", data={"email": email, "username": username, "password": password}
    )
    if r.status_code != 200:
        return templates.TemplateResponse("signup.html", {"request": request, "error": "Signup failed"})
    return RedirectResponse(url="/login", status_code=303)


# ======================================================
# LOGIN
# ======================================================
@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login")
def login_submit(request: Request, email_or_username: str = Form(...), password: str = Form(...)):
    r = requests.post(f"{_api_base(request)}/auth/login", data={"email_or_username": email_or_username, "password": password})
    if r.status_code != 200:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid credentials"})
    token = r.json()["access_token"]
    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie(key="access_token", value=token, httponly=True, samesite="lax")
    return response


# ======================================================
# DASHBOARD
# ======================================================
@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request, 
    error: str = Query(None),
    level: int = Query(None),
    user: User = Depends(get_current_user)
):
    error_message = None
    if error == "no_challenge" and level:
        error_message = f"No challenges available at level {level}. Please create challenges for this level first."
    elif error == "no_more_challenges" and level:
        solved = request.query_params.get("solved", "0")
        required = request.query_params.get("required", "0")
        error_message = f"All level {level} challenges completed! You've solved {solved}/{required} required challenges. More challenges need to be added for level {level}."
    elif error == "all_solved" and level:
        solved = request.query_params.get("solved", "0")
        required = request.query_params.get("required", "0")
        error_message = f"Congratulations! You've solved {solved}/{required} level {level} challenges. You should level up soon!"
    
    return templates.TemplateResponse(
        "dashboard.html", {
            "request": request, 
            "username": user.username, 
            "level": user.level, 
            "streak": user.streak, 
            "verified": user.is_verified,
            "user": user,  # Pass user object for profile
            "error_message": error_message
        }
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
        challenge_r = requests.get(f"{_api_base(request)}/challenge/{challenge_id}", cookies=request.cookies)
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
            challenge_r = requests.get(f"{_api_base(request)}/challenge/{latest_submission.challenge_id}", cookies=request.cookies)
            if challenge_r.status_code == 200:
                challenge = challenge_r.json()
                challenge_id = latest_submission.challenge_id  # Set challenge_id for consistency
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
            check = requests.get(f"{_api_base(request)}/submission/check-today", cookies=request.cookies)
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
                    Submission.is_correct == 1
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
                "level": current_level
            }
            
            # If in edit mode, get the latest correct submission for this challenge to pre-fill
            if edit:
                latest_correct = (
                    db.query(Submission)
                    .filter(
                        Submission.user_id == user.id,
                        Submission.challenge_id == challenge_id,
                        Submission.is_correct == 1
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
                    sub = requests.get(f"{_api_base(request)}/submission/{submission_id}?allow_incorrect=true", cookies=request.cookies)
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
            "user": user
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
            cookies=request.cookies
        )
        if r.status_code != 200:
            # Get challenge data to show error
            challenge_r = requests.get(f"{_api_base(request)}/challenge/{challenge_id}", cookies=request.cookies)
            challenge = challenge_r.json() if challenge_r.status_code == 200 else None
            return templates.TemplateResponse(
                "challenge.html",
                {
                    "request": request,
                    "challenge": challenge,
                    "today_completed": False,
                    "previous_code": code,
                    "edit_mode": True,
                    "error_message": "Your answer is incorrect. Please try again!" if challenge else "Error submitting challenge.",
                    "user": user
                }
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
                return RedirectResponse(url=f"/submission/{submission_id}/view?level_up=true&new_level={new_level}&old_level={old_level}", status_code=303)
            return RedirectResponse(url=f"/submission/{submission_id}/view", status_code=303)
        else:
            # Get challenge data to show error
            challenge_r = requests.get(f"{_api_base(request)}/challenge/{challenge_id}", cookies=request.cookies)
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
                    "user": user
                }
            )
    else:
        # Regular daily challenge submission
        r = requests.post(f"{_api_base(request)}/challenge/submit", data={"code": code}, cookies=request.cookies)
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
            # If user leveled up, add level up info to session
            if level_up:
                # Store level up info in a way that can be accessed by the frontend
                # We'll pass it via query parameter or use a different approach
                response = RedirectResponse(url=f"/progress/{submission_id}?level_up=true&new_level={new_level}&old_level={old_level}", status_code=303)
                return response
            return RedirectResponse(url=f"/progress/{submission_id}", status_code=303)
        else:  # If the answer is incorrect, stay on the challenge page with error message
            # Get challenge data to render the challenge page properly
            challenge_r = requests.get(f"{_api_base(request)}/challenge/today", cookies=request.cookies)
            challenge = challenge_r.json() if challenge_r.status_code == 200 else None
            
            check_r = requests.get(f"{_api_base(request)}/submission/check-today", cookies=request.cookies)
            today_completed = False
            if check_r.status_code == 200:
                today_completed = check_r.json().get("completed", False)
            
            # Get the previous code to pre-fill the editor
            previous_code = code
            
            # When showing error message, always show the form (edit_mode=True) so user can try again
            # This overrides the "Today's Challenge Complete" screen when there's an error
            return templates.TemplateResponse(
                "challenge.html",
                {
                    "request": request,
                    "challenge": challenge,
                    "today_completed": today_completed,
                    "previous_code": previous_code,
                    "edit_mode": True,  # Keep edit mode ON so form is visible even if today is completed
                    "error_message": "Your answer is incorrect. Please try again!",
                    "mentor_hint": mentor_hint,  # Pass mentor hint to template
                    "user": user
                }
            )


# ======================================================
# JOURNEY PAGE (LATEST SUBMISSION PROGRESS)
# ======================================================
@router.get("/journey", response_class=HTMLResponse)
def journey_page(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Show journey page with tree view by default, or detail if submission_id provided."""
    submission_id = request.query_params.get("submission_id")
    
    if submission_id:
        # Load specific submission detail
        r = requests.get(f"{_api_base(request)}/submission/{submission_id}", cookies=request.cookies)
        if r.status_code == 200:
            submission = r.json()
            return templates.TemplateResponse("progress.html", {"request": request, "submission": submission})
    
    # Get only main categories where the user has attempted challenges (has submissions)
    main_categories = (
        db.query(Challenge.main_category)
        .join(Submission, Submission.challenge_id == Challenge.id)
        .filter(
            Submission.user_id == user.id,
            Challenge.main_category.isnot(None),
            Challenge.main_category != ""
        )
        .distinct()
        .all()
    )
    main_categories = [cat[0] for cat in main_categories if cat[0]]
    
    # Default: Show tree view (no submission)
    return templates.TemplateResponse(
        "journey_layout.html", 
        {
            "request": request, 
            "submission": None, 
            "user": user,
            "main_categories": main_categories
        }
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
    # Get the submission details from the backend
    # The API will reject incorrect submissions (400 error)
    r = requests.get(f"{_api_base(request)}/submission/{submission_id}", cookies=request.cookies)
    if r.status_code != 200:
        # If submission is incorrect or not found, redirect to dashboard
        return RedirectResponse(url="/dashboard", status_code=303)

    submission = r.json()
    
    # Double-check: if submission is not correct, redirect (shouldn't happen due to API check, but safety)
    if not submission.get("is_correct", False):
        return RedirectResponse(url="/dashboard", status_code=303)
    
    # Check for level up from query parameters
    level_up = request.query_params.get("level_up") == "true"
    new_level = request.query_params.get("new_level")
    old_level = request.query_params.get("old_level")

    # Render the progress page template with submission data
    return templates.TemplateResponse("progress.html", {
        "request": request, 
        "submission": submission,
        "level_up": level_up,
        "new_level": int(new_level) if new_level else None,
        "old_level": int(old_level) if old_level else None,
        "user": user
    })


@router.get("/submission/{submission_id}/view", response_class=HTMLResponse)
def submission_view(request: Request, submission_id: int, user: User = Depends(get_current_user)):
    """View route for submission detail - renders HTML template."""
    # Get the submission details from the backend API
    # Allow viewing incorrect submissions for journey page (learning purposes)
    r = requests.get(f"{_api_base(request)}/submission/{submission_id}?allow_incorrect=true", cookies=request.cookies)
    if r.status_code != 200:
        # If submission not found, redirect to dashboard
        return RedirectResponse(url="/dashboard", status_code=303)

    submission = r.json()
    
    # Check for level up from query parameters
    level_up = request.query_params.get("level_up") == "true"
    new_level = request.query_params.get("new_level")
    old_level = request.query_params.get("old_level")

    # Render the progress page template with submission data
    return templates.TemplateResponse("progress.html", {
        "request": request, 
        "submission": submission,
        "level_up": level_up,
        "new_level": int(new_level) if new_level else None,
        "old_level": int(old_level) if old_level else None,
        "user": user
    })


# ======================================================
# ADMIN – CREATE DAILY CHALLENGE
# ======================================================
@router.get("/admin/challenge/new", response_class=HTMLResponse)
def admin_new_challenge(request: Request, user: User = Depends(get_admin)):
    return templates.TemplateResponse("admin_challenge.html", {"request": request, "today": date.today(), "error": None, "user": user, "challenge": None, "edit_mode": False})


@router.get("/admin/challenge/edit/{challenge_id}", response_class=HTMLResponse)
def admin_edit_challenge(
    request: Request,
    challenge_id: int,
    user: User = Depends(get_admin),
    db: Session = Depends(get_db),
):
    """Edit an existing challenge."""
    challenge = db.query(Challenge).filter(Challenge.id == challenge_id).first()
    
    if not challenge:
        return templates.TemplateResponse(
            "admin_challenge.html",
            {
                "request": request,
                "today": date.today(),
                "error": "Challenge not found",
                "user": user,
                "challenge": None,
                "edit_mode": False
            }
        )
    
    return templates.TemplateResponse(
        "admin_challenge.html",
        {
            "request": request,
            "today": date.today(),
            "error": None,
            "user": user,
            "challenge": challenge,
            "edit_mode": True
        }
    )


@router.get("/admin/challenges/list", response_class=HTMLResponse)
def admin_challenges_list(
    request: Request,
    user: User = Depends(get_admin),
    db: Session = Depends(get_db),
):
    """List all challenges for admin review in journey-style layout."""
    # Get all unique main categories
    main_categories = db.query(Challenge.main_category).distinct().filter(
        Challenge.main_category.isnot(None),
        Challenge.main_category != ""
    ).all()
    main_categories = [cat[0] for cat in main_categories if cat[0]]
    
    return templates.TemplateResponse(
        "admin_challenges_list.html",
        {
            "request": request,
            "main_categories": main_categories,
            "user": user,
        }
    )


@router.post("/admin/challenge/update/{challenge_id}")
def admin_update_challenge(
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
    """Update an existing challenge."""
    challenge = db.query(Challenge).filter(Challenge.id == challenge_id).first()
    
    if not challenge:
        return templates.TemplateResponse(
            "admin_challenge.html",
            {
                "request": request,
                "today": date.today(),
                "error": "Challenge not found",
                "user": user,
                "challenge": None,
                "edit_mode": False
            }
        )
    
    # Parse challenge_date if provided
    parsed_date = None
    if challenge_date and challenge_date.strip():
        try:
            parsed_date = date.fromisoformat(challenge_date)
        except ValueError:
            return templates.TemplateResponse(
                "admin_challenge.html",
                {
                    "request": request,
                    "today": date.today(),
                    "error": "Invalid date format",
                    "user": user,
                    "challenge": challenge,
                    "edit_mode": True
                }
            )
    
    # Update challenge fields
    challenge.level = level
    challenge.title = title
    challenge.description = description
    challenge.expected_output = expected_output
    challenge.challenge_date = parsed_date
    challenge.main_category = main_category.strip()
    challenge.sub_category = sub_category.strip()
    challenge.stage_order = stage_order
    
    try:
        db.commit()
        db.refresh(challenge)
        success_message = f"Challenge updated successfully! (ID: {challenge.id})"
        return templates.TemplateResponse(
            "admin_challenge.html",
            {
                "request": request,
                "today": date.today(),
                "error": None,
                "success": success_message,
                "user": user,
                "challenge": challenge,
                "edit_mode": True
            }
        )
    except Exception as e:
        db.rollback()
        return templates.TemplateResponse(
            "admin_challenge.html",
            {
                "request": request,
                "today": date.today(),
                "error": f"Failed to update challenge: {str(e)}",
                "user": user,
                "challenge": challenge,
                "edit_mode": True
            }
        )


@router.post("/admin/challenge/new")
def admin_create_challenge(
    request: Request,
    level: int = Form(...),
    title: str = Form(...),
    description: str = Form(...),
    expected_output: str = Form(...),
    challenge_date: str = Form(None),  # Optional - leave empty for pool challenges
    main_category: str = Form(...),
    sub_category: str = Form(...),
    stage_order: int = Form(1),
    user: User = Depends(get_admin),
):
    r = requests.post(
        f"{_api_base(request)}/challenge/admin/create",
        data={
            "level": level,
            "title": title,
            "description": description,
            "expected_output": expected_output,
            "challenge_date": challenge_date or "",  # Send empty string if None
            "main_category": main_category,
            "sub_category": sub_category,
            "stage_order": stage_order,
        },
        cookies=request.cookies,
    )

    if r.status_code != 200:
        error_detail = "Failed to create challenge"
        try:
            error_detail = r.json().get("detail", error_detail)
        except:
            pass
        return templates.TemplateResponse(
            "admin_challenge.html", {"request": request, "today": date.today(), "error": error_detail, "user": user}
        )

    # Check the response to see if challenge was actually created
    result = r.json() if r.status_code == 200 else {}
    success_message = None
    if result.get("status") == "challenge created":
        challenge_id = result.get("challenge_id", "unknown")
        success_message = f"Challenge created successfully! (ID: {challenge_id})"

    # Just return to the form - don't redirect, UI should not react
    return templates.TemplateResponse(
        "admin_challenge.html", {"request": request, "today": date.today(), "error": None, "success": success_message, "user": user}
    )


# ======================================================
# ADMIN - USER MANAGEMENT
# ======================================================
@router.get("/admin/users", response_class=HTMLResponse)
def admin_users(
    request: Request,
    user: User = Depends(get_main_admin),  # Only main admin can access
    db: Session = Depends(get_db),
):
    """List all users for main admin to manage."""
    users = db.query(User).order_by(User.created_at.asc()).all()
    
    # If no users exist, show message that admin is the only user
    if not users:
        return templates.TemplateResponse(
            "admin_users.html",
            {
                "request": request,
                "users": [],
                "user": user,
                "no_users_message": "You are the only user in the system.",
            }
        )
    
    users_data = []
    for u in users:
        # Determine role: main admin by ID constant, otherwise use role field
        is_main_admin = u.id == MAIN_ADMIN_USER_ID
        role = "admin" if is_main_admin else (u.role or "user")
        
        users_data.append({
            "id": u.id,
            "username": u.username,
            "email": u.email,
            "level": u.level,
            "role": role,
            "is_main_admin": is_main_admin,
            "is_co_admin": role == "coadmin",
            "created_at": u.created_at,
        })
    
    return templates.TemplateResponse(
        "admin_users.html",
        {
            "request": request,
            "users": users_data,
            "user": user,
        }
    )


@router.post("/admin/users/{user_id}/promote", response_class=HTMLResponse)
def promote_to_co_admin(
    request: Request,
    user_id: int,
    user: User = Depends(get_main_admin),  # Only main admin can promote
    db: Session = Depends(get_db),
):
    """Promote a user to co-admin (main admin only)."""
    target_user = db.query(User).filter(User.id == user_id).first()
    
    if not target_user:
        return RedirectResponse(url="/admin/users?error=User not found", status_code=303)
    
    # Cannot modify the main admin (protected by ID constant)
    if target_user.id == MAIN_ADMIN_USER_ID:
        return RedirectResponse(url="/admin/users?error=Cannot modify main admin", status_code=303)
    
    # Only promote normal users to co-admin (cannot promote to "admin" role)
    if target_user.role == "admin":
        return RedirectResponse(url="/admin/users?error=Cannot modify admin role", status_code=303)
    
    # Promote to co-admin
    target_user.role = "coadmin"
    db.commit()
    
    return RedirectResponse(url="/admin/users?success=User promoted to co-admin", status_code=303)


@router.post("/admin/users/{user_id}/demote", response_class=HTMLResponse)
def demote_co_admin(
    request: Request,
    user_id: int,
    user: User = Depends(get_main_admin),  # Only main admin can demote
    db: Session = Depends(get_db),
):
    """Demote a co-admin back to regular user (main admin only)."""
    target_user = db.query(User).filter(User.id == user_id).first()
    
    if not target_user:
        return RedirectResponse(url="/admin/users?error=User not found", status_code=303)
    
    # Cannot modify the main admin (protected by ID constant)
    if target_user.id == MAIN_ADMIN_USER_ID:
        return RedirectResponse(url="/admin/users?error=Cannot modify main admin", status_code=303)
    
    # Only demote co-admins (cannot demote "admin" role)
    if target_user.role == "admin":
        return RedirectResponse(url="/admin/users?error=Cannot modify admin role", status_code=303)
    
    # Demote from co-admin to normal user
    target_user.role = "user"
    db.commit()
    
    return RedirectResponse(url="/admin/users?success=User demoted from co-admin", status_code=303)


@router.post("/admin/users/{user_id}/delete", response_class=HTMLResponse)
def delete_user(
    request: Request,
    user_id: int,
    user: User = Depends(get_main_admin),  # Only main admin can delete users
    db: Session = Depends(get_db),
):
    """Delete a user and all their progress (main admin only)."""
    target_user = db.query(User).filter(User.id == user_id).first()
    
    if not target_user:
        return RedirectResponse(url="/admin/users?error=User not found", status_code=303)
    
    # Cannot delete the main admin (protected by ID constant)
    if target_user.id == MAIN_ADMIN_USER_ID:
        return RedirectResponse(url="/admin/users?error=Cannot delete main admin", status_code=303)
    
    try:
        # Get all submission IDs for this user to delete related insights
        submission_ids = db.query(Submission.id).filter(Submission.user_id == user_id).all()
        submission_ids = [sub_id[0] for sub_id in submission_ids]
        
        # Delete all submission insights for this user's submissions
        if submission_ids:
            db.query(SubmissionInsight).filter(SubmissionInsight.submission_id.in_(submission_ids)).delete()
        
        # Delete all submissions for this user
        db.query(Submission).filter(Submission.user_id == user_id).delete()
        
        # Delete the user
        db.delete(target_user)
        db.commit()
        
        return RedirectResponse(url="/admin/users?success=User deleted successfully (all progress removed)", status_code=303)
    except Exception as e:
        db.rollback()
        return RedirectResponse(url=f"/admin/users?error=Failed to delete user: {str(e)}", status_code=303)


# ======================================================
# LEARN MORE (FORCE LEARNING) - PROGRESSION SYSTEM
# ======================================================
@router.get("/force-learning")
def force_learning(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Learn More progression system:
    - Gives challenges from the CURRENT level
    - Users solve current-level challenges to demonstrate mastery
    - When they solve enough (solved >= current_level), they level up
    - Allows fast leveling up by solving more challenges at current level
    """
    current_level = user.level
    
    # Get all challenges at current level
    # For "Learn More", we allow ALL challenges at the current level, regardless of date
    # This allows users to access challenges even if they have dates set
    all_challenges = (
        db.query(Challenge)
        .filter(Challenge.level == current_level)
        .all()
    )
    
    if not all_challenges:
        # No challenges available at current level - redirect with message
        return RedirectResponse(
            url=f"/dashboard?error=no_challenge&level={current_level}", 
            status_code=303
        )
    
    # Get all challenge IDs the user has solved correctly at current level
    solved_challenge_ids = (
        db.query(distinct(Submission.challenge_id))
        .join(Challenge, Challenge.id == Submission.challenge_id)
        .filter(
            Submission.user_id == user.id,
            Submission.is_correct == 1,
            Challenge.level == current_level,
        )
        .all()
    )
    solved_ids = {row[0] for row in solved_challenge_ids}
    
    # Filter to unsolved challenges
    unsolved_challenges = [ch for ch in all_challenges if ch.id not in solved_ids]
    
    # Count how many challenges solved at current level
    solved_count = len(solved_ids)
    required_count = current_level
    
    if not unsolved_challenges:
        # All challenges at current level are solved
        # Check if user has solved enough to level up
        if solved_count >= required_count:
            # User has solved enough, they should level up
            # But they haven't yet - redirect with message
            return RedirectResponse(
                url=f"/dashboard?error=all_solved&level={current_level}&solved={solved_count}&required={required_count}", 
                status_code=303
            )
        else:
            # Not enough solved yet, but no more challenges available
            return RedirectResponse(
                url=f"/dashboard?error=no_more_challenges&level={current_level}&solved={solved_count}&required={required_count}", 
                status_code=303
            )
    else:
        # Group unsolved challenges by category/subcategory
        import random
        challenges_by_category = {}
        for ch in unsolved_challenges:
            key = (ch.main_category or "", ch.sub_category or "")
            if key not in challenges_by_category:
                challenges_by_category[key] = []
            challenges_by_category[key].append(ch)
        
        # If there are multiple categories, randomize which category to pick from
        # Then randomize within that category
        if challenges_by_category:
            selected_category = random.choice(list(challenges_by_category.keys()))
            category_challenges = challenges_by_category[selected_category]
            selected_challenge = random.choice(category_challenges)
        else:
            selected_challenge = random.choice(unsolved_challenges)
    
    # Redirect to challenge page with this challenge from current level
    return RedirectResponse(url=f"/challenge?challenge_id={selected_challenge.id}", status_code=303)


# ======================================================
# LOGOUT
# ======================================================
@router.get("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("access_token")
    return response
