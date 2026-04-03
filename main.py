from fastapi import FastAPI
from app.routes import router
from contextlib import asynccontextmanager
import asyncio

app = FastAPI(title="Welvom AI Audit")
app.include_router(router)

@app.get("/")
def root():
    return {"message": "Welvom AI Audit is running ✅"}