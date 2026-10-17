from fastapi import FastAPI

app = FastAPI(title="Report EC")


@app.get("/")
def home():
    return {"status": "ok", "message": "Report EC backend is running"}