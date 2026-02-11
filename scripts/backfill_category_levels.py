"""
Backfill script: Create UserCategoryProgress records for existing users.

This script:
1. Finds all users
2. For each user, finds all categories they've interacted with (via submissions)
3. Creates UserCategoryProgress records with level=user.level (migrating from global level)
4. If no submissions exist, creates a default record for each existing category
"""
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from app.db.session import SessionLocal
from app.auth.models import User
from app.auth.category_progress import UserCategoryProgress
from app.challenges.models import Challenge
from app.submissions.models import Submission
from sqlalchemy import distinct


def backfill_category_levels():
    """Backfill UserCategoryProgress from existing user.level values."""
    db = SessionLocal()
    
    try:
        # Get all users
        users = db.query(User).all()
        print(f"Found {len(users)} users to process", flush=True)
        
        # Get all unique categories from challenges
        all_categories = db.query(distinct(Challenge.main_category)).filter(
            Challenge.main_category.isnot(None),
            Challenge.main_category != ""
        ).all()
        all_category_names = [cat[0] for cat in all_categories if cat[0]]
        print(f"Found {len(all_category_names)} categories: {all_category_names}", flush=True)
        
        for user in users:
            print(f"\nProcessing user {user.id} ({user.username})", flush=True)
            
            # Find categories this user has interacted with (via submissions)
            user_categories = db.query(distinct(Challenge.main_category)).join(
                Submission, Submission.challenge_id == Challenge.id
            ).filter(
                Submission.user_id == user.id,
                Challenge.main_category.isnot(None),
                Challenge.main_category != ""
            ).all()
            user_category_names = [cat[0] for cat in user_categories if cat[0]]
            
            print(f"  User has interacted with {len(user_category_names)} categories: {user_category_names}", flush=True)
            
            # Create progress records for categories user has interacted with
            # Use user.level as the initial level (migrating from global level)
            for category in user_category_names:
                existing = db.query(UserCategoryProgress).filter(
                    UserCategoryProgress.user_id == user.id,
                    UserCategoryProgress.main_category == category
                ).first()
                
                if not existing:
                    progress = UserCategoryProgress(
                        user_id=user.id,
                        main_category=category,
                        level=user.level,  # Migrate from global level
                        xp=0
                    )
                    db.add(progress)
                    print(f"    Created progress: {category} -> level {user.level}", flush=True)
                else:
                    print(f"    Progress already exists: {category} -> level {existing.level}", flush=True)
            
            # Optionally: Create default records for all categories (level=1)
            # Uncomment if you want every user to have progress for every category
            # for category in all_category_names:
            #     if category not in user_category_names:
            #         existing = db.query(UserCategoryProgress).filter(
            #             UserCategoryProgress.user_id == user.id,
            #             UserCategoryProgress.main_category == category
            #         ).first()
            #         if not existing:
            #             progress = UserCategoryProgress(
            #                 user_id=user.id,
            #                 main_category=category,
            #                 level=1,  # Default level
            #                 xp=0
            #             )
            #             db.add(progress)
        
        db.commit()
        print(f"\n✅ Backfill complete!", flush=True)
        
    except Exception as e:
        db.rollback()
        print(f"❌ Error during backfill: {e}", flush=True)
        raise
    finally:
        db.close()


if __name__ == "__main__":
    backfill_category_levels()

