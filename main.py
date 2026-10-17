from fastapi import FastAPI

from db import supabase

app = FastAPI(title="Report EC")


@app.get("/")
def home():
    return {"status": "ok", "message": "Report EC backend is running"}


@app.get("/reports")
def list_reports():
    result = (
        supabase.table("reports")
        .select("*")
        .order("created_at", desc=True)
        .execute()
    )
    return result.data