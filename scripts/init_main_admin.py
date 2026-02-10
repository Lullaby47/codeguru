"""
Script to initialize the main admin user.
This should be run once after the first user is created.

The first user (ID = 1) will be set as the main admin.
"""
import sys
import os

# Add the parent directory to the path so we can import app modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.db.session import SessionLocal
from app.auth.models import User
from app.core.config import MAIN_ADMIN_USER_ID


def init_main_admin():
    """Set the first user as the main admin."""
    db = SessionLocal()
    
    try:
        # Get the first user (should be ID = 1)
        main_admin = db.query(User).filter(User.id == MAIN_ADMIN_USER_ID).first()
        
        if not main_admin:
            print(f"ERROR: User with ID {MAIN_ADMIN_USER_ID} not found!")
            print("Please create a user first, then run this script.")
            return False
        
        # Set as main admin
        main_admin.is_main_admin = True
        db.commit()
        
        print(f"SUCCESS: User '{main_admin.username}' (ID: {main_admin.id}) has been set as the main admin.")
        print(f"Email: {main_admin.email}")
        return True
        
    except Exception as e:
        db.rollback()
        print(f"ERROR: Failed to set main admin: {str(e)}")
        return False
    finally:
        db.close()


if __name__ == "__main__":
    print("Initializing main admin...")
    print(f"Main admin user ID constant: {MAIN_ADMIN_USER_ID}")
    print("-" * 50)
    
    if init_main_admin():
        print("-" * 50)
        print("Main admin initialization complete!")
    else:
        print("-" * 50)
        print("Main admin initialization failed!")
        sys.exit(1)

