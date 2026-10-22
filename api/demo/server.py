# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""In-memory demo API. Run: python -m uvicorn demo.server:app --port 8000 (from the api folder)."""

import re
from difflib import SequenceMatcher
from typing import Any

from fastapi import Depends, FastAPI, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.core.errors import (
    Conflict,
    NotFound,
    OutOfScope,
    PermissionDenied,
    register_error_handlers,
)
from app.core.time import utc_now
from app.platform.permissions.guard import Effect, require
from app.platform.permissions.keys import KEYS
from app.platform.permissions.matrix import seed_table
from app.platform.permissions.roles import Role, Scope
from app.platform.reasons.policy import check as check_reason

TABLE = seed_table()
DEFAULT_CAP = 3

app = FastAPI(title="Relay CRM demo")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
register_error_handlers(app)

S: dict[str, Any] = {}


def now() -> str:
    return utc_now().isoformat()


def reset() -> None:
    teams = {
        "north": {"id": "north", "name": "Team North"},
        "south": {"id": "south", "name": "Team South"},
    }
    users = [
        ("sa", "Samar (Super admin)", Role.SUPER_ADMIN, None),
        ("admin", "Anita (Admin)", Role.ADMIN, None),
        ("tl_n", "Tara (TL North)", Role.TEAM_LEADER, "north"),
        ("tl_s", "Tejas (TL South)", Role.TEAM_LEADER, "south"),
        ("u_n1", "Priya (North rep)", Role.USER, "north"),
        ("u_n2", "Rahul (North rep)", Role.USER, "north"),
        ("u_s1", "Sneha (South rep)", Role.USER, "south"),
        ("u_s2", "Vikram (South rep)", Role.USER, "south"),
    ]
    S.clear()
    S.update(
        teams=teams,
        users={
            i: {"id": i, "name": n, "role": r, "team_id": t, "active": True, "cap": DEFAULT_CAP}
            for i, n, r, t in users
        },
        accounts={},
        deals={},
        audit=[],
        notifications=[],
        approvals={},
        pointers={},
        seq=0,
        default_team="north",
        rules=[
            {
                "id": "r1",
                "name": "Referral leads -> Team North",
                "priority": 1,
                "match": {"lead_source": "Referral"},
                "team_id": "north",
            },
            {
                "id": "r2",
                "name": "Food & Beverage -> Team South",
                "priority": 2,
                "match": {"industry": "Food & Beverage"},
                "team_id": "south",
            },
        ],
    )


reset()


def nid(prefix: str) -> str:
    S["seq"] += 1
    return f"{prefix}{S['seq']}"


class Actor(BaseModel):
    id: str


def current_user(x_user_id: str = Header(default="sa")) -> dict[str, Any]:
    user = S["users"].get(x_user_id)
    if not user:
        raise NotFound("unknown user")
    return user  # type: ignore[no-any-return]


User = Depends(current_user)


def audit(
    actor: dict[str, Any] | None,
    key: str,
    entity: str,
    entity_id: str,
    *,
    outcome: str = "ok",
    old: Any = None,
    new: Any = None,
    reason: str | None = None,
    origin: str = "screen",
    note: str | None = None,
) -> None:
    S["audit"].append(
        {
            "id": nid("a"),
            "at": now(),
            "actor": actor["name"] if actor else "System",
            "actor_role": actor["role"] if actor else "system",
            "action_key": key,
            "entity": entity,
            "entity_id": entity_id,
            "outcome": outcome,
            "old": old,
            "new": new,
            "reason": reason,
            "origin": origin,
            "note": note,
        }
    )


def notify(user_id: str | None, kind: str, text: str) -> None:
    if user_id:
        S["notifications"].append(
            {"id": nid("n"), "user_id": user_id, "type": kind, "text": text, "at": now()}
        )


def team_name(tid: str | None) -> str | None:
    return S["teams"][tid]["name"] if tid else None


def user_name(uid: str | None) -> str | None:
    return S["users"][uid]["name"] if uid else None


def in_scope(user: dict[str, Any], scope: Scope, deal: dict[str, Any]) -> bool:
    if scope == Scope.ALL:
        return True
    if scope == Scope.TEAM:
        return deal["team_id"] == user["team_id"]
    if scope == Scope.OWN:
        return deal["owner_id"] == user["id"]
    return False


def authorize(user: dict[str, Any], key: str, deal: dict[str, Any] | None = None) -> Any:
    own = None if deal is None else deal["owner_id"] == user["id"]
    decision = require(TABLE, user["role"], key, is_own_record=own)
    if deal is not None and not in_scope(user, decision.scope, deal):
        raise OutOfScope("This record is outside your scope")
    return decision


def reason_for(user: dict[str, Any], key: str, reason: str | None) -> str | None:
    return check_reason(user["role"], KEYS[key].action_class, reason)


def active_leads(uid: str) -> int:
    return sum(1 for d in S["deals"].values() if d["owner_id"] == uid and d["status"] == "open")


def view_deal(d: dict[str, Any]) -> dict[str, Any]:
    acc = S["accounts"][d["account_id"]]
    return {
        **d,
        "company": acc["name"],
        "domain": acc["domain"],
        "industry": acc["industry"],
        "owner_name": user_name(d["owner_id"]),
        "team_name": team_name(d["team_id"]),
        "assigned_by_name": d["assigned_by"],
    }


# ---------------------------------------------------------------- assignment engine
def rule_matches(rule: dict[str, Any], deal: dict[str, Any]) -> bool:
    acc = S["accounts"][deal["account_id"]]
    facts = {"lead_source": deal["lead_source"], "industry": acc["industry"], "city": acc["city"]}
    return all(facts.get(k) == v for k, v in rule["match"].items())


def run_engine(deal: dict[str, Any], origin: str = "system_job") -> bool:
    """Rules first, then round-robin among active reps only, skipping reps at cap."""
    if deal["status"] != "pooled":
        return False
    rule = next(
        (r for r in sorted(S["rules"], key=lambda r: r["priority"]) if rule_matches(r, deal)), None
    )
    team_id = rule["team_id"] if rule else S["default_team"]
    deal["team_id"] = team_id
    reps = sorted(
        (u for u in S["users"].values() if u["role"] == Role.USER and u["team_id"] == team_id),
        key=lambda u: u["id"],
    )
    active = [u for u in reps if u["active"]]
    last = S["pointers"].get(team_id)
    ids = [u["id"] for u in active]
    start = (ids.index(last) + 1) if last in ids else 0
    ordered = active[start:] + active[:start]
    pick = next((u for u in ordered if active_leads(u["id"]) < u["cap"]), None)
    if pick is None:
        reason = "no_active_reps" if not active else "all_reps_at_cap"
        deal["waiting_reason"] = reason
        if not deal.get("tl_notified"):
            deal["tl_notified"] = True
            for u in S["users"].values():
                if u["role"] == Role.TEAM_LEADER and u["team_id"] == team_id:
                    notify(
                        u["id"],
                        "lead.waiting",
                        f"{S['accounts'][deal['account_id']]['name']} is waiting: {reason}",
                    )
        return False
    S["pointers"][team_id] = pick["id"]
    deal.update(
        owner_id=pick["id"],
        status="open",
        waiting_reason=None,
        assigned_at=now(),
        assignment_method="rule" if rule else "round_robin",
        rule_name=rule["name"] if rule else None,
        assigned_by="System",
    )
    audit(
        None,
        "lead.assigned",
        "deal",
        deal["id"],
        origin=origin,
        new={"owner": pick["name"], "method": deal["assignment_method"], "rule": deal["rule_name"]},
        note=f"Rule: {rule['name']}" if rule else "Round-robin",
    )
    notify(pick["id"], "lead.assigned", f"New lead: {S['accounts'][deal['account_id']]['name']}")
    return True


def retry_waiting() -> None:
    waiting = sorted(
        (d for d in S["deals"].values() if d["status"] == "pooled"), key=lambda d: d["pooled_at"]
    )
    for d in waiting:
        run_engine(d)


