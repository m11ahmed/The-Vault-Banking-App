from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

PROJECT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_DIR / ".env", override=False)

from urllib.parse import urlsplit
from fastapi import Request
from fastapi.responses import JSONResponse
from .database import initialize_database
from .routers import accounts, transactions, kyc, assistant, kyc_documents, auth

# Initialize the schema and migrate the supplied SQLite starter with a backup.
initialize_database()

app = FastAPI(
    title="Mini Core Banking API",
    description=(
        "A simplified core banking system — accounts, transactions, and KYC — "
        "built as a portfolio project demonstrating BFS-domain backend engineering."
    ),
    version="2.0.0",
)

@app.middleware('http')
async def request_controls(request: Request, call_next):
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        origin = request.headers.get('origin')
        expected = urlsplit(str(request.base_url))
        if origin and (urlsplit(origin).scheme, urlsplit(origin).netloc) != (expected.scheme, expected.netloc):
            return JSONResponse(status_code=403, content={'detail': 'Use the same-origin dashboard for browser requests.'})
        if request.headers.get('sec-fetch-site') == 'cross-site':
            return JSONResponse(status_code=403, content={'detail': 'Cross-site requests are not permitted.'})
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    if request.url.path.startswith(('/auth', '/accounts', '/transfer', '/kyc', '/assistant/chat')):
        response.headers['Cache-Control'] = 'no-store'
    return response


app.include_router(auth.router)
app.include_router(accounts.router)
app.include_router(transactions.router)
app.include_router(kyc.router)
app.include_router(assistant.router)
app.include_router(kyc_documents.router)
app.mount("/static", StaticFiles(directory=PROJECT_DIR / "app" / "static"), name="static")
app.mount("/samples", StaticFiles(directory=PROJECT_DIR / "samples"), name="samples")


@app.get("/demo", include_in_schema=False)
def demo_dashboard():
    return FileResponse(PROJECT_DIR / "app" / "static" / "index.html")


@app.get("/")
def root():
    return {"message": "Core Banking API is running. Visit /demo for the dashboard or /docs for interactive API docs."}


@app.get('/health', tags=['Health'])
def health():
    return {'status': 'ok', 'version': '2.0.0'}
