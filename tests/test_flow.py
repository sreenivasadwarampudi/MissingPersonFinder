"""End-to-end test of the whole reunification flow, including the privacy rules."""
import io

import numpy as np
from PIL import Image


def make_photo(seed: int, size: int = 256) -> bytes:
    """A smooth random 'portrait-ish' picture; different seeds look different to the mock face backend."""
    rng = np.random.default_rng(seed)
    small = (rng.random((8, 8, 3)) * 255).astype("uint8")
    img = Image.fromarray(small).resize((size, size), Image.BICUBIC)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


_tokens: dict[str, dict] = {}


def login(client, email, privacy=True):
    # The DB persists across tests and OTP requests are throttled per email (1/min),
    # so reuse the session token when the same account appears in several tests.
    if email in _tokens:
        return _tokens[email]
    r = client.post("/api/auth/request-otp", json={"email": email})
    assert r.status_code == 200, r.text
    code = r.json()["dev_otp"]
    r = client.post("/api/auth/verify-otp", json={"email": email, "code": code, "accept_privacy": privacy})
    assert r.status_code == 200, r.text
    _tokens[email] = {"Authorization": f"Bearer {r.json()['token']}"}
    return _tokens[email]


def upload_missing(client, headers, photo, name="Asha", consent="true"):
    return client.post(
        "/api/missing",
        headers=headers,
        files={"photo": ("p.png", photo, "image/png")},
        data={"full_name": name, "age": "9", "last_seen_place": "Hyderabad", "consent": consent},
    )


def upload_sighting(client, headers, photo, lat="17.385", lng="78.4867"):
    return client.post(
        "/api/sightings",
        headers=headers,
        files={"photo": ("s.png", photo, "image/png")},
        data={"lat": lat, "lng": lng, "location_source": "browser", "note": "near the bus stand", "consent": "true"},
    )


def test_full_flow_and_privacy(client):
    family = login(client, "family@example.org")
    finder = login(client, "finder@example.org")
    mod = login(client, "mod@example.org")
    stranger = login(client, "stranger@example.org")
    photo = make_photo(1)

    # 1. family registers a missing person; it is processed in the background
    r = upload_missing(client, family, photo)
    assert r.status_code == 201
    mid = r.json()["id"]
    rec = client.get(f"/api/missing/{mid}", headers=family).json()
    assert rec["status"] == "pending_review" and rec["has_restored"] is True

    # not matchable until a moderator approves (stops fake "missing" records used for stalking)
    r = upload_sighting(client, finder, photo)
    assert r.status_code == 201
    assert client.get("/api/admin/matches", headers=mod).json() == []

    # 2. moderator approves the record -> the earlier sighting is matched against it
    r = client.get("/api/admin/missing", headers=mod)
    assert [m["id"] for m in r.json()] == [mid]
    r = client.post(f"/api/admin/missing/{mid}/decision", headers=mod, json={"decision": "approve"})
    assert r.status_code == 200 and r.json()["new_matches"] == 1

    # 3. a different sighting of a different person must NOT match
    upload_sighting(client, finder, make_photo(99))
    pending = client.get("/api/admin/matches", headers=mod).json()
    assert len(pending) == 1 and pending[0]["score"] > 0.9

    # 4. family sees only "under review": no location, no score, no photo access
    mine = client.get("/api/matches/mine", headers=family).json()
    assert len(mine) == 1 and mine[0]["status"] == "pending"
    assert "sighting" not in mine[0] and "score" not in mine[0]
    sid = pending[0]["sighting"]["id"]
    assert client.get(f"/api/files/sighting/{sid}/original", headers=family).status_code == 404

    # the uploader's own view never leaks match info or the family
    s_mine = client.get("/api/sightings/mine", headers=finder).json()
    assert all("lat" not in s and "score" not in s for s in s_mine)

    # 5. moderator confirms -> only now does the family get the exact location + photos
    mid_match = pending[0]["id"]
    r = client.post(
        f"/api/admin/matches/{mid_match}/decision",
        headers=mod,
        json={"decision": "confirm", "note": "same scar on chin", "routed_to": "Local NGO"},
    )
    assert r.status_code == 200
    mine = client.get("/api/matches/mine", headers=family).json()
    assert mine[0]["status"] == "confirmed"
    assert abs(mine[0]["sighting"]["lat"] - 17.385) < 1e-6
    assert mine[0]["routed_to"] == "Local NGO"
    assert client.get(f"/api/files/sighting/{sid}/original", headers=family).status_code == 200
    assert client.get(f"/api/files/sighting/{sid}/restored", headers=family).status_code == 200

    # strangers see nothing
    assert client.get(f"/api/files/sighting/{sid}/original", headers=stranger).status_code == 404
    assert client.get(f"/api/files/missing/{mid}/original", headers=stranger).status_code == 404
    assert client.get("/api/matches/mine", headers=stranger).json() == []

    # the uploader can never see the family's photo
    assert client.get(f"/api/files/missing/{mid}/original", headers=finder).status_code == 404

    # 6. the action trail exists
    actions = [a["action"] for a in client.get("/api/admin/audit", headers=mod).json()]
    assert "match.confirm" in actions and "missing.approve" in actions

    # 7. family says reunited -> record closes
    assert client.post(f"/api/matches/{mid_match}/feedback", headers=family, json={"outcome": "reunited"}).status_code == 200
    assert client.get(f"/api/missing/{mid}", headers=family).json()["status"] == "found"


