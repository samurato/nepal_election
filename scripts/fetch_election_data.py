#!/usr/bin/env python3
"""
Fetch Nepal election data from result.election.gov.np, merge everything into a
single data/data.json, and write it only when the content has changed.

data.json shape
───────────────
{
  "updated_at": "2026-03-08T07:00:00Z",
  "parties":    [ { party, won, leading, total, symbolId, prVotes } … ],
  "pr_parties": [ { party, symbolId, prVotes } … ],
  "winners":    [ { constituency_no, constituency, district, province,
                    candidate, party, votes, symbolId, candidateId } … ],
  "candidates": [ { province, district, constituency_no, candidate, gender,
                    party, symbol, votes, elected,
                    constituency, candidateId, symbolId } … ]
}

Exit codes
  0  — data.json was updated (caller should commit)
  1  — no changes
  2  — all fetches failed
"""

import re
import json
import hashlib
import datetime
import sys
from pathlib import Path

import requests

# ── Constants ────────────────────────────────────────────────────────────────
BASE         = "https://result.election.gov.np/"
RESULTS      = "https://result.election.gov.np/HouseOfRepresentatives"
CENTRAL_PAGE = "https://result.election.gov.np/ElectionResultCentral2082.aspx"
HANDLER_BASE = "https://result.election.gov.np/Handlers/SecureJson.ashx?file=JSONFiles/Election2082/Common/"
CENTRAL_URL  = (
    "https://result.election.gov.np/Handlers/SecureJson.ashx"
    "?file=JSONFiles/ElectionResultCentral2082.txt"
    "&_search=false&rows=99999&page=1&sidx=_id&sord=asc&filters="
)
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
OUT_FILE = DATA_DIR / "data.json"


# ── Session helpers ───────────────────────────────────────────────────────────

def _extract_csrf(body: str, cookies: dict) -> str:
    csrf = cookies.get("CsrfToken", "")
    if not csrf:
        m = (re.search(r"""['\"]CsrfToken['\"]:\s*['\"]([^'"]+)['\"]""", body) or
             re.search(r"""name=['\"]__RequestVerificationToken['\"][^>]*value=['\"]([^'"]+)['\"]""", body))
        if m:
            csrf = m.group(1)
    return csrf


def build_session(seed_url: str):
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    r = session.get(seed_url, timeout=30)
    csrf = _extract_csrf(r.text, session.cookies.get_dict())
    if not session.cookies.get("ASP.NET_SessionId"):
        session.get(BASE, timeout=20)
        csrf = csrf or _extract_csrf("", session.cookies.get_dict())
    return session, csrf


# ── Fetch helpers ─────────────────────────────────────────────────────────────

def fetch_standard(filename: str, max_attempts: int = 3) -> list | None:
    url = HANDLER_BASE + filename
    for attempt in range(1, max_attempts + 1):
        try:
            session, csrf = build_session(RESULTS)
            r = session.get(url, timeout=30, headers={
                "X-CSRF-Token":     csrf,
                "X-Requested-With": "XMLHttpRequest",
                "Referer":          RESULTS,
                "Accept":           "application/json, text/plain, */*",
            })
            if r.status_code == 200:
                text = r.text.strip()
                if text.startswith(("[", "{")):
                    return json.loads(text)
            print(f"  [{filename}] attempt {attempt}: HTTP {r.status_code} or HTML response", flush=True)
        except Exception as exc:
            print(f"  [{filename}] attempt {attempt} exception: {exc}", flush=True)
    return None


def fetch_central(max_attempts: int = 3) -> dict | None:
    for attempt in range(1, max_attempts + 1):
        try:
            session, csrf = build_session(CENTRAL_PAGE)
            r = session.get(CENTRAL_URL, timeout=60, headers={
                "X-CSRF-Token":     csrf,
                "X-Requested-With": "XMLHttpRequest",
                "Referer":          CENTRAL_PAGE,
                "Accept":           "application/json, text/javascript, */*; q=0.01",
            })
            if r.status_code == 200:
                text = r.text.strip()
                if not text.startswith("<"):
                    return json.loads(text)
            print(f"  [central] attempt {attempt}: HTTP {r.status_code} or HTML response", flush=True)
        except Exception as exc:
            print(f"  [central] attempt {attempt} exception: {exc}", flush=True)
    return None


# ── Row mappers ───────────────────────────────────────────────────────────────

_PREFIX_RE = re.compile(r"^प्रतिनिधि सभा सदस्य निर्वाचन क्षेत्र\s*", re.UNICODE)
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


