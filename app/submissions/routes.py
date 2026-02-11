from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.db.session import get_db
from app.submissions.models import Submission
from app.challenges.models import Challenge
from app.auth.models import User
from app.core.deps import get_current_user

router = APIRouter(prefix="/submission", tags=["submission"])

# ======================================================
# GET ALL ATTEMPTED QUESTIONS (FOR PROGRESS PAGE)
# ======================================================
@router.get("/attempted")
def get_attempted_questions(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get all questions the user has attempted, grouped by category/subcategory."""
    # Get all distinct challenges the user has attempted
    attempted_challenges = (
        db.query(Challenge)
        .join(Submission, Submission.challenge_id == Challenge.id)
        .filter(Submission.user_id == user.id)
        .distinct()
        .all()
    )
    
    # Group by main_category and sub_category
    grouped = {}
    for challenge in attempted_challenges:
        main_cat = challenge.main_category or "Uncategorized"
        sub_cat = challenge.sub_category or "Uncategorized"
        
        if main_cat not in grouped:
            grouped[main_cat] = {}
        if sub_cat not in grouped[main_cat]:
            grouped[main_cat][sub_cat] = []
        
        # Get latest correct submission if exists, otherwise latest submission
        correct_submission = (
            db.query(Submission)
            .filter(
                Submission.user_id == user.id,
                Submission.challenge_id == challenge.id,
                Submission.is_correct == 1
            )
            .order_by(Submission.created_at.desc())
            .first()
        )
        
        latest_submission = (
            db.query(Submission)
            .filter(
                Submission.user_id == user.id,
                Submission.challenge_id == challenge.id
            )
            .order_by(Submission.created_at.desc())
            .first()
        )
        
        grouped[main_cat][sub_cat].append({
            "id": challenge.id,
            "title": challenge.title,
            "level": challenge.level,
            "submission_id": correct_submission.id if correct_submission else latest_submission.id,
            "completed": correct_submission is not None,
        })
    
    return {"attempted": grouped}

# ======================================================
# CHECK IF TODAY'S CHALLENGE IS COMPLETED
# ======================================================
@router.get("/check-today")
def check_today_submission(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # Only check for CORRECT submissions - challenge is only completed when answer is correct
    today_submission = (
        db.query(Submission)
        .join(Challenge, Challenge.id == Submission.challenge_id)
        .filter(
            Submission.user_id == user.id,
            Challenge.challenge_date == func.current_date(),
            Submission.is_correct == 1,  # Only count correct submissions
        )
        .first()
    )

    return {"completed": bool(today_submission)}


# ======================================================
# GET LATEST SUBMISSION (FOR JOURNEY)
# ======================================================
@router.get("/all/{challenge_id}")
def get_all_submissions(
    challenge_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get all submissions for a specific challenge (both correct and wrong, excluding empty attempts).
    Returns correct attempts first, then wrong attempts, both in descending order (newest first)."""
    from app.challenges.models import Challenge
    from sqlalchemy import desc
    
    submissions = (
        db.query(Submission)
        .join(Challenge, Challenge.id == Submission.challenge_id)
        .filter(
            Submission.user_id == user.id,
            Submission.challenge_id == challenge_id
        )
        .order_by(desc(Submission.is_correct), desc(Submission.created_at))  # Correct first, then by newest first
        .all()
    )
    
    result = []
    for sub in submissions:
        # Skip empty submissions (only whitespace)
        if not sub.code or not sub.code.strip():
            continue
            
        challenge = db.query(Challenge).filter(Challenge.id == sub.challenge_id).first()
        if challenge:
            result.append({
                "id": sub.id,
                "code": sub.code,
                "created_at": sub.created_at,
                "is_correct": bool(sub.is_correct),
                "attempt_number": (
                    db.query(func.count(Submission.id))
                    .filter(
                        Submission.user_id == user.id,
                        Submission.challenge_id == sub.challenge_id,
                        Submission.created_at <= sub.created_at,
                    )
                    .scalar()
                ),
                "challenge": {
                    "id": challenge.id,
                    "title": challenge.title,
                    "level": challenge.level,
                    "description": challenge.description,
                    "main_category": challenge.main_category or "",
                    "sub_category": challenge.sub_category or "",
                    "expected_output": challenge.expected_output or "",
                }
            })
    
    return {"submissions": result}


@router.get("/wrong/{challenge_id}")
def get_wrong_submissions(
    challenge_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get all wrong submissions for a specific challenge (excluding empty attempts)."""
    from app.challenges.models import Challenge
    
    submissions = (
        db.query(Submission)
        .join(Challenge, Challenge.id == Submission.challenge_id)
        .filter(
            Submission.user_id == user.id,
            Submission.challenge_id == challenge_id,
            Submission.is_correct == 0
        )
        .order_by(Submission.created_at.desc())
        .all()
    )
    
    result = []
    for sub in submissions:
        # Skip empty submissions (only whitespace)
        if not sub.code or not sub.code.strip():
            continue
            
        challenge = db.query(Challenge).filter(Challenge.id == sub.challenge_id).first()
        if challenge:
            result.append({
                "id": sub.id,
                "code": sub.code,
                "created_at": sub.created_at,
                "attempt_number": (
                    db.query(func.count(Submission.id))
                    .filter(
                        Submission.user_id == user.id,
                        Submission.challenge_id == sub.challenge_id,
                        Submission.created_at <= sub.created_at,
                    )
                    .scalar()
                ),
                "challenge": {
                    "id": challenge.id,
                    "title": challenge.title,
                    "level": challenge.level,
                    "description": challenge.description,
                    "main_category": challenge.main_category or "",
                    "sub_category": challenge.sub_category or "",
                    "expected_output": challenge.expected_output or "",
                }
            })
    
    return {"submissions": result}


@router.get("/latest")
def get_latest_submission(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    correct_only: bool = Query(False, description="Only return correct submissions"),
):
    query = db.query(Submission).filter(Submission.user_id == user.id)
    
    if correct_only:
        query = query.filter(Submission.is_correct == 1)
    
    latest = query.order_by(Submission.created_at.desc()).first()

    if not latest:
        return {"submission_id": None}

    return {"submission_id": latest.id}


# ======================================================
# GET SINGLE SUBMISSION (PROGRESS PAGE)
# ======================================================
@router.get("/{submission_id}")
def get_submission(
    submission_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    allow_incorrect: bool = Query(False, description="Allow viewing incorrect submissions (for journey page)"),
):
    submission = (
        db.query(Submission)
        .filter(
            Submission.id == submission_id,
            Submission.user_id == user.id,
        )
        .first()
    )

    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    challenge = (
        db.query(Challenge)
        .filter(Challenge.id == submission.challenge_id)
        .first()
    )

    if not challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")

    attempt_number = (
        db.query(func.count(Submission.id))
        .filter(
            Submission.user_id == user.id,
            Submission.challenge_id == submission.challenge_id,
            Submission.created_at <= submission.created_at,
        )
        .scalar()
    )

    # If the submission is not correct, throw an exception to prevent progress
    # Unless allow_incorrect is True (for journey page)
    if not submission.is_correct and not allow_incorrect:
        raise HTTPException(status_code=400, detail="Incorrect answer, try again.")

    # Get the user's current per-category level for this challenge's category
    from app.auth.category_level import get_user_category_level
    challenge_category = challenge.main_category if challenge.main_category and challenge.main_category.strip() else None
    if challenge_category:
        current_cat_level = get_user_category_level(db, user.id, challenge_category)
    else:
        current_cat_level = user.level

    # Return details, but don't auto-route to progress page until correct submission
    return {
        "id": submission.id,
        "code": submission.code,
        "is_correct": bool(submission.is_correct),
        "attempt_number": attempt_number,
        "created_at": submission.created_at,
        "is_first_submission": bool(submission.is_first_submission),
        "current_level": current_cat_level,
        "challenge": {
            "id": challenge.id,
            "title": challenge.title,
            "level": challenge.level,
            "description": challenge.description,
            "main_category": challenge.main_category or "",
            "sub_category": challenge.sub_category or "",
            "stage_order": challenge.stage_order or 1,
        },
    }
