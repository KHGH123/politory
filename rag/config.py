import os

from dotenv import load_dotenv

load_dotenv()

# Vertex AI Search
SEARCH_PROJECT_ID = os.getenv("SEARCH_PROJECT_ID")
SEARCH_ENGINE_ID = os.getenv("SEARCH_APP_ID")
SEARCH_LOCATION = os.getenv("SEARCH_LOCATION", "global")
ACTION_SEARCH_ENGINE_ID = (
    os.getenv("ACTION_SEARCH_APP_ID") or "assembly-action-search_1787480152830"
)

# BigQuery
BQ_PROJECT_ID = os.getenv("BQ_PROJECT_ID")
BQ_DATASET = os.getenv("BQ_DATASET", "assembly")
