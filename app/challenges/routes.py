from fastapi import APIRouter, Depends, Form, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, distinct, or_
from datetime import date, timedelta
import io
import contextlib
import random
import ast
import re
import logging
import uuid as _uuid
import time as _time
import traceback as _traceback
import subprocess as _subprocess
import tempfile as _tempfile
import os as _os
try:
    import openai
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
from app.core.config import OPENAI_API_KEY

# Configure logger to output to console
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    formatter = logging.Formatter('%(levelname)s: [MENTOR HINT] %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.propagate = False

# Also use print for immediate visibility
def debug_print(msg):
    print(f"[MENTOR HINT DEBUG] {msg}", flush=True)

from app.db.session import get_db
from app.challenges.models import Challenge
from app.submissions.models import Submission, SubmissionInsight
from app.auth.models import User
from app.core.deps import get_current_user, get_admin

router = APIRouter(prefix="/challenge", tags=["challenge"])


def normalize_output_text(text: str) -> str:
    """
    Normalize execution/expected output before correctness checks.
    Handles CRLF/LF differences and trailing spaces/newlines.
    """
    if text is None:
        return ""
    normalized = str(text).replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in normalized.split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines).strip()


# ======================================================
# MENTOR HINT GENERATOR
# ======================================================
def should_trigger_mentor_hint(attempt_number: int) -> bool:
    """
    Check if mentor hint should trigger based on attempt number.
    STRICT RULE: Triggers on 3, 5, 7, 8, 10, and EVERY attempt AFTER 10.
    Do NOT use modulo logic or "multiple of X" rules.
    """
    if attempt_number in [3, 5, 7, 8, 10]:
        return True
    if attempt_number > 10:  # EVERY attempt after 10
        return True
    return False


