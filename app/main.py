from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from app.routes import router

app = FastAPI(title="Welvom AI Audit")
app.include_router(router)


@app.get("/")
def root():
    return RedirectResponse(url="/questionnaire", status_code=307)