# ---------------------------------------------------------------- duplicates
def norm_domain(site: str | None) -> str | None:
    if not site:
        return None
    s = re.sub(r"^https?://", "", site.strip().lower())
    s = re.sub(r"^www\.", "", s).split("/")[0]
    return s or None


def norm_phone(p: str | None) -> str | None:
    digits = re.sub(r"\D", "", p or "")
    return digits[-10:] if digits else None


def find_matches(user: dict[str, Any], body: "LeadIn") -> dict[str, Any]:
    domain, phone, email = (
        norm_domain(body.website),
        norm_phone(body.phone),
        (body.email or "").lower() or None,
    )
    exact: list[dict[str, Any]] = []
    similar: list[dict[str, Any]] = []
    for acc_id, acc in S["accounts"].items():
        deals = [d for d in S["deals"].values() if d["account_id"] == acc_id]
        is_exact = bool(
            (domain and acc["domain"] == domain)
            or (phone and any(norm_phone(d["phone"]) == phone for d in deals))
            or (email and any((d["email"] or "").lower() == email for d in deals))
        )
        ratio = SequenceMatcher(None, body.company.lower(), acc["name"].lower()).ratio()
        if not is_exact and ratio < 0.75:
            continue
        decision = TABLE[(user["role"], "deal.view")]
        visible = [d for d in deals if in_scope(user, decision.scope, d)]
        owner = next((user_name(d["owner_id"]) for d in deals if d["owner_id"]), None)
        entry: dict[str, Any] = {"company": acc["name"], "owner": owner, "in_scope": bool(visible)}
        if visible:  # only in-scope matches may reveal ids
            entry["deal_id"] = visible[0]["id"]
        (exact if is_exact else similar).append(entry)
    return {"block": exact, "warn": similar}


# ---------------------------------------------------------------- API
class LeadIn(BaseModel):
    company: str
    website: str | None = None
    industry: str | None = None
    city: str | None = None
    contact: str
    phone: str | None = None
    email: str | None = None
    lead_source: str
    next_step: str
    due_date: str
    create_anyway_reason: str | None = None
    reason: str | None = None


class ReasonBody(BaseModel):
    reason: str | None = None
    user_id: str | None = None
    to_team_id: str | None = None


@app.get("/demo/users")
def users() -> list[dict[str, Any]]:
    return [
        {**u, "team_name": team_name(u["team_id"]), "active_leads": active_leads(u["id"])}
        for u in S["users"].values()
    ]


@app.get("/demo/state")
def state(user: dict[str, Any] = User) -> dict[str, Any]:
    deal_scope = TABLE[(user["role"], "deal.view")].scope
    pool_rule = TABLE[(user["role"], "pool.view")]
    deals = [view_deal(d) for d in S["deals"].values() if in_scope(user, deal_scope, d)]
    pool = []
    if not pool_rule.denied:
        pool = [
            view_deal(d)
            for d in S["deals"].values()
            if d["status"] == "pooled" and in_scope(user, pool_rule.scope, d)
        ]
    return {
        "me": {**user, "team_name": team_name(user["team_id"])},
        "deals": sorted(deals, key=lambda d: d["pooled_at"], reverse=True),
        "pool": pool,
        "teams": list(S["teams"].values()),
        "reps": [
            {
                "id": u["id"],
                "name": u["name"],
                "team_id": u["team_id"],
                "cap": u["cap"],
                "active_leads": active_leads(u["id"]),
                "active": u["active"],
            }
            for u in S["users"].values()
            if u["role"] == Role.USER
        ],
        "notifications": [n for n in reversed(S["notifications"]) if n["user_id"] == user["id"]][
            :30
        ],
        "can": {
            k: not TABLE[(user["role"], k)].denied
            for k in (
                "pool.assign",
                "lead.reassign",
                "assignment.config",
                "deal.team_transfer.request",
                "approval.decide",
                "deal.lose",
            )
        },
        "rules": S["rules"],
        "default_team": S["default_team"],
    }


@app.post("/demo/duplicates/check")
def dup_check(body: LeadIn, user: dict[str, Any] = User) -> dict[str, Any]:
    return find_matches(user, body)


@app.post("/demo/leads", status_code=201)
def create_lead(body: LeadIn, user: dict[str, Any] = User) -> dict[str, Any]:
    authorize(user, "lead.create")
    reason_for(user, "lead.create", body.reason)
    if body.due_date < now()[:10]:
        raise Conflict("Due date cannot be in the past")
    matches = find_matches(user, body)
    if matches["block"]:
        raise Conflict("Company already exists", {"matches": matches["block"]})
    if matches["warn"]:
        authorize(user, "duplicate.create_anyway")
        why = reason_for(user, "duplicate.create_anyway", body.create_anyway_reason)
        outside = any(not m["in_scope"] for m in matches["warn"])
        audit(
            user,
            "duplicate.create_anyway",
            "lead",
            body.company,
            reason=why,
            new={"matches": [m["company"] for m in matches["warn"]], "outside_scope": outside},
        )
        if outside:
            for u in S["users"].values():
                if u["role"] in (Role.ADMIN, Role.SUPER_ADMIN):
                    notify(
                        u["id"],
                        "duplicate.created_anyway_outside_scope",
                        f"{user['name']} created '{body.company}' despite a similar company in another team",
                    )
    acc_id = nid("acc")
    S["accounts"][acc_id] = {
        "id": acc_id,
        "name": body.company,
        "domain": norm_domain(body.website),
        "industry": body.industry,
        "city": body.city,
    }
    deal_id = nid("d")
    deal = {
        "id": deal_id,
        "account_id": acc_id,
        "contact": body.contact,
        "phone": body.phone,
        "email": body.email,
        "lead_source": body.lead_source,
        "status": "pooled",
        "owner_id": None,
        "team_id": None,
        "generated_by": user["name"],
        "assigned_at": None,
        "assignment_method": None,
        "rule_name": None,
        "assigned_by": None,
        "next_step": body.next_step,
        "due_date": body.due_date,
        "waiting_reason": None,
        "pooled_at": now(),
        "tl_notified": False,
    }
    S["deals"][deal_id] = deal
    audit(
        user,
        "lead.create",
        "deal",
        deal_id,
        new={"company": body.company, "source": body.lead_source},
    )
    run_engine(deal)
    return view_deal(deal)


def _assign(user: dict[str, Any], deal_id: str, body: ReasonBody) -> dict[str, Any]:
    deal = S["deals"].get(deal_id)
    if not deal:
        raise NotFound("deal not found")
    key = "pool.assign" if deal["status"] == "pooled" else "lead.reassign"
    pool_scope_ok = key == "pool.assign" and TABLE[(user["role"], "pool.view")].scope != Scope.NONE
    authorize(user, key, deal if not pool_scope_ok else None)
    if key == "pool.assign" and not in_scope(user, TABLE[(user["role"], "pool.view")].scope, deal):
        raise OutOfScope("This lead is not in your share of the Pool")
    why = reason_for(user, key, body.reason)
    target = S["users"].get(body.user_id or "")
    if not target or target["role"] != Role.USER or not target["active"]:
        raise Conflict("Pick an active sales rep")
    if user["role"] == Role.TEAM_LEADER and target["team_id"] != user["team_id"]:
        raise OutOfScope("Team Leaders can only assign inside their own team")
    warning = None
    if active_leads(target["id"]) >= target["cap"]:
        warning = f"{target['name']} is at their cap ({target['cap']}); assigned anyway"
    prev = deal["owner_id"]
    old = {"owner": user_name(prev), "team": team_name(deal["team_id"])}
    deal.update(
        owner_id=target["id"],
        team_id=target["team_id"],
        status="open",
        waiting_reason=None,
        assigned_at=now(),
        assignment_method="manual",
        rule_name=None,
        assigned_by=user["name"],
    )
    audit(
        user,
        key,
        "deal",
        deal_id,
        old=old,
        new={"owner": target["name"], "team": team_name(target["team_id"])},
        reason=why,
        note=warning,
    )
    notify(
        target["id"],
        "lead.assigned" if not prev else "deal.owner_changed",
        f"{S['accounts'][deal['account_id']]['name']} assigned to you by {user['name']}",
    )
    if prev and prev != target["id"]:
        notify(
            prev,
            "deal.owner_changed",
            f"{S['accounts'][deal['account_id']]['name']} moved to {target['name']}",
        )
    return {**view_deal(deal), "warning": warning}


@app.post("/demo/deals/{deal_id}/assign")
def assign(deal_id: str, body: ReasonBody, user: dict[str, Any] = User) -> dict[str, Any]:
    try:
        return _assign(user, deal_id, body)
    except PermissionDenied as exc:
        audit(user, "pool.assign", "deal", deal_id, outcome="denied", note=exc.message)
        raise


@app.post("/demo/deals/{deal_id}/lose")
def lose(deal_id: str, body: ReasonBody, user: dict[str, Any] = User) -> dict[str, Any]:
    deal = S["deals"].get(deal_id)
    if not deal:
        raise NotFound("deal not found")
    authorize(user, "deal.lose", deal)
    why = reason_for(user, "deal.lose", body.reason)
    deal["status"] = "lost"
    audit(
        user,
        "deal.lose",
        "deal",
        deal_id,
        reason=why,
        old={"status": "open"},
        new={"status": "lost"},
    )
    retry_waiting()  # freed capacity: waiting leads are retried, oldest first
    return view_deal(deal)


@app.post("/demo/deals/{deal_id}/transfer")
def request_transfer(deal_id: str, body: ReasonBody, user: dict[str, Any] = User) -> dict[str, Any]:
    deal = S["deals"].get(deal_id)
    if not deal:
        raise NotFound("deal not found")
    decision = require(TABLE, user["role"], "deal.team_transfer.request")
    if decision.effect != Effect.REQUEST_ONLY:
        raise PermissionDenied(
            "Only Team Leaders raise transfer requests; Admins move deals directly"
        )
    if (
        not body.to_team_id
        or body.to_team_id == deal["team_id"]
        or body.to_team_id not in S["teams"]
    ):
        raise Conflict("Choose a different target team")
    if deal["team_id"] != user["team_id"]:
        raise OutOfScope("You can only give away deals owned by your team")
    why = reason_for(user, "deal.team_transfer.request", body.reason)
    if any(a["deal_id"] == deal_id and a["status"] == "pending" for a in S["approvals"].values()):
        raise Conflict("A transfer request is already pending for this deal")
    aid = nid("ap")
    S["approvals"][aid] = {
        "id": aid,
        "deal_id": deal_id,
        "type": "team_transfer",
        "status": "pending",
        "from_team": deal["team_id"],
        "to_team": body.to_team_id,
        "requested_by": user["name"],
        "reason": why,
        "routed_to": "admin_pool",
        "created_at": now(),
        "decided_by": None,
        "decision_reason": None,
        "summary": f"{team_name(deal['team_id'])} asks to give {S['accounts'][deal['account_id']]['name']} to {team_name(body.to_team_id)}; reason: {why}",
    }
    audit(
        user,
        "deal.team_transfer.request",
        "deal",
        deal_id,
        outcome="approval_requested",
        reason=why,
        new={"to_team": team_name(body.to_team_id)},
    )
    for u in S["users"].values():
        if u["role"] in (Role.ADMIN, Role.SUPER_ADMIN) or (
            u["role"] == Role.TEAM_LEADER and u["team_id"] == body.to_team_id
        ):
            notify(u["id"], "transfer.requested", S["approvals"][aid]["summary"])
    return S["approvals"][aid]


@app.get("/demo/approvals")
def approvals(user: dict[str, Any] = User) -> list[dict[str, Any]]:
    items = list(S["approvals"].values())
    if user["role"] in (Role.ADMIN, Role.SUPER_ADMIN):
        return list(reversed(items))
    return [a for a in reversed(items) if a["requested_by"] == user["name"]]