def generate_mentor_hint_openai(code: str, description: str, expected_output: str, user_output: str, attempt_number: int, has_error: bool = False) -> str:
    """
    Generate mentor hint using OpenAI.
    
    Type A: Code does NOT run or throws syntax/runtime error → STRUCTURE or SYNTAX-LEVEL hint
    Type B: Code runs and produces output but output is wrong → LOGIC-LEVEL hint
    
    Returns None if OpenAI fails or response violates rules.
    """
    debug_print(f"OpenAI function called - attempt_number={attempt_number}, code_length={len(code)}, description_length={len(description)}")
    logger.info(f"[MENTOR HINT] OpenAI function called - attempt_number={attempt_number}, code_length={len(code)}, description_length={len(description)}")
    
    if not OPENAI_AVAILABLE:
        debug_print("OpenAI module not available")
        logger.warning("[MENTOR HINT] OpenAI module not available")
        return None
    
    if not OPENAI_API_KEY or OPENAI_API_KEY == "":
        debug_print("OpenAI API key not set")
        logger.warning("[MENTOR HINT] OpenAI API key not set")
        return None
    
    try:
        debug_print("Calling OpenAI API...")
        logger.info("[MENTOR HINT] Calling OpenAI API...")
        client = openai.OpenAI(api_key=OPENAI_API_KEY)
        
        # Determine hint type
        if has_error:
            hint_context = "The code does NOT run or throws a syntax/runtime error. The interpreter stops before execution."
        else:
            hint_context = "The code runs and produces output, but the output does NOT match what is expected."
        
        # Determine progressive reveal strategy based on attempt number
        if attempt_number == 3:
            reveal_level = "ATTEMPT_3"
            reveal_instructions = """ATTEMPT 3 - Describe what kind of thing is wrong:
- Describe what kind of thing is wrong
- No fix, no code
- Just identify the category of the problem
- Example: "This is a structural issue" or "The interpreter can't distinguish between command and data"
"""
        elif attempt_number == 5:
            reveal_level = "ATTEMPT_5"
            reveal_instructions = """ATTEMPT 5 - Name the missing element explicitly:
- Name the missing element explicitly
- Example: "You're missing the quote" or "The parentheses are missing"
- Still no code
- Be direct about what's missing
"""
        elif attempt_number == 7:
            reveal_level = "ATTEMPT_7"
            reveal_instructions = """ATTEMPT 7 - Suggest a partial correction:
- Suggest a partial correction
- Example: "The text needs to be treated as text" or "This part needs to be wrapped"
- No full syntax
- Point to what needs to happen, not how exactly
"""
        elif attempt_number == 8:
            reveal_level = "ATTEMPT_8"
            reveal_instructions = """ATTEMPT 8 - Reveal ONE corrected line OR ONE corrected fragment:
- Reveal ONE corrected line OR ONE corrected fragment
- Never the whole program
- Never more than one line
- Show exactly one line of corrected code
- Example: Show just the corrected print statement, or just the corrected variable assignment
"""
        elif attempt_number == 10:
            reveal_level = "ATTEMPT_10"
            reveal_instructions = """ATTEMPT 10 - Reveal another missing piece:
- Reveal another missing piece
- Still incomplete overall solution
- Show another one-line fragment or correction
- Continue building the solution piece by piece
"""
        else:  # After attempt 10
            reveal_level = "ATTEMPT_10_PLUS"
            reveal_instructions = """AFTER ATTEMPT 10 - Continue dropping remaining lines:
- Mentor may continue dropping remaining lines
- One line per hint
- Never paste the entire solution together
- Show one more corrected line or fragment
- Keep the solution incomplete so learner must think
"""
        
        prompt = f"""You are a mentor on a coding practice platform. Your role is to guide learners by revealing the solution gradually, in fragments across attempts.

CORE PHILOSOPHY:
The mentor IS allowed to give the solution, but ONLY in fragments, spread across attempts.
Never reveal the full solution in one hint.

PROGRESSIVE SOLUTION RULE (MANDATORY):
The mentor must reveal the solution gradually, based on attempt number.
The learner should feel: "I can finish this myself now."

WHAT YOU MUST DO:

- Reveal the solution piece by piece across attempts
- Show one line or fragment at a time (starting from attempt 8)
- Speak like a calm senior engineer
- Be direct and helpful, but never give everything at once
- Let the learner combine multiple hints to solve it

STYLE REQUIREMENTS:
- Calm and direct
- 1-2 sentences maximum
- Instructional but partial
- No emojis, no praise, no scolding
- Sound like a senior engineer helping a colleague

GOOD MENTOR HINT EXAMPLES BY ATTEMPT:

ATTEMPT 3 - Describe what kind of thing is wrong:
- "This is a structural issue — the interpreter can't distinguish between command and data."
- "The problem is with how the message is presented to the interpreter."

ATTEMPT 5 - Name the missing element explicitly:
- "You're missing the quote marks around the text."
- "The parentheses are missing around the function call."

ATTEMPT 7 - Suggest a partial correction:
- "The text needs to be treated as text, not as a command."
- "This part needs to be wrapped to mark it as data."

ATTEMPT 8 - Reveal ONE corrected line:
- "This line needs quotes — fix that first: print(\"Hello World\")"
- "Try this: print('Hello World')"

ATTEMPT 10 - Reveal another missing piece:
- "Now add the closing parenthesis here: )"
- "The next line should be: x = 5"

AFTER 10 - Continue dropping remaining lines:
- "Now the structure is right, but one line still isn't. Try: y = 10"
- "One more piece: z = x + y"

BAD HINT EXAMPLES (FORBIDDEN - DO NOT DO THIS):
- "Here's the full working code: [entire solution]"
- "Copy this: [multiple lines]"
- "Here is the solution"
- Showing multiple corrected lines in one hint
- Outputting the entire correct program at once

--------------------------------------------------
PROGRESSIVE REVEAL STRATEGY (CRITICAL):
Current attempt: {attempt_number}
Reveal level: {reveal_level}

{reveal_instructions}

ABSOLUTE LIMITS (applies to ALL attempt levels):
The mentor must NEVER:
- Output the full correct code at once
- Output multiple corrected lines in one hint
- Say "Here is the solution"
- Remove the learner's need to think

SUCCESS CONDITION:
If the learner solves the problem by combining multiple mentor hints,
the mentor did the job correctly.

MENTOR VOICE:
Speak like a calm senior engineer.
Good: "This line needs quotes — fix that first."
Good: "Now the structure is right, but one line still isn't."
Good: "Try adjusting just this part next."

--------------------------------------------------
CONTEXT:
{hint_context}

Challenge description: {description}
Expected output: {expected_output}
User's actual output: {user_output}
User's code:
{code}
Attempt number: {attempt_number}

Respond with ONLY the hint (1-2 sentences max), or return NOTHING if you cannot give a hint without violating rules."""

        # Adjust temperature based on reveal level (more creative for early attempts, more focused for later attempts)
        if reveal_level == "ATTEMPT_3":
            temperature = 0.5  # Balanced for describing problem type
        elif reveal_level == "ATTEMPT_5":
            temperature = 0.4  # More focused for naming missing elements
        elif reveal_level == "ATTEMPT_7":
            temperature = 0.4  # Focused for partial corrections
        else:  # ATTEMPT_8, ATTEMPT_10, ATTEMPT_10_PLUS
            temperature = 0.3  # Very focused for showing code fragments
        
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": f"You are a mentor on a coding practice platform. You reveal solutions gradually in fragments across attempts. Current attempt: {attempt_number} ({reveal_level}). You speak like a calm senior engineer. You show one line or fragment at a time (starting from attempt 8), never the full solution. The learner should feel they can finish it themselves by combining your hints."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=100,  # Increased to allow code fragments
            temperature=temperature
        )
        
        hint = response.choices[0].message.content.strip()
        debug_print(f"OpenAI response received: {hint[:100]}...")
        logger.info(f"[MENTOR HINT] OpenAI response received: {hint[:100]}...")
        
        # Validate response: must be one sentence, max 20 words, no code blocks
        if not hint:
            debug_print("OpenAI returned empty hint")
            logger.warning("[MENTOR HINT] OpenAI returned empty hint")
            return None
        
        # Validation: Reject hints that show the FULL solution or multiple lines at once
        # ALLOW: Single line fragments (from attempt 8+), code snippets, partial solutions
        # REJECT: Full solutions, multiple lines in one hint, "here is the solution"
        
        hint_lower = hint.lower()
        
        # Reject if hint explicitly says it's giving the full solution
        if "here is the solution" in hint_lower or "here's the solution" in hint_lower or "full solution" in hint_lower:
            debug_print(f"Hint rejected - claims to give full solution")
            logger.warning(f"[MENTOR HINT] Hint rejected - claims full solution: {hint}")
            return None
        
        # Reject if hint contains code blocks (markdown code blocks suggest full code)
        if "```" in hint:
            debug_print(f"Hint rejected - contains code blocks (suggests full code)")
            logger.warning(f"[MENTOR HINT] Hint rejected - contains code blocks: {hint}")
            return None
        
        # Count newlines in hint - if more than 2-3 lines of code, likely showing too much
        # Allow single line fragments, but reject multi-line solutions
        code_line_count = hint.count('\n')
        if code_line_count > 2:
            # Check if it looks like multiple lines of actual code (not just text with line breaks)
            if any(keyword in hint for keyword in ["print(", "def ", "for ", "if ", "while ", "return "]):
                debug_print(f"Hint rejected - contains multiple lines of code ({code_line_count} lines)")
                logger.warning(f"[MENTOR HINT] Hint rejected - multiple code lines: {hint}")
                return None
        
        # Note: We do NOT reject hints for using words like:
        # structure, interpreter, syntax, message, command, element, arrangement, clarity, definition, separation
        # These words are ALLOWED and are part of good mentor guidance
        
        # We also do NOT reject hints that mention quotes conceptually
        # Only reject if quotes are used to show actual code (which is caught by code_syntax_patterns above)
        
        # VALIDATION PHILOSOPHY:
        # The mentor IS allowed to show code fragments (one line at a time from attempt 8+)
        # The mentor IS allowed to name missing elements and suggest corrections
        # The mentor is NOT allowed to show the full solution or multiple lines at once
        
        # If we got here, the hint passes validation
        # It may show single line code fragments (from attempt 8+) - this is ALLOWED
        # It may name missing elements explicitly (from attempt 5+) - this is ALLOWED
        # It may show partial corrections (from attempt 7+) - this is ALLOWED
        
        # Check word count (max 40 words for 2 sentences)
        word_count = len(hint.split())
        if word_count > 40:
            debug_print(f"Hint rejected - word count {word_count} exceeds 40")
            logger.warning(f"[MENTOR HINT] Hint rejected - word count {word_count} exceeds 40")
            return None
        
        # Check if it's more than 2 sentences (allow 1-2 sentences)
        sentence_endings = hint.count('.') + hint.count('!') + hint.count('?')
        if sentence_endings > 2:
            debug_print(f"Hint rejected - too many sentences ({sentence_endings} > 2)")
            logger.warning(f"[MENTOR HINT] Hint rejected - too many sentences ({sentence_endings} > 2)")
            return None
        
        debug_print(f"Hint validated successfully: {hint}")
        logger.info(f"[MENTOR HINT] Hint validated successfully: {hint}")
        return hint
        
    except Exception as e:
        # Log error but don't break the app if OpenAI is down
        debug_print(f"OpenAI call failed: {str(e)}")
        logger.error(f"[MENTOR HINT] OpenAI call failed: {str(e)}", exc_info=True)
        return None