def normalize_party_key(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip()).casefold()


def to_int(value) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)

    text = str(value).translate(_DEVANAGARI_DIGITS).strip()
    if not text:
        return 0

    text = text.replace(",", "")
    m = re.search(r"-?\d+", text)
    return int(m.group(0)) if m else 0


def map_winner(d: dict) -> dict:
    return {
        "constituency_no": d.get("ScConstId") or d.get("ConstNo") or d.get("ConstId") or
                           d.get("ConstituencyNo") or d.get("HoRConstId") or d.get("ConsNo"),
        "constituency":    _PREFIX_RE.sub("", (
                               d.get("ConstName") or d.get("ScConstName") or
                               d.get("ConstituencyName") or ""
                           )).strip(),
        "district":        d.get("DistrictName", ""),
        "province":        d.get("StateName", ""),
        "candidate":       d.get("CandidateName", ""),
        "party":           d.get("PartyName") or d.get("PoliticalPartyName", ""),
        "votes":           d.get("TotalVote") or 0,
        "symbolId":        d.get("SymbolId") or d.get("SymbolID"),
        "candidateId":     d.get("CandidateId"),
    }


def map_party(d: dict) -> dict:
    return {
        "party":    d.get("PoliticalPartyName", ""),
        "won":      to_int(d.get("TotWin", 0)),
        "leading":  to_int(d.get("TotLead", 0)),
        "total":    to_int(d.get("TotWinLead", 0)),
        "symbolId": d.get("SymbolID") or d.get("SymbolId"),
    }


def map_pr_party(d: dict) -> dict:
    return {
        "party": d.get("PoliticalPartyName") or d.get("PartyName") or "",
        "symbolId": d.get("SymbolID") or d.get("SymbolId"),
        "prVotes": to_int(
            d.get("TotVote")
            or d.get("TotalVote")
            or d.get("TotVotes")
            or d.get("VoteCount")
            or d.get("PRVote")
            or d.get("PRVotes")
            or d.get("PartyVote")
            or d.get("TotalPRVote")
            or d.get("TotPRVote")
            or 0
        ),
    }


def map_central_row(row: dict | list, winner_by_const: dict, winner_by_name: dict) -> dict:
    # Two formats come back from this endpoint:
    #   jqGrid envelope:  {"id": ..., "cell": [province, district, constNo, name, gender, party, symbol, votes, status]}
    #   Plain named dict: {"StateName": ..., "DistrictName": ..., "HoRConstId": ..., "CandidateName": ..., ...}
    if isinstance(row, list):
        c = row
    elif "cell" in row:
        c = row["cell"]
    else:
        # Named-field dict — map directly using actual upstream field names
        const_no  = str(row.get("SCConstID") or row.get("HoRConstId") or row.get("ConsNo") or row.get("ConstNo") or "")
        name      = (row.get("CandidateName") or "").strip()
        ref       = winner_by_const.get(const_no)
        wr        = winner_by_name.get(name)
        votes_raw = row.get("TotalVoteReceived") or row.get("TotalVote") or row.get("TotVotes") or 0
        try: votes = int(float(votes_raw))
        except (ValueError, TypeError): votes = 0
        remarks = str(row.get("Remarks") or row.get("ElectedStatus") or row.get("CandResult") or row.get("Status") or "").strip()
        return {
            "province":        row.get("StateName") or "",
            "district":        row.get("DistrictName") or "",
            "constituency_no": const_no,
            "candidate":       name,
            "gender":          row.get("Gender") or "",
            "party":           row.get("PoliticalPartyName") or row.get("PartyName") or "",
            "symbol":          row.get("SymbolName") or "",
            "votes":           votes,
            "elected":         remarks == "Elected",
            "constituency":    ref["constituency"] if ref else "",
            "candidateId":     int(row.get("CandidateID") or row.get("CandidateId") or 0) or None,
            "symbolId":        int(row.get("SymbolID") or row.get("SymbolId") or 0) or None,
        }

    # cell[] index-based path
    const_no = str(c[2]) if len(c) > 2 else ""
    name     = c[3].strip() if len(c) > 3 else ""

    ref = winner_by_const.get(const_no)
    wr  = winner_by_name.get(name)

    return {
        "province":        c[0] if len(c) > 0 else "",
        "district":        c[1] if len(c) > 1 else "",
        "constituency_no": const_no,
        "candidate":       name,
        "gender":          c[4] if len(c) > 4 else "",
        "party":           c[5] if len(c) > 5 else "",
        "symbol":          c[6] if len(c) > 6 else "",
        "votes":           int(c[7]) if len(c) > 7 and str(c[7]).isdigit() else 0,
        "elected":         (c[8] or "").strip() == "Elected" if len(c) > 8 else False,
        "constituency":    ref["constituency"] if ref else "",
        "candidateId":     wr["candidateId"]   if wr  else None,
        "symbolId":        wr["symbolId"]       if wr  else None,
    }


# ── Hash helper ───────────────────────────────────────────────────────────────

def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    print("Fetching HoRPartyTop5.txt …",   flush=True)
    raw_parties = fetch_standard("HoRPartyTop5.txt")

    print("Fetching HOR-T5Winner.json …",  flush=True)
    raw_winners = fetch_standard("HOR-T5Winner.json")

    print("Fetching ElectionResultCentral2082.txt (all candidates) …", flush=True)
    raw_central = fetch_central()

    print("Fetching PRHoRPartyTop5.txt (party-list votes) …", flush=True)
    raw_pr_parties = fetch_standard("PRHoRPartyTop5.txt")

    if raw_parties is None and raw_winners is None and raw_central is None and raw_pr_parties is None:
        print("✗ All fetches failed.")
        sys.exit(2)

    # Build winners
    winners: list[dict] = [map_winner(d) for d in (raw_winners or [])]
    winner_by_const = {str(w["constituency_no"]): w for w in winners if w["constituency_no"]}
    winner_by_name  = {w["candidate"].strip(): w   for w in winners if w["candidate"]}
    print(f"  winners: {len(winners)}")

    # Build parties
    parties: list[dict] = sorted(
        [map_party(d) for d in (raw_parties if isinstance(raw_parties, list) else [])],
        key=lambda p: p["total"], reverse=True,
    )

    # Build PR party votes (nationwide party-list)
    pr_parties_raw = [
        map_pr_party(d)
        for d in (raw_pr_parties if isinstance(raw_pr_parties, list) else [])
    ]

    pr_votes_by_party: dict[str, int] = {}
    pr_votes_by_symbol: dict[str, int] = {}

    for row in pr_parties_raw:
        name = normalize_party_key(row.get("party", ""))
        votes = to_int(row.get("prVotes", 0))
        symbol = row.get("symbolId")

        if name:
            pr_votes_by_party[name] = pr_votes_by_party.get(name, 0) + votes
        if symbol is not None:
            sk = str(symbol)
            pr_votes_by_symbol[sk] = pr_votes_by_symbol.get(sk, 0) + votes

    for p in parties:
        key = normalize_party_key(p.get("party", ""))
        sym = p.get("symbolId")
        symbol_votes = pr_votes_by_symbol.get(str(sym), 0) if sym is not None else 0
        p["prVotes"] = symbol_votes or pr_votes_by_party.get(key, 0)

    pr_parties = sorted(
        [
            {
                "party": row.get("party", ""),
                "symbolId": row.get("symbolId"),
                "prVotes": to_int(row.get("prVotes", 0)),
            }
            for row in pr_parties_raw
            if row.get("party")
        ],
        key=lambda p: p["prVotes"],
        reverse=True,
    )

    print(f"  parties: {len(parties)}")
    print(f"  pr_parties: {len(pr_parties)}")

    # Build candidates from jqGrid rows (envelope or plain list)
    candidates: list[dict] = []
    if raw_central is not None:
        rows = raw_central.get("rows") if isinstance(raw_central, dict) else raw_central
        if isinstance(rows, list):
            candidates = [
                map_central_row(row, winner_by_const, winner_by_name)
                for row in rows
            ]
            candidates.sort(key=lambda c: (c["constituency_no"], -c["votes"]))
    print(f"  candidates: {len(candidates)}")

    # Merge into single output file
    output = {
        "updated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "parties":    parties,
        "pr_parties": pr_parties,
        "winners":    winners,
        "candidates": candidates,
    }
    content = json.dumps(output, ensure_ascii=False, separators=(",", ":"))

    if OUT_FILE.exists():
        if sha256(OUT_FILE.read_text(encoding="utf-8")) == sha256(content):
            print("\n= No changes — data.json is already up to date.")
            sys.exit(1)

    OUT_FILE.write_text(content, encoding="utf-8")
    kb = len(content.encode("utf-8")) / 1024
    print(f"\n✓ data.json written ({kb:.0f} KB) — parties={len(parties)}  pr_parties={len(pr_parties)}  winners={len(winners)}  candidates={len(candidates)}")
    sys.exit(0)


if __name__ == "__main__":
    main()
