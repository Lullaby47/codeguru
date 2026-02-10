"""
One-time seeding script for user_journeys.

Purpose:
- Backfill journey records for existing users
- Reflect current users.level as a stable journey state
- SAFE to run multiple times (won't duplicate entries)

IMPORTANT:
- This script does NOT change users.level
- This script does NOT affect app behavior
- Run manually when you decide
"""

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.auth.models import User
from app.journey.models import UserJourney


def seed_user_journeys():
    db: Session = SessionLocal()

    try:
        users = db.query(User).all()
        created = 0
        skipped = 0

        for user in users:
            # Check if a journey entry already exists for this level
            existing = (
                db.query(UserJourney)
                .filter(
                    UserJourney.user_id == user.id,
                    UserJourney.level == user.level,
                )
                .first()
            )

            if existing:
                skipped += 1
                continue

            journey = UserJourney(
                user_id=user.id,
                level=user.level,
                status="stable",
                source="system",
                related_submission_id=None,
                metadata="",
            )

            db.add(journey)
            created += 1

        db.commit()

        print("✅ User journey seeding complete")
        print(f"   Created: {created}")
        print(f"   Skipped (already existed): {skipped}")

    except Exception as e:
        db.rollback()
        print("❌ Error while seeding user journeys")
        print(str(e))
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed_user_journeys()