def generate_mentor_hint(code: str, expected_output: str, execution_output: str, execution_error: str = None) -> str:
    """
    Generate a specific, technical mentor hint based on code analysis.
    Returns None if no hint should be given.
    
    Priority 1-3: Static hints for syntax errors and basic issues (no OpenAI)
    Priority 4: Bucket D cases use OpenAI (code executes, output exists, but wrong)
    """
    if not code or not code.strip():
        return None
    
    code_stripped = code.strip()
    
    # Priority 1: Syntax errors (static analysis, no OpenAI)
    if execution_error:
        error_lower = execution_error.lower()
        
        # Missing quotes around strings
        if "nameerror" in error_lower and "not defined" in error_lower:
            undefined_match = re.search(r"name '([^']+)' is not defined", error_lower)
            if undefined_match:
                name = undefined_match.group(1)
                if name and not re.search(r'[_\d]', name) and len(name) > 1:
                    return f"Strings need quotes."
        
        # Missing parentheses
        if "syntaxerror" in error_lower or "invalid syntax" in error_lower:
            open_count = code.count("(")
            close_count = code.count(")")
            if open_count > close_count:
                return "Missing closing parenthesis."
            elif close_count > open_count:
                return "Missing opening parenthesis."
            elif "(" in code or ")" in code:
                return "Something is missing inside the parentheses."
        
        # Indentation errors
        if "indentationerror" in error_lower or "unexpected indent" in error_lower:
            return "This line is indented too far."
        
        # Colon missing
        if "expected ':'" in error_lower:
            if any(keyword in code_stripped for keyword in ["if ", "for ", "while ", "def ", "else", "elif "]):
                return "Missing colon after control statement."
        
        # For syntax errors, return static hint (no OpenAI)
        return None
    
    # Priority 2: Missing print statement (static, no OpenAI)
    if "print" not in code_stripped.lower():
        if expected_output:
            return "Try starting with: print("
    
    # Priority 3: Print statement issues (static, no OpenAI)
    if "print(" in code_stripped:
        print_matches = re.finditer(r'print\s*\((.*?)\)', code_stripped, re.DOTALL)
        for match in print_matches:
            content = match.group(1).strip()
            if content and not (content.startswith('"') or content.startswith("'") or content.startswith('f"')):
                if re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', content):
                    return "Strings need quotes."
        
        if not execution_output.strip() and expected_output.strip():
            return "Check what you're printing."
    
    # Priority 4: Bucket D - Code executes, output exists, but wrong (use OpenAI)
    # This case is handled in submission endpoints, not here
    # This function returns None for Bucket D, OpenAI is called directly from endpoints
    return None


