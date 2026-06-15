from fastapi import FastAPI
from app.api.gps import router as gps_router

app = FastAPI()
app.include_router(gps_router)

@app.get("/")
def root():
    return {"status": "PathGuard API is running"}