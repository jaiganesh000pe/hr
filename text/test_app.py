import os, tempfile
os.environ.update(APP_PASSWORD="pw", SECRET_KEY="k"*32, DATA_DIR=tempfile.mkdtemp())
os.environ.pop("DATABASE_URL", None)
from fastapi.testclient import TestClient
import main
main.init_db()
c = TestClient(main.app)

def login(): return c.post("/api/login", json={"password": "pw"})

def test_health(): assert c.get("/health").json()["status"] == "ok"
def test_auth_required(): assert TestClient(main.app).get("/api/contractors").status_code == 401
def test_bad_login(): assert TestClient(main.app).post("/api/login", json={"password": "x"}).status_code == 401
def test_contractor_and_candidate_flow():
    assert login().status_code == 200
    k = c.post("/api/contractors", json={"company_name": "Acme Staffing", "state": "TX"}).json()
    r = c.post("/api/candidates", json={"full_name": "Jane Doe", "email": "jane@example.com",
        "phone": "(512) 555-0147", "contractor_id": k["id"]}).json()
    assert r["email_status"] == "Syntax Valid"
    assert c.get("/api/contractors").json()["items"][0]["pool_size"] == 1
    assert c.get(f"/api/candidates?contractor_id={k['id']}").json()["total"] == 1
def test_jd_without_ai_key():
    login(); r = c.post("/api/jd/analyze", json={"text": "x" * 50})
    assert r.status_code == 503 and "Configure AI" in r.json()["detail"]
def test_invalid_stage():
    login(); assert c.patch("/api/candidates/1/stage", json={"stage": "Bogus"}).status_code == 422