# ======================================================
# COUNT SOLVED CHALLENGES FOR A LEVEL
# ======================================================
@router.get("/solved-count/{level}")
def get_solved_count(
    level: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Count distinct challenges solved correctly by user at a specific level."""
    from sqlalchemy import distinct

    solved_count = (
        db.query(func.count(distinct(Submission.challenge_id)))
        .join(Challenge, Challenge.id == Submission.challenge_id)
        .filter(
            Submission.user_id == user.id,
            Submission.is_correct == 1,
            Challenge.level == level,
        )
        .scalar()
    )

    return {"count": solved_count or 0}

# ======================================================
# GET NEXT UNSOLVED CHALLENGE FOR A CATEGORY  (Rules B, D, E)
# ======================================================
@router.get("/next/{level}")
def get_next_challenge(
    level: int,                           # kept for URL compat; ignored when category given
    main_category: str = Query(None),
    force_next_level: bool = Query(False), # legacy param, ignored
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Get the next unsolved challenge for the CURRENT USER.
    When main_category is given: uses strict level == user's category level.
    Respects daily cap (2/day) in normal mode, unlimited in fast-track.
    """
    from app.auth.category_level import get_next_challenge_for_category

    if main_category and main_category.strip():
        result = get_next_challenge_for_category(db, user.id, main_category.strip())
        return result

    # No category specified — return nothing (user must select category)
    return {"challenge_id": None, "reason": "NO_CATEGORY", "message": "Select a category first."}

# ======================================================
# GET SUBCATEGORIES FOR A MAIN CATEGORY
# ======================================================
@router.get("/subcategories/{main_category}")
def get_subcategories(main_category: str, db: Session = Depends(get_db)):
    """Get ALL subcategories (Skill Stages) - predefined list from admin page in specific order."""
    from sqlalchemy import func

    # Predefined Skill Stages in the exact order specified
    predefined_stages = [
        "Fundamental",
        "Amateur",
        "Intermediate",
        "Advanced",
        "Expert",
        "Builder",
        "Master"
    ]

    # Get all distinct subcategories that exist in challenges for this main category
    # Use case-insensitive comparison to ensure we get all matches
    db_subcategories = (
        db.query(Challenge.sub_category)
        .filter(func.lower(Challenge.main_category) == main_category.lower())
        .distinct()
        .all()
    )

    # Extract subcategory names from database
    existing_subs = [sub[0] for sub in db_subcategories if sub[0] and sub[0].strip()]

    # Start with predefined stages in order
    result = []
    seen = set()

    # Add predefined stages first in the specified order
    for stage in predefined_stages:
        if stage not in seen:
            result.append(stage)
            seen.add(stage)

    # Add any additional subcategories from database that aren't in predefined list
    for sub in existing_subs:
        if sub not in seen:
            result.append(sub)
            seen.add(sub)

    # Return subcategories in the specified order
    return {"subcategories": result}

# ======================================================
# GET SOLUTIONS/CHALLENGES FOR A SUBCATEGORY
# ======================================================
@router.get("/solutions/{main_category}/{sub_category}")
def get_solutions(
    main_category: str,
    sub_category: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get all ATTEMPTED challenges for a subcategory (only questions user has tried)."""
    from app.submissions.models import Submission

    # Get all submissions for this user in this category/subcategory
    attempted_submissions = (
        db.query(Submission, Challenge)
        .join(Challenge, Challenge.id == Submission.challenge_id)
        .filter(
            Submission.user_id == user.id,
            Challenge.main_category == main_category,
            Challenge.sub_category == sub_category
        )
        .order_by(Submission.created_at.desc())
        .all()
    )

    # Group by challenge_id to get the latest submission for each challenge
    challenge_submissions = {}
    for submission, challenge in attempted_submissions:
        challenge_id = challenge.id
        if challenge_id not in challenge_submissions:
            challenge_submissions[challenge_id] = (submission, challenge)

    solutions = []
    for challenge_id, (submission, challenge) in challenge_submissions.items():
        # Get the latest correct submission if exists
        correct_submission = (
            db.query(Submission)
            .filter(
                Submission.user_id == user.id,
                Submission.challenge_id == challenge_id,
                Submission.is_correct == 1
            )
            .order_by(Submission.created_at.desc())
            .first()
        )

        # Only add to solutions if there's a correct submission
        if correct_submission:
            solutions.append({
                "id": challenge.id,
                "title": challenge.title,
                "level": challenge.level,
                "stage_order": challenge.stage_order,
                "submission_id": correct_submission.id,
                "completed": True,
            })

    # Sort by stage_order
    solutions.sort(key=lambda x: x["stage_order"])

    return {"solutions": solutions}

# ======================================================
# GET TODAY'S CHALLENGE
# ======================================================
@router.get("/today")
def get_today_challenge(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Get today's challenge based on user's level and completion status.
    - If user completed yesterday's challenge and has solved enough challenges at their level,
      they get a challenge from the next level.
    - Otherwise, they get a challenge from their current level.
    - Always selects an unsolved challenge for today's date.
    """
    # Determine the appropriate level for today's challenge
    target_level = user.level
    
    # Check if user completed yesterday's daily challenge
    yesterday = date.today() - timedelta(days=1)
    yesterday_challenge_completed = (
        db.query(Submission)
        .join(Challenge, Challenge.id == Submission.challenge_id)
        .filter(
            Submission.user_id == user.id,
            Challenge.challenge_date == yesterday,
            Submission.is_correct == 1,
        )
        .first()
    ) is not None
    
    # If user completed yesterday's challenge, check if they should progress
    if yesterday_challenge_completed:
        # Count distinct challenges solved correctly at user's current level
        solved_count = (
            db.query(func.count(distinct(Submission.challenge_id)))
            .join(Challenge, Challenge.id == Submission.challenge_id)
            .filter(
                Submission.user_id == user.id,
                Submission.is_correct == 1,
                Challenge.level == user.level,
            )
            .scalar()
        ) or 0
        
        # If user solved enough challenges at current level, they should get next level challenge
        if solved_count >= user.level:
            target_level = user.level + 1
    
    # Get all active challenges for today's date at the target level
    today_challenges = (
        db.query(Challenge)
        .filter(
            Challenge.challenge_date == date.today(),
            Challenge.level == target_level,
            or_(Challenge.is_active.is_(True), Challenge.is_active.is_(None)),
        )
        .all()
    )
    
    if not today_challenges:
        # No challenges available for today at this level
        return None
    
    # Get all challenge IDs the user has solved correctly
    solved_challenge_ids = (
        db.query(distinct(Submission.challenge_id))
        .join(Challenge, Challenge.id == Submission.challenge_id)
        .filter(
            Submission.user_id == user.id,
            Submission.is_correct == 1,
        )
        .all()
    )
    solved_ids = {row[0] for row in solved_challenge_ids}
    
    # Filter to unsolved challenges
    unsolved_challenges = [ch for ch in today_challenges if ch.id not in solved_ids]
    
    # If no unsolved challenges at target level, try current level
    if not unsolved_challenges and target_level > user.level:
        current_level_challenges = (
            db.query(Challenge)
            .filter(
                Challenge.challenge_date == date.today(),
                Challenge.level == user.level,
                or_(Challenge.is_active.is_(True), Challenge.is_active.is_(None)),
            )
            .all()
        )
        unsolved_challenges = [ch for ch in current_level_challenges if ch.id not in solved_ids]
        target_level = user.level
    
    # If still no unsolved challenges, return any challenge (user has solved all)
    if not unsolved_challenges:
        challenge = today_challenges[0]  # Return first available challenge
    else:
        # Randomly select from unsolved challenges
        challenge = random.choice(unsolved_challenges)
    
    return {
        "id": challenge.id,
        "level": challenge.level,
        "title": challenge.title,
        "description": challenge.description,
        "main_category": challenge.main_category or "",
        "sub_category": challenge.sub_category or "",
        "stage_order": challenge.stage_order or 1,
        "expected_output": challenge.expected_output or "",
    }

# ======================================================
# TEST CODE EXECUTION (FOR TERMINAL OUTPUT)
# ======================================================
# Safety constants for test-code execution
_TEST_CODE_MAX_LENGTH = 10_000       # max characters of user code
_TEST_CODE_TIMEOUT_SECONDS = 5       # subprocess execution timeout
_TEST_CODE_MAX_OUTPUT_LENGTH = 5_000  # max chars returned in output


def _run_code_in_subprocess(code: str, timeout: int) -> tuple[str, str | None]:
    """
    Run user code in an isolated subprocess with a timeout.
    Returns (stdout_output, error_string_or_None).
    Uses a per-request temp file (uuid-based) and cleans up in finally.
    """
    tmp_path = None
    try:
        # Create a unique temp file for this request
        fd, tmp_path = _tempfile.mkstemp(suffix=".py", prefix="codeguru_test_")
        with _os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(code)

        result = _subprocess.run(
            ["python", tmp_path],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=_tempfile.gettempdir(),  # don't run in app directory
        )

        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()

        if result.returncode != 0:
            # Code raised an exception or had a syntax error
            # Parse the stderr to give a clean error message
            error_msg = stderr
            # Try to extract just the last line (the actual exception)
            if stderr:
                lines = stderr.strip().split("\n")
                # Find the last meaningful error line
                for line in reversed(lines):
                    line_stripped = line.strip()
                    if line_stripped and not line_stripped.startswith("Traceback") and not line_stripped.startswith("File "):
                        error_msg = line_stripped
                        break
            return stdout, error_msg or f"Process exited with code {result.returncode}"
        
        return stdout, None

    except _subprocess.TimeoutExpired:
        return "", f"Execution timed out after {timeout} seconds"
    except FileNotFoundError:
        # python binary not found – signal caller to use fallback
        return "", "Execution error: No such file or directory (python binary not found)"
    except Exception as e:
        return "", f"Execution error: {str(e)}"
    finally:
        # Always clean up the temp file
        if tmp_path:
            try:
                _os.unlink(tmp_path)
            except OSError:
                pass


def _run_code_in_process(code: str) -> tuple[str, str | None]:
    """
    Fallback: run code in-process using exec() with stdout capture.
    Used only if subprocess execution is unavailable (e.g. no python binary).
    """
    buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    try:
        import sys
        old_stderr = sys.stderr
        sys.stderr = stderr_buffer
        try:
            with contextlib.redirect_stdout(buffer):
                exec(code, {"__builtins__": __builtins__})
        finally:
            sys.stderr = old_stderr
        return buffer.getvalue().strip(), None
    except SyntaxError as e:
        return buffer.getvalue().strip(), f"SyntaxError: {e}"
    except Exception as e:
        return buffer.getvalue().strip(), f"{type(e).__name__}: {e}"


@router.post("/test-code")
def test_code(
    code: str = Form(""),
    user: User = Depends(get_current_user),
):
    """
    Execute user code and return output for the live terminal preview.
    - Runs in an isolated subprocess with a timeout.
    - Falls back to in-process exec if subprocess is unavailable.
    - Never crashes the server; all errors are caught and returned as JSON.
    """
    request_id = str(_uuid.uuid4())[:8]
    start_time = _time.time()
    user_id = user.id if user else "anon"
    execution_method = "none"

    try:
        # ── Auth check ──────────────────────────────────────
        if not user:
            return {"ok": False, "output": "", "error": "Not authenticated", "details": "Login required"}

        # ── Validate input ──────────────────────────────────
        if not code or not code.strip():
            return {"ok": True, "output": "", "error": None}

        code = code.strip()

        if len(code) > _TEST_CODE_MAX_LENGTH:
            return {
                "ok": False,
                "output": "",
                "error": f"Code too long ({len(code)} chars). Maximum is {_TEST_CODE_MAX_LENGTH} characters.",
                "details": "Reduce your code length and try again.",
            }

        print(f"[TEST-CODE {request_id}] user={user_id} code_len={len(code)}", flush=True)

        # ── Try subprocess execution first (isolated) ───────
        execution_method = "subprocess"
        output, error = _run_code_in_subprocess(code, _TEST_CODE_TIMEOUT_SECONDS)

        # If subprocess returned a fallback signal (FileNotFoundError for python binary),
        # fall back to in-process execution
        if error is not None and "Execution error:" in error and "No such file" in error:
            execution_method = "in-process-fallback"
            print(f"[TEST-CODE {request_id}] subprocess unavailable, falling back to in-process exec", flush=True)
            output, error = _run_code_in_process(code)

        # Truncate output if too long
        if output and len(output) > _TEST_CODE_MAX_OUTPUT_LENGTH:
            output = output[:_TEST_CODE_MAX_OUTPUT_LENGTH] + "\n... (output truncated)"

        elapsed = _time.time() - start_time
        print(
            f"[TEST-CODE {request_id}] done method={execution_method} "
            f"elapsed={elapsed:.3f}s output_len={len(output)} error={'yes' if error else 'no'}",
            flush=True,
        )

        return {"ok": True, "output": output, "error": error}

    except Exception as exc:
        # ── CATCH-ALL: this endpoint must NEVER crash the server ──
        elapsed = _time.time() - start_time
        tb = _traceback.format_exc()
        print(
            f"[TEST-CODE ERROR {request_id}] user={user_id} method={execution_method} "
            f"elapsed={elapsed:.3f}s\n{tb}",
            flush=True,
        )
        return {
            "ok": False,
            "output": "",
            "error": f"Internal error: {type(exc).__name__}: {str(exc)}",
            "details": "The server encountered an unexpected error running your code. Please try again.",
        }

# ======================================================
# SEARCH CHALLENGES  ✅ MOVED ABOVE /{challenge_id} (ONLY CHANGE)
# ======================================================
@router.get("/search")
def search_challenges(
    q: str = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Search ONLY challenges that the current user has attempted."""
    if not q or not q.strip():
        return {"challenges": []}

    # Convert search term to lowercase for case-insensitive matching
    search_term = q.strip()
    search_term_lower = f"%{search_term.lower()}%"

    # Only include challenges that THIS USER has attempted (any submission)
    # Join with Submission and filter by user_id, then apply the text search.
    challenges_query = (
        db.query(Challenge)
        .join(Submission, Submission.challenge_id == Challenge.id)
        .filter(Submission.user_id == user.id)
        .filter(
            or_(
                func.lower(Challenge.title).like(search_term_lower),
                func.lower(Challenge.description).like(search_term_lower),
                func.lower(func.coalesce(Challenge.expected_output, "")).like(search_term_lower),
                func.lower(func.coalesce(Challenge.main_category, "")).like(search_term_lower),
                func.lower(func.coalesce(Challenge.sub_category, "")).like(search_term_lower),
            )
        )
        .order_by(Challenge.level.asc(), Challenge.id.desc())
    )

    # Distinct challenges (user might have many submissions for same challenge)
    challenges = challenges_query.distinct(Challenge.id).limit(50).all()

    result = []
    for ch in challenges:
        result.append({
            "id": ch.id,
            "level": ch.level,
            "title": ch.title,
            "description": ch.description or "",
            "main_category": ch.main_category or "",
            "sub_category": ch.sub_category or "",
            "expected_output": ch.expected_output or "",
        })

    return {"challenges": result}

# ======================================================
# GET CHALLENGE BY ID
# ======================================================
@router.get("/{challenge_id}")
def get_challenge_by_id(
    challenge_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get a specific challenge by ID."""
    challenge = db.query(Challenge).filter(Challenge.id == challenge_id).first()

    if not challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")

    # Strict access: challenge level must equal user's category level
    from app.auth.category_level import get_user_category_level
    challenge_category = challenge.main_category if challenge.main_category and challenge.main_category.strip() else None
    if challenge_category:
        user_level = get_user_category_level(db, user.id, challenge_category)
    else:
        user_level = user.level
    if challenge.level > user_level:
        cat_msg = f" for {challenge_category}" if challenge_category else ""
        raise HTTPException(
            status_code=403,
            detail=f"Level {challenge.level} challenge is above your current level{cat_msg} ({user_level})."
        )

    return {
        "id": challenge.id,
        "level": challenge.level,
        "title": challenge.title,
        "description": challenge.description,
        "main_category": challenge.main_category or "",
        "sub_category": challenge.sub_category or "",
        "stage_order": challenge.stage_order or 1,
        "expected_output": challenge.expected_output or "",
    }

# ======================================================
# SUBMIT CHALLENGE (TODAY'S CHALLENGE)
# ======================================================
@router.post("/submit")
def submit_challenge(
    code: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    i_dont_know: bool = Form(False),  # New flag for "I don't know" option
):
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Get today's challenge appropriate for user's level
    # First check if user completed yesterday's challenge to determine target level
    yesterday = date.today() - timedelta(days=1)
    yesterday_challenge_completed = (
        db.query(Submission)
        .join(Challenge, Challenge.id == Submission.challenge_id)
        .filter(
            Submission.user_id == user.id,
            Challenge.challenge_date == yesterday,
            Submission.is_correct == 1,
        )
        .first()
    ) is not None
    
    # Determine target level
    target_level = user.level
    if yesterday_challenge_completed:
        solved_count = (
            db.query(func.count(distinct(Submission.challenge_id)))
            .join(Challenge, Challenge.id == Submission.challenge_id)
            .filter(
                Submission.user_id == user.id,
                Submission.is_correct == 1,
                Challenge.level == user.level,
            )
            .scalar()
        ) or 0
        if solved_count >= user.level:
            target_level = user.level + 1
    
    # Get active challenge for today at target level (or current level if no next level challenge)
    act = or_(Challenge.is_active.is_(True), Challenge.is_active.is_(None))
    challenge = (
        db.query(Challenge)
        .filter(
            Challenge.challenge_date == date.today(),
            Challenge.level == target_level,
            act,
        )
        .first()
    )
    
    # Fallback to current level if no challenge at target level
    if not challenge and target_level > user.level:
        challenge = (
            db.query(Challenge)
            .filter(
                Challenge.challenge_date == date.today(),
                Challenge.level == user.level,
                act,
            )
            .first()
        )
    
    # Final fallback: any active challenge for today
    if not challenge:
        challenge = (
            db.query(Challenge)
            .filter(
                Challenge.challenge_date == date.today(),
                or_(Challenge.is_active.is_(True), Challenge.is_active.is_(None)),
            )
            .first()
        )

    if not challenge:
        raise HTTPException(status_code=400, detail="No challenge today")

    # ------------------------------------
    # FIRST EVER SUBMISSION (GLOBAL)
    # ------------------------------------
    first_time_global = False
    has_any_submission = (
        db.query(Submission)
        .filter(Submission.user_id == user.id)
        .first()
    )

    if not has_any_submission:
        first_time_global = True
        user.level = 1  # Make sure the user starts at level 1
        db.add(user)

    # ------------------------------------
    # ATTEMPT COUNT
    # ------------------------------------
    attempt_number = (
        db.query(Submission)
        .filter(
            Submission.user_id == user.id,
            Submission.challenge_id == challenge.id,
        )
        .count()
        + 1
    )

    is_retry = 1 if attempt_number > 1 else 0

    # ------------------------------------
    # RUN USER CODE
    # ------------------------------------
    output = ""
    is_correct = 0

    try:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            exec(code, {})

        output = buffer.getvalue().strip()
        output_normalized = normalize_output_text(output)
        expected_normalized = normalize_output_text(challenge.expected_output or "")

        if output_normalized == expected_normalized:
            is_correct = 1

    except Exception as e:
        output = f"Error: {str(e)}"
        is_correct = 0

    # ------------------------------------
    # HANDLE "I DON'T KNOW"
    # ------------------------------------
    if i_dont_know:
        return {"status": "failed", "message": "Try again tomorrow or use hints!"}

    # ------------------------------------
    # SAVE SUBMISSION
    # ------------------------------------
    submission = Submission(
        user_id=user.id,
        challenge_id=challenge.id,
        code=code,
        is_correct=is_correct,
        is_first_submission=1 if first_time_global else 0,
        attempt_number=attempt_number,
        is_retry=is_retry,
    )

    db.add(submission)
    db.commit()
    db.refresh(submission)

    # ------------------------------------
    # 🧠 CREATE EMPTY INSIGHT (IDEMPOTENT)
    # ------------------------------------
    _existing_insight = db.query(SubmissionInsight).filter_by(submission_id=submission.id).one_or_none()
    if _existing_insight:
        print(f"[INSIGHT] submission_id={submission.id} already exists — skipped", flush=True)
    else:
        db.add(SubmissionInsight(
            submission_id=submission.id, concepts="",
            learning_points="", real_world_use="", improvement_hint="",
        ))
        db.commit()
        print(f"[INSIGHT] submission_id={submission.id} inserted", flush=True)

    # ------------------------------------
    # 🎯 MENTOR HINT (on attempts 3, 5, 7, 8, 10, or ≥ 11)
    # ------------------------------------
    mentor_hint = None
    if is_correct == 0:  # Only check for wrong attempts
        # Check if we should trigger hint based on attempt number
        if should_trigger_mentor_hint(attempt_number):
            debug_print(f"Attempt {attempt_number} detected - checking for mentor hint...")
            # Check if code has error or produces wrong output
            has_error = output.startswith("Error:")
            has_output = output and output.strip() and not has_error
            has_expected = challenge.expected_output and challenge.expected_output.strip()
            
            debug_print(f"Mentor hint check - has_error={has_error}, has_output={has_output}, has_expected={has_expected}")
            debug_print(f"  Output: '{output[:100] if output else None}'")
            debug_print(f"  Expected: '{challenge.expected_output[:100] if challenge.expected_output else None}'")
            
            # Trigger hint for:
            # Type A: Syntax/runtime errors (has_error = True)
            # Type B: Code runs but output is wrong (has_error = False, has_output = True, output != expected)
            output_normalized = normalize_output_text(output)
            expected_normalized = normalize_output_text(challenge.expected_output or "")
            if has_error or (has_output and has_expected and output_normalized != expected_normalized):
                debug_print("Conditions met - calling OpenAI...")
                mentor_hint = generate_mentor_hint_openai(
                    code=code,
                    description=challenge.description or "",
                    expected_output=challenge.expected_output or "",
                    user_output=output,
                    attempt_number=attempt_number,
                    has_error=has_error
                )
                debug_print(f"OpenAI returned: {mentor_hint}")
            else:
                debug_print(f"Conditions not met - has_error={has_error}, has_output={has_output}, output matches={output_normalized == expected_normalized if output and challenge.expected_output else False}")
        else:
            debug_print(f"Attempt {attempt_number} - no hint trigger (only on 3, 5, 7, 8, 10, or ≥ 11)")

    # Level progression: Rule C — solve N at level N, counter-based
    from app.auth.category_level import record_solve_and_maybe_level_up
    
    level_up = False
    old_level = user.level
    new_level = user.level
    category_for_level = None
    
    if is_correct:
        challenge_category = challenge.main_category if challenge.main_category and challenge.main_category.strip() else None
        if challenge_category:
            level_up, old_level, new_level = record_solve_and_maybe_level_up(
                db, user.id, challenge_category, challenge.level
            )
            category_for_level = challenge_category

    debug_print(f"Returning response - mentor_hint={'SET' if mentor_hint else 'None'}")
    logger.info(f"[MENTOR HINT] Returning response - mentor_hint={'SET' if mentor_hint else 'None'}")
    
    return {
        "status": "submitted",
        "submission_id": submission.id,
        "output": output,
        "correct": bool(is_correct),
        "first_time_global": first_time_global,
        "attempt_number": attempt_number,
        "is_retry": bool(is_retry),
        "new_level": new_level if new_level is not None else user.level,  # Send new level back
        "level_up": level_up,  # Flag indicating if user leveled up
        "old_level": old_level if old_level is not None else user.level,  # Previous level before this submission
        "category": category_for_level,  # Category that leveled up (if any)
        "mentor_hint": mentor_hint,  # Mentor hint if triggered (None otherwise)
    }

# ======================================================
# SUBMIT FORCE-LEARNING CHALLENGE (POOL CHALLENGES)
# ======================================================
@router.post("/submit-force")
def submit_force_challenge(
    challenge_id: int = Form(...),
    code: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Submit a force-learning (pool) challenge by challenge_id."""
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Get the challenge by ID
    challenge = db.query(Challenge).filter(Challenge.id == challenge_id).first()
    if not challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")

    # Strict access: challenge level must equal user's category level
    from app.auth.category_level import get_user_category_level, record_solve_and_maybe_level_up
    challenge_category = challenge.main_category if challenge.main_category and challenge.main_category.strip() else None
    if challenge_category:
        user_level_for_cat = get_user_category_level(db, user.id, challenge_category)
        if challenge.level > user_level_for_cat:
            raise HTTPException(
                status_code=403,
                detail=f"Level {challenge.level} is above your level for {challenge_category} ({user_level_for_cat})."
            )
        print(f"[SUBMIT-FORCE] cat='{challenge_category}' user_level={user_level_for_cat} challenge_level={challenge.level}", flush=True)
    else:
        user_level_for_cat = user.level

    # ------------------------------------
    # FIRST EVER SUBMISSION (GLOBAL)
    # ------------------------------------
    first_time_global = False
    has_any_submission = (
        db.query(Submission)
        .filter(Submission.user_id == user.id)
        .first()
    )

    if not has_any_submission:
        first_time_global = True
        user.level = 1  # Make sure the user starts at level 1
        db.add(user)

    # ------------------------------------
    # ATTEMPT COUNT
    # ------------------------------------
    attempt_number = (
        db.query(Submission)
        .filter(
            Submission.user_id == user.id,
            Submission.challenge_id == challenge.id,
        )
        .count()
        + 1
    )

    is_retry = 1 if attempt_number > 1 else 0

    # ------------------------------------
    # RUN USER CODE
    # ------------------------------------
    output = ""
    is_correct = 0

    try:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            exec(code, {})

        output = buffer.getvalue().strip()
        output_normalized = normalize_output_text(output)
        expected_normalized = normalize_output_text(challenge.expected_output or "")

        print(f"[API DEBUG] submit-force output: {repr(output_normalized[:100])}", flush=True)
        print(f"[API DEBUG] submit-force expected: {repr(expected_normalized[:100])}", flush=True)
        print(f"[API DEBUG] submit-force match: {output_normalized == expected_normalized}", flush=True)

        if output_normalized == expected_normalized:
            is_correct = 1

    except Exception as e:
        output = f"Error: {str(e)}"
        is_correct = 0
        print(f"[API DEBUG] submit-force exec error: {str(e)}", flush=True)

    print(f"[API DEBUG] submit-force is_correct={is_correct} for challenge {challenge.id}", flush=True)

    # ------------------------------------
    # SAVE SUBMISSION (per-user progress only; never delete/disable the challenge)
    # ------------------------------------
    submission = Submission(
        user_id=user.id,
        challenge_id=challenge.id,
        code=code,
        is_correct=is_correct,
        is_first_submission=1 if first_time_global else 0,
        attempt_number=attempt_number,
        is_retry=is_retry,
    )

    db.add(submission)
    db.commit()
    db.refresh(submission)

    # ------------------------------------
    # 🧠 CREATE EMPTY INSIGHT (IDEMPOTENT)
    # ------------------------------------
    _existing_insight = db.query(SubmissionInsight).filter_by(submission_id=submission.id).one_or_none()
    if _existing_insight:
        print(f"[INSIGHT] submission_id={submission.id} already exists — skipped", flush=True)
    else:
        db.add(SubmissionInsight(
            submission_id=submission.id, concepts="",
            learning_points="", real_world_use="", improvement_hint="",
        ))
        db.commit()
        print(f"[INSIGHT] submission_id={submission.id} inserted", flush=True)

    # ------------------------------------
    # 🎯 MENTOR HINT (on attempts 3, 5, 7, 8, 10, or ≥ 11)
    # ------------------------------------
    mentor_hint = None
    if is_correct == 0:  # Only check for wrong attempts
        # Check if we should trigger hint based on attempt number
        if should_trigger_mentor_hint(attempt_number):
            debug_print(f"Attempt {attempt_number} detected (force) - checking for mentor hint...")
            # Check if code has error or produces wrong output
            has_error = output.startswith("Error:")
            has_output = output and output.strip() and not has_error
            has_expected = challenge.expected_output and challenge.expected_output.strip()
            
            debug_print(f"Mentor hint check (force) - has_error={has_error}, has_output={has_output}, has_expected={has_expected}")
            debug_print(f"  Output: '{output[:100] if output else None}'")
            debug_print(f"  Expected: '{challenge.expected_output[:100] if challenge.expected_output else None}'")
            
            # Trigger hint for:
            # Type A: Syntax/runtime errors (has_error = True)
            # Type B: Code runs but output is wrong (has_error = False, has_output = True, output != expected)
            output_normalized = normalize_output_text(output)
            expected_normalized = normalize_output_text(challenge.expected_output or "")
            if has_error or (has_output and has_expected and output_normalized != expected_normalized):
                debug_print("Conditions met (force) - calling OpenAI...")
                mentor_hint = generate_mentor_hint_openai(
                    code=code,
                    description=challenge.description or "",
                    expected_output=challenge.expected_output or "",
                    user_output=output,
                    attempt_number=attempt_number,
                    has_error=has_error
                )
                debug_print(f"OpenAI returned (force): {mentor_hint}")
            else:
                debug_print(f"Conditions not met (force) - has_error={has_error}, has_output={has_output}, output matches={output_normalized == expected_normalized if output and challenge.expected_output else False}")
        else:
            debug_print(f"Attempt {attempt_number} (force) - no hint trigger (only on 3, 5, 7, 8, 10, or ≥ 11)")

    # Level progression: Rule C — solve N at level N, counter-based
    level_up = False
    old_level = user_level_for_cat
    new_level = user_level_for_cat
    if is_correct and challenge_category:
        level_up, old_level, new_level = record_solve_and_maybe_level_up(
            db, user.id, challenge_category, challenge.level
        )

    resp_current = new_level
    resp_old = old_level

    logger.info(f"[MENTOR HINT] Returning response (force) - mentor_hint={'SET' if mentor_hint else 'None'}")
    
    return {
        "status": "submitted",
        "submission_id": submission.id,
        "output": output,
        "correct": bool(is_correct),
        "first_time_global": first_time_global,
        "attempt_number": attempt_number,
        "is_retry": bool(is_retry),
        "current_level": resp_current,
        "level_up": level_up,
        "old_level": resp_old,
        "mentor_hint": mentor_hint,
    }

# ======================================================
# ACTIVATE FAST TRACK (Learn More)  — Rule E
# ======================================================
@router.post("/learn-more/activate")
def activate_fast_track(
    main_category: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Permanently enable fast-track for this user+category."""
    from app.auth.category_level import enable_fast_track
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    progress = enable_fast_track(db, user.id, main_category.strip())
    return {"ok": True, "fast_track_enabled": True, "main_category": main_category.strip(), "level": progress.level}


# ======================================================
# ADMIN – CREATE CHALLENGE
# ======================================================
@router.post("/admin/create")
def admin_create_challenge(
    level: int = Form(...),
    title: str = Form(...),
    description: str = Form(...),
    expected_output: str = Form(...),
    challenge_date: str = Form(None),  # Optional - leave empty for pool challenges

    # New fields added to the form
    main_category: str = Form(...),   # Main category (e.g., Basic Python)
    sub_category: str = Form(...),    # Sub category (e.g., Fundamentals)
    stage_order: int = Form(1),       # Stage order (default 1)

    db: Session = Depends(get_db),
    user: User = Depends(get_admin),  # Require admin access
):

    # Parse challenge_date if provided, otherwise None (for pool challenges)
    parsed_date = None
    if challenge_date and challenge_date.strip():
        try:
            parsed_date = date.fromisoformat(challenge_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format")

    # Always create new challenges - this allows multiple challenges with the same date
    # This is important for "Learn More" pool challenges where users need multiple challenges per level
    # Even if a challenge with today's date exists, we create a new one (allows multiple pool challenges)
    try:
        challenge = Challenge(
            level=level,
            title=title,
            description=description,
            expected_output=expected_output,
            challenge_date=parsed_date,  # None for pool challenges, date for daily/future challenges

            # These are the new fields that should be inserted
            main_category=main_category.strip(),
            sub_category=sub_category.strip(),
            stage_order=stage_order,
        )

        db.add(challenge)
        db.commit()
        db.refresh(challenge)
        return {"status": "challenge created", "challenge_id": challenge.id}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to create challenge: {str(e)}")

# ======================================================
# ADMIN – LIST CHALLENGES
# ======================================================
@router.get("/admin/list")
def admin_list_challenges(
    main_category: str = None,
    sub_category: str = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_admin),
):
    """List challenges filtered by category and subcategory (admin only)."""
    query = db.query(Challenge)

    if main_category:
        query = query.filter(Challenge.main_category == main_category)

    if sub_category:
        query = query.filter(Challenge.sub_category == sub_category)

    challenges = query.order_by(
        Challenge.level.asc(),
        Challenge.id.desc()
    ).all()

    result = []
    for ch in challenges:
        result.append({
            "id": ch.id,
            "level": ch.level,
            "title": ch.title,
            "description": ch.description,
            "main_category": ch.main_category or "",
            "sub_category": ch.sub_category or "",
            "expected_output": ch.expected_output or "",
            "challenge_date": ch.challenge_date.isoformat() if ch.challenge_date else None,
        })

    return {"challenges": result}


# ======================================================
# ADMIN – DELETE CHALLENGE
# ======================================================
@router.post("/admin/delete/{challenge_id}")
def admin_delete_challenge(
    challenge_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_admin),
):
    """Delete a challenge (admin and co-admin only)."""
    from fastapi.responses import RedirectResponse
    
    challenge = db.query(Challenge).filter(Challenge.id == challenge_id).first()
    
    if not challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")
    
    # Delete the challenge
    db.delete(challenge)
    db.commit()
    
    # Redirect to admin challenge list page instead of returning JSON
    return RedirectResponse(url="/admin/challenges/list", status_code=303)

