from helpers import checks
from config import OUTLINE_COLLECTION_IDS as COLLECTION_IDS


LINES_PER_PAGE = 15
DOCS_PER_PAGE = 5


COLLECTION_NAMES = {v: k for k, v in COLLECTION_IDS.items()}
ACCESSIBLE_COLLECTIONS = {
    checks.is_developer: ("development",),
    checks.is_trial_moderator: ("moderators", "moderator wiki"),
    checks.is_moderator: ("moderators", "moderator wiki"),
    checks.is_community_manager: ("management",),
}

DEFAULT_COLLECTION = ACCESSIBLE_COLLECTIONS[checks.is_trial_moderator][0]
