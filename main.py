"""WEBIFC AI RECRUITER - backend (Phase 1). Sections: Config, Database & Models, Auth,
Helpers, AI Service, API Routes, Static File Serving, Startup."""
# ============================ Config ============================
import os, json, time, hmac, hashlib, base64, logging, secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List
import httpx
from fastapi import FastAPI, Depends, HTTPException, Request, Response, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import (create_engine, String, Integer, Text, DateTime, ForeignKey, Float,
                        Boolean, select, func, or_, text)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker, Session, relationship

VERSION = "0.1.0"
SCHEMA_VERSION = 1
log = logging.getLogger("webifc")
logging.basicConfig(level=logging.INFO)

APP_PASSWORD = os.getenv("APP_PASSWORD", "")
SECRET_KEY = os.getenv("SECRET_KEY", "")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")
_default_dir = "/data" if os.getenv("RAILWAY_ENVIRONMENT") else "./data"
DATA_DIR = Path(os.getenv("DATA_DIR", _default_dir))
SESSION_TTL = 60 * 60 * 12
COOKIE = "webifc_session"

def utcnow(): return datetime.now(timezone.utc)

# ======================= Database & Models ======================
if DATABASE_URL:
    _url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    engine = create_engine(_url, pool_pre_ping=True)
    DB_KIND = "postgres"
else:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "resumes").mkdir(exist_ok=True)
    engine = create_engine(f"sqlite:///{DATA_DIR / 'webifc.db'}", connect_args={"check_same_thread": False})
    DB_KIND = "sqlite"
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

class Base(DeclarativeBase): pass

