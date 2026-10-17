from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from db import supabase

app = FastAPI(title="Report EC")

# Everything the dashboard is allowed to see. The resident's phone number is
# left out on purpose: the dashboard tracks the issue, not the person.
DASHBOARD_COLUMNS = (
    "id, ref, language, category, description, photo_url, latitude, longitude, "
    "landmark, ward, urgency, status, duplicate_of, created_at, updated_at, resolved_at"
)


@app.get("/")
def home():
    return {"status": "ok", "message": "Report EC backend is running"}


@app.get("/reports")
def list_reports():
    result = (
        supabase.table("reports")
        .select(DASHBOARD_COLUMNS)
        .order("created_at", desc=True)
        .execute()
    )
    return result.data


# Serves the dashboard at http://127.0.0.1:8000/dashboard/
# Keep this line at the bottom so it doesn't hide the API routes above.
app.mount("/dashboard", StaticFiles(directory="static", html=True), name="dashboard")
