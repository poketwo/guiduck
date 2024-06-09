from helpers import checks
from config import OUTLINE_COLLECTION_IDS as COLLECTION_IDS


OPTIONS_LIMIT = 25
LINES_PER_PAGE = 15
RESULTS_PER_PAGE = 5

RANKING_THRESHOLD = 0.35
CONTEXT_LIMIT = 100
CONTEXT_HIGHLIGHT_PATTERN = r"<b>(.+?)</b>"


COLLECTION_NAMES = {v: k for k, v in COLLECTION_IDS.items()}
ACCESSIBLE_COLLECTIONS = {
    checks.is_developer: ("development",),
    checks.is_trial_moderator: ("moderators", "moderator wiki"),
    checks.is_moderator: ("moderators", "moderator wiki"),
    checks.is_community_manager: ("management",),
}

DEFAULT_COLLECTION = ACCESSIBLE_COLLECTIONS[checks.is_trial_moderator][0]