def test_access_control(client):
    assert client.get("/api/missing/mine").status_code == 401
    user = login(client, "plain@example.org")
    assert client.get("/api/admin/stats", headers=user).status_code == 403
    assert client.get("/api/admin/matches", headers=user).status_code == 403


def test_consent_and_validation(client):
    user = login(client, "consent@example.org")
    assert upload_missing(client, user, make_photo(3), consent="false").status_code == 400
    r = client.post(
        "/api/sightings",
        headers=user,
        files={"photo": ("s.png", make_photo(3), "image/png")},
        data={"consent": "true", "lat": "10"},  # lat without lng
    )
    assert r.status_code == 400
    r = client.post(
        "/api/missing",
        headers=user,
        files={"photo": ("x.png", b"not an image", "image/png")},
        data={"full_name": "X", "consent": "true"},
    )
    assert r.status_code == 400


def test_otp_security(client):
    # new user must accept the privacy policy, and the code survives that failed attempt
    r = client.post("/api/auth/request-otp", json={"email": "newbie@example.org"})
    code = r.json()["dev_otp"]
    r = client.post("/api/auth/verify-otp", json={"email": "newbie@example.org", "code": code, "accept_privacy": False})
    assert r.status_code == 409 and r.json()["detail"] == "privacy_consent_required"
    r = client.post("/api/auth/verify-otp", json={"email": "newbie@example.org", "code": code, "accept_privacy": True})
    assert r.status_code == 200
    # a used code cannot be replayed
    r = client.post("/api/auth/verify-otp", json={"email": "newbie@example.org", "code": code, "accept_privacy": True})
    assert r.status_code == 400

    # brute force: five wrong guesses lock the code even if the 6th guess is right
    r = client.post("/api/auth/request-otp", json={"email": "victim@example.org"})
    good = r.json()["dev_otp"]
    wrong = "000000" if good != "000000" else "111111"
    for _ in range(5):
        assert client.post("/api/auth/verify-otp", json={"email": "victim@example.org", "code": wrong, "accept_privacy": True}).status_code == 400
    assert client.post("/api/auth/verify-otp", json={"email": "victim@example.org", "code": good, "accept_privacy": True}).status_code == 400

    # one code per minute per email
    assert client.post("/api/auth/request-otp", json={"email": "victim@example.org"}).status_code == 429


def test_rate_limit_on_sightings(client):
    user = login(client, "spammer@example.org")
    codes = [upload_sighting(client, user, make_photo(i + 10)).status_code for i in range(11)]
    assert codes[:10] == [201] * 10 and codes[10] == 429


def test_report_and_takedown(client):
    finder = login(client, "finder2@example.org")
    mod = login(client, "mod@example.org")
    sid = upload_sighting(client, finder, make_photo(5)).json()["id"]

    # anyone can report, no account needed
    r = client.post("/api/reports", json={"target_type": "sighting", "target_id": sid, "reason": "This is my photo, I did not consent."})
    assert r.status_code == 202
    reports = client.get("/api/admin/reports", headers=mod).json()
    assert len(reports) == 1

    r = client.post(f"/api/admin/reports/{reports[0]['id']}/resolve", headers=mod, json={"action": "takedown"})
    assert r.status_code == 200
    assert client.get(f"/api/sightings/{sid}", headers=finder).status_code == 404
    assert client.get(f"/api/files/sighting/{sid}/original", headers=mod).status_code == 404


def test_user_can_erase_account(client):
    user = login(client, "leaving@example.org")
    mid = upload_missing(client, user, make_photo(7)).json()["id"]
    assert client.delete("/api/auth/me", headers=user).status_code == 204
    mod = login(client, "mod@example.org")
    assert client.get(f"/api/files/missing/{mid}/original", headers=mod).status_code == 404
    assert client.get("/api/auth/me", headers=user).status_code == 401


def test_exif_gps_fallback(client):
    """No browser GPS -> location is read from the photo's EXIF, then stripped from the stored copy."""
    finder = login(client, "exif@example.org")
    mod = login(client, "mod@example.org")
    img = Image.fromarray((np.random.default_rng(8).random((8, 8, 3)) * 255).astype("uint8")).resize((256, 256), Image.BICUBIC)
    exif = Image.Exif()
    gps = {1: "N", 2: (17.0, 23.0, 6.0), 3: "E", 4: (78.0, 29.0, 12.0)}
    exif[0x8825] = gps
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif)
    r = client.post(
        "/api/sightings",
        headers=finder,
        files={"photo": ("g.jpg", buf.getvalue(), "image/jpeg")},
        data={"consent": "true"},
    )
    assert r.status_code == 201 and r.json()["location_source"] == "exif"
    sid = r.json()["id"]
    stored = client.get(f"/api/files/sighting/{sid}/original", headers=mod).content
    assert not Image.open(io.BytesIO(stored)).getexif().get_ifd(0x8825)  # metadata stripped
