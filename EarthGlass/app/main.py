from fastapi import FastAPI

app = FastAPI(title="EarthGlass")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