class Contractor(Base):
    __tablename__ = "contractors"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_name: Mapped[str] = mapped_column(String(255), index=True)
    recruiter_name: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    website: Mapped[Optional[str]] = mapped_column(String(500))
    linkedin_company_url: Mapped[Optional[str]] = mapped_column(String(500))
    linkedin_recruiter_url: Mapped[Optional[str]] = mapped_column(String(500))
    email: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    phone: Mapped[Optional[str]] = mapped_column(String(50))
    city: Mapped[Optional[str]] = mapped_column(String(100))
    state: Mapped[Optional[str]] = mapped_column(String(50), index=True)
    source: Mapped[Optional[str]] = mapped_column(String(100))
    verification_status: Mapped[str] = mapped_column(String(30), default="Unverified")
    tags: Mapped[Optional[str]] = mapped_column(String(500))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    last_contacted: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class Candidate(Base):
    __tablename__ = "candidates"
    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(255), index=True)
    title: Mapped[Optional[str]] = mapped_column(String(255))
    current_company: Mapped[Optional[str]] = mapped_column(String(255))
    skills: Mapped[Optional[str]] = mapped_column(Text)  # comma separated
    years_experience: Mapped[Optional[float]] = mapped_column(Float)
    city: Mapped[Optional[str]] = mapped_column(String(100))
    state: Mapped[Optional[str]] = mapped_column(String(50), index=True)
    linkedin_url: Mapped[Optional[str]] = mapped_column(String(500), index=True)
    github_url: Mapped[Optional[str]] = mapped_column(String(500))
    email: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    email_status: Mapped[str] = mapped_column(String(30), default="Not Available")
    phone: Mapped[Optional[str]] = mapped_column(String(50), index=True)
    phone_status: Mapped[str] = mapped_column(String(30), default="Not Available")
    contact_source: Mapped[Optional[str]] = mapped_column(String(255))
    work_authorization: Mapped[str] = mapped_column(String(40), default="Unknown")
    availability: Mapped[str] = mapped_column(String(30), default="Unknown")
    availability_source: Mapped[Optional[str]] = mapped_column(String(255))
    source: Mapped[Optional[str]] = mapped_column(String(100))
    stage: Mapped[str] = mapped_column(String(40), default="New", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

class Association(Base):
    """Contractor <-> Candidate. Many-to-many, history preserved (never exclusive)."""
    __tablename__ = "contractor_candidate_associations"
    id: Mapped[int] = mapped_column(primary_key=True)
    contractor_id: Mapped[int] = mapped_column(ForeignKey("contractors.id"), index=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    origin: Mapped[Optional[str]] = mapped_column(String(100))
    submitted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class JobDescription(Base):
    __tablename__ = "job_descriptions"
    id: Mapped[int] = mapped_column(primary_key=True)
    raw_text: Mapped[str] = mapped_column(Text)
    analysis_json: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    detail: Mapped[Optional[str]] = mapped_column(Text)
    ip: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class AppSetting(Base):
    __tablename__ = "application_settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Optional[str]] = mapped_column(Text)

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

def audit(db: Session, action: str, detail: str = "", request: Request = None):
    db.add(AuditLog(action=action, detail=detail, ip=request.client.host if request and request.client else None))
    db.commit()

def init_db():
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        row = db.get(AppSetting, "schema_version")
        if not row:
            db.add(AppSetting(key="schema_version", value=str(SCHEMA_VERSION))); db.commit()
    if DB_KIND == "sqlite":
        log.info("Database: SQLite at %s", DATA_DIR / "webifc.db")
        if os.getenv("RAILWAY_ENVIRONMENT") and not os.path.ismount(str(DATA_DIR)):
            log.warning("SQLite is NOT on a mounted volume: data will be LOST on redeploy. Add a Railway Volume at %s.", DATA_DIR)
    else:
        log.info("Database: PostgreSQL via DATABASE_URL")

# ============================== Auth ============================
_fail: dict = {}  # ip -> (count, locked_until)
MAX_FAILS, LOCK_SECONDS = 5, 15 * 60

def _sign(payload: str) -> str:
    return hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()

def make_token() -> str:
    exp = str(int(time.time()) + SESSION_TTL)
    return base64.urlsafe_b64encode(f"{exp}.{_sign(exp)}".encode()).decode()

def valid_token(tok: Optional[str]) -> bool:
    if not tok or not SECRET_KEY: return False
    try:
        exp, sig = base64.urlsafe_b64decode(tok.encode()).decode().split(".", 1)
        return hmac.compare_digest(sig, _sign(exp)) and int(exp) > time.time()
    except Exception:
        return False

def require_auth(request: Request):
    if not valid_token(request.cookies.get(COOKIE)):
        raise HTTPException(401, "Not authenticated")

class LoginIn(BaseModel):
    password: str = Field(min_length=1, max_length=200)

# ============================ Helpers ===========================
import email_validator, phonenumbers

def validate_email(addr: Optional[str]) -> str:
    """Returns Syntax Valid / Invalid / Not Available. Never 'Verified' from format alone."""
    if not addr: return "Not Available"
    try:
        email_validator.validate_email(addr, check_deliverability=False); return "Syntax Valid"
    except Exception: return "Invalid"

def normalize_phone(num: Optional[str]):
    if not num: return None, "Not Available"
    try:
        p = phonenumbers.parse(num, "US")
        if phonenumbers.is_valid_number(p):
            return phonenumbers.format_number(p, phonenumbers.PhoneNumberFormat.E164), "Valid Format"
        return num, "Invalid"
    except Exception: return num, "Invalid"

def page(db: Session, stmt, model, limit: int, offset: int):
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(model.id.desc()).limit(limit).offset(offset)).all()
    return total, rows

def row_dict(o): return {c.name: (getattr(o, c.name).isoformat() if isinstance(getattr(o, c.name), datetime) else getattr(o, c.name)) for c in o.__table__.columns}

# =========================== AI Service =========================
class JDAnalysis(BaseModel):
    job_title: str = ""
    alternative_titles: List[str] = []
    required_skills: List[str] = []
    preferred_skills: List[str] = []
    languages: List[str] = []
    frameworks: List[str] = []
    tools: List[str] = []
    technologies: List[str] = []
    experience_years: Optional[float] = None
    seniority: str = ""
    education: str = ""
    certifications: List[str] = []
    city: str = ""
    state: str = ""
    work_mode: str = ""
    employment_type: str = ""
    required_qualifications: List[str] = []
    preferred_qualifications: List[str] = []
    search_keywords: List[str] = []
    boolean_queries: List[str] = []
    natural_queries: List[str] = []

def ai_configured() -> bool: return bool(LLM_API_KEY)

async def llm_json(system: str, user: str) -> dict:
    if not ai_configured():
        raise HTTPException(503, "Configure AI provider in Settings (set LLM_API_KEY in Railway variables).")
    async with httpx.AsyncClient(timeout=60) as c:
        for attempt in range(3):
            try:
                r = await c.post(f"{LLM_BASE_URL}/chat/completions",
                    headers={"Authorization": f"Bearer {LLM_API_KEY}"},
                    json={"model": LLM_MODEL, "response_format": {"type": "json_object"},
                          "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
                r.raise_for_status()
                return json.loads(r.json()["choices"][0]["message"]["content"])
            except (httpx.HTTPError, KeyError, json.JSONDecodeError) as e:
                if attempt == 2: raise HTTPException(502, f"AI provider error: {type(e).__name__}")
                await _sleep(2 ** attempt)

async def _sleep(s):
    import asyncio; await asyncio.sleep(s)

JD_SYSTEM = ("You extract structured data from US job descriptions. Return ONLY JSON with keys: "
  + ", ".join(JDAnalysis.model_fields.keys()) + ". Do not invent facts; use empty values when absent. "
  "boolean_queries and natural_queries must be search queries for finding US staffing companies and recruiters first, then candidates.")

# =========================== API Routes =========================
app = FastAPI(title="WEBIFC AI Recruiter", version=VERSION)

@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.update({"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
        "Referrer-Policy": "same-origin", "Cache-Control": "no-store" if request.url.path.startswith("/api") else "no-cache"})
    return resp

@app.get("/health")
def health():
    ok = True
    try:
        with engine.connect() as c: c.execute(text("SELECT 1"))
    except Exception: ok = False
    return JSONResponse({"status": "ok" if ok else "degraded", "database": DB_KIND, "db_connected": ok, "version": VERSION},
                        status_code=200 if ok else 503)

@app.post("/api/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else "?"
    cnt, until = _fail.get(ip, (0, 0))
    if until > time.time(): raise HTTPException(429, "Too many failed attempts. Try again later.")
    if not APP_PASSWORD or not SECRET_KEY: raise HTTPException(500, "Server not configured: set APP_PASSWORD and SECRET_KEY.")
    if not hmac.compare_digest(body.password.encode(), APP_PASSWORD.encode()):
        cnt += 1
        _fail[ip] = (cnt, time.time() + LOCK_SECONDS if cnt >= MAX_FAILS else 0)
        audit(db, "login_failed", "", request)
        raise HTTPException(401, "Invalid password")
    _fail.pop(ip, None)
    response.set_cookie(COOKIE, make_token(), httponly=True, samesite="strict",
                        secure=request.url.scheme == "https", max_age=SESSION_TTL)
    audit(db, "login", "", request)
    return {"ok": True}

@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE); return {"ok": True}

@app.get("/api/me", dependencies=[Depends(require_auth)])
def me(): return {"authenticated": True}

@app.get("/api/settings/status", dependencies=[Depends(require_auth)])
def settings_status():
    cfg = lambda v: "configured" if v else "not configured"
    return {"version": VERSION, "database": DB_KIND, "data_dir": str(DATA_DIR),
            "llm": cfg(LLM_API_KEY), "llm_model": LLM_MODEL if LLM_API_KEY else None,
            "embeddings": cfg(EMBEDDING_MODEL and LLM_API_KEY),
            "matching_mode": "embeddings" if (EMBEDDING_MODEL and LLM_API_KEY) else "keyword/TF-IDF fallback",
            "github": cfg(GITHUB_TOKEN),
            "volume_mounted": os.path.ismount(str(DATA_DIR)) if DB_KIND == "sqlite" else None}

# ---- Contractors ----
class ContractorIn(BaseModel):
    company_name: str = Field(min_length=1, max_length=255)
    recruiter_name: Optional[str] = None
    website: Optional[str] = None
    linkedin_company_url: Optional[str] = None
    linkedin_recruiter_url: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    source: Optional[str] = "Manual"
    tags: Optional[str] = None
    notes: Optional[str] = None

@app.get("/api/contractors", dependencies=[Depends(require_auth)])
def list_contractors(q: str = "", state: str = "", limit: int = Query(25, le=200), offset: int = 0, db: Session = Depends(get_db)):
    stmt = select(Contractor)
    if q: stmt = stmt.where(or_(Contractor.company_name.ilike(f"%{q}%"), Contractor.recruiter_name.ilike(f"%{q}%")))
    if state: stmt = stmt.where(Contractor.state == state)
    total, rows = page(db, stmt, Contractor, limit, offset)
    out = []
    for r in rows:
        d = row_dict(r)
        d["pool_size"] = db.scalar(select(func.count()).select_from(Association).where(Association.contractor_id == r.id))
        out.append(d)
    return {"total": total, "items": out}

@app.post("/api/contractors", dependencies=[Depends(require_auth)], status_code=201)
def create_contractor(body: ContractorIn, request: Request, db: Session = Depends(get_db)):
    c = Contractor(**body.model_dump()); db.add(c); db.commit()
    audit(db, "contractor_create", c.company_name, request); return row_dict(c)

@app.put("/api/contractors/{cid}", dependencies=[Depends(require_auth)])
def update_contractor(cid: int, body: ContractorIn, db: Session = Depends(get_db)):
    c = db.get(Contractor, cid)
    if not c: raise HTTPException(404, "Not found")
    for k, v in body.model_dump().items(): setattr(c, k, v)
    db.commit(); return row_dict(c)

@app.delete("/api/contractors/{cid}", dependencies=[Depends(require_auth)])
def delete_contractor(cid: int, request: Request, db: Session = Depends(get_db)):
    c = db.get(Contractor, cid)
    if not c: raise HTTPException(404, "Not found")
    for a in db.scalars(select(Association).where(Association.contractor_id == cid)): db.delete(a)
    db.delete(c); db.commit(); audit(db, "contractor_delete", str(cid), request); return {"ok": True}

# ---- Candidates ----
class CandidateIn(BaseModel):
    full_name: str = Field(min_length=1, max_length=255)
    title: Optional[str] = None
    current_company: Optional[str] = None
    skills: Optional[str] = None
    years_experience: Optional[float] = Field(None, ge=0, le=70)
    city: Optional[str] = None
    state: Optional[str] = None
    linkedin_url: Optional[str] = None
    github_url: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    contact_source: Optional[str] = None
    work_authorization: str = "Unknown"
    availability: str = "Unknown"
    availability_source: Optional[str] = None
    source: Optional[str] = "Manual"
    contractor_id: Optional[int] = None

def _apply_contact(c: Candidate):
    c.email_status = validate_email(c.email)
    c.phone, c.phone_status = normalize_phone(c.phone)

@app.get("/api/candidates", dependencies=[Depends(require_auth)])
def list_candidates(q: str = "", state: str = "", contractor_id: Optional[int] = None, availability: str = "",
                    limit: int = Query(25, le=200), offset: int = 0, db: Session = Depends(get_db)):
    stmt = select(Candidate)
    if q: stmt = stmt.where(or_(Candidate.full_name.ilike(f"%{q}%"), Candidate.skills.ilike(f"%{q}%"), Candidate.title.ilike(f"%{q}%")))
    if state: stmt = stmt.where(Candidate.state == state)
    if availability: stmt = stmt.where(Candidate.availability == availability)
    if contractor_id: stmt = stmt.where(Candidate.id.in_(select(Association.candidate_id).where(Association.contractor_id == contractor_id)))
    total, rows = page(db, stmt, Candidate, limit, offset)
    return {"total": total, "items": [row_dict(r) for r in rows]}

@app.post("/api/candidates", dependencies=[Depends(require_auth)], status_code=201)
def create_candidate(body: CandidateIn, request: Request, db: Session = Depends(get_db)):
    data = body.model_dump(); cid = data.pop("contractor_id")
    c = Candidate(**data); _apply_contact(c); db.add(c); db.commit()
    if cid: db.add(Association(contractor_id=cid, candidate_id=c.id, origin=c.source)); db.commit()
    audit(db, "candidate_create", c.full_name, request); return row_dict(c)

@app.delete("/api/candidates/{cid}", dependencies=[Depends(require_auth)])
def delete_candidate(cid: int, request: Request, db: Session = Depends(get_db)):
    c = db.get(Candidate, cid)
    if not c: raise HTTPException(404, "Not found")
    for a in db.scalars(select(Association).where(Association.candidate_id == cid)): db.delete(a)
    db.delete(c); db.commit(); audit(db, "candidate_delete", str(cid), request); return {"ok": True}

class StageIn(BaseModel):
    stage: str

STAGES = ["New","Discovered","Contact Verification","Shortlisted","Contacted","Responded","Interested",
          "Interview Scheduled","Interview Completed","Submitted to Client","Hired","Rejected"]

@app.patch("/api/candidates/{cid}/stage", dependencies=[Depends(require_auth)])
def set_stage(cid: int, body: StageIn, db: Session = Depends(get_db)):
    if body.stage not in STAGES: raise HTTPException(422, "Invalid stage")
    c = db.get(Candidate, cid)
    if not c: raise HTTPException(404, "Not found")
    c.stage = body.stage; db.commit(); return row_dict(c)

@app.get("/api/dashboard", dependencies=[Depends(require_auth)])
def dashboard(db: Session = Depends(get_db)):
    n = lambda m, *w: db.scalar(select(func.count()).select_from(m).where(*w))
    return {"contractors": n(Contractor), "candidates": n(Candidate),
            "open_to_work": n(Candidate, Candidate.availability.in_(["Explicit", "Public Signal"])),
            "verified_contacts": n(Candidate, or_(Candidate.email_status == "Verified", Candidate.phone_status == "Verified")),
            "pipeline": {s: n(Candidate, Candidate.stage == s) for s in STAGES}}

# ---- JD Analyzer ----
class JDIn(BaseModel):
    text: str = Field(min_length=30, max_length=30000)

@app.post("/api/jd/analyze", dependencies=[Depends(require_auth)])
async def analyze_jd(body: JDIn, db: Session = Depends(get_db)):
    raw = await llm_json(JD_SYSTEM, body.text)
    try: result = JDAnalysis(**raw)
    except ValidationError as e: raise HTTPException(502, f"AI returned invalid structure: {e.error_count()} errors")
    jd = JobDescription(raw_text=body.text, analysis_json=result.model_dump_json()); db.add(jd); db.commit()
    return {"id": jd.id, "analysis": result.model_dump()}

@app.get("/api/jd", dependencies=[Depends(require_auth)])
def list_jds(limit: int = Query(25, le=100), offset: int = 0, db: Session = Depends(get_db)):
    total, rows = page(db, select(JobDescription), JobDescription, limit, offset)
    return {"total": total, "items": [{"id": r.id, "created_at": r.created_at.isoformat(),
            "analysis": json.loads(r.analysis_json or "{}")} for r in rows]}

# ======================= Static File Serving ====================
@app.get("/")
def root(): return FileResponse(Path(__file__).parent / "index.html")

# ============================ Startup ===========================
@app.on_event("startup")
def on_startup():
    if not APP_PASSWORD or not SECRET_KEY:
        log.warning("APP_PASSWORD and/or SECRET_KEY not set: login is disabled until configured.")
    init_db()