@app.post("/demo/approvals/{aid}/decide")
def decide(aid: str, body: dict[str, Any], user: dict[str, Any] = User) -> dict[str, Any]:
    ap = S["approvals"].get(aid)
    if not ap:
        raise NotFound("request not found")
    require(TABLE, user["role"], "approval.decide")
    if ap["routed_to"] == "admin_pool" and user["role"] not in (Role.ADMIN, Role.SUPER_ADMIN):
        raise PermissionDenied("This request goes to Admins and Super admins")
    if ap["status"] != "pending":
        raise Conflict("Already decided")
    why = reason_for(user, "approval.decide", body.get("reason"))
    approve = body.get("decision") == "approve"
    deal = S["deals"][ap["deal_id"]]
    if approve:
        if deal["team_id"] != ap["from_team"]:
            raise Conflict("Deal changed, raise the request again")
        prev = deal["owner_id"]
        deal.update(
            team_id=ap["to_team"],
            owner_id=None,
            status="pooled",
            waiting_reason=None,
            tl_notified=False,
            pooled_at=now(),
            assignment_method=None,
            assigned_by=None,
        )
        notify(prev, "transfer.decided", "Your deal moved to another team")
    ap.update(
        status="approved" if approve else "rejected", decided_by=user["name"], decision_reason=why
    )
    audit(
        user,
        "deal.team_transfer.decide_or_move",
        "deal",
        ap["deal_id"],
        reason=why,
        new={
            "decision": ap["status"],
            "requested_by": ap["requested_by"],
            "to_team": team_name(ap["to_team"]),
        },
    )
    for u in S["users"].values():
        if u["role"] == Role.TEAM_LEADER and u["team_id"] in (ap["from_team"], ap["to_team"]):
            notify(u["id"], "transfer.decided", f"Transfer {ap['status']}: {ap['summary']}")
    if approve:
        retry_waiting()
    return ap


@app.put("/demo/reps/{uid}/cap")
def set_cap(uid: str, body: dict[str, Any], user: dict[str, Any] = User) -> dict[str, Any]:
    authorize(user, "assignment.config")
    why = reason_for(user, "assignment.config", body.get("reason"))
    rep = S["users"].get(uid)
    if not rep or rep["role"] != Role.USER:
        raise NotFound("rep not found")
    old = rep["cap"]
    rep["cap"] = int(body["cap"])
    audit(
        user,
        "assignment.config",
        "user",
        uid,
        old={"cap": old},
        new={"cap": rep["cap"]},
        reason=why,
    )
    retry_waiting()
    return {"id": uid, "cap": rep["cap"]}


@app.get("/demo/history/{deal_id}")
def history(deal_id: str, user: dict[str, Any] = User) -> list[dict[str, Any]]:
    deal = S["deals"].get(deal_id)
    if not deal:
        raise NotFound("deal not found")
    authorize(
        user,
        "audit.view",
        deal if deal["status"] != "pooled" or user["role"] == Role.USER else None,
    )
    return [a for a in reversed(S["audit"]) if a["entity_id"] == deal_id]


@app.get("/demo/audit")
def full_audit(user: dict[str, Any] = User) -> list[dict[str, Any]]:
    if TABLE[(user["role"], "audit.view")].scope != Scope.ALL:
        raise PermissionDenied("Only Admins and Super admins see the full audit log")
    return list(reversed(S["audit"]))[:200]


@app.post("/demo/reset")
def reset_demo() -> dict[str, str]:
    reset()
    seed_demo()
    return {"status": "reset"}


def seed_demo() -> None:
    """A few starting leads so the Pool and distribution are not empty."""
    sa = S["users"]["sa"]
    for company, src, industry, site in [
        ("Acme Foods", "Referral", "Food & Beverage", "acmefoods.in"),
        ("Bharat Logistics", "Website", "Logistics", "bharatlogistics.com"),
        ("Cedar Clinics", "LinkedIn", "Healthcare", "cedarclinics.in"),
    ]:
        create_lead(
            LeadIn(
                company=company,
                website=site,
                industry=industry,
                contact="Owner",
                phone=None,
                email=f"hello@{site}",
                lead_source=src,
                next_step="Intro call",
                due_date="2099-01-01",
                create_anyway_reason="demo seed",
            ),
            sa,
        )


seed_demo()
