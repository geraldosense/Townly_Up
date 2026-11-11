"""Painel de supervisão (só a Câmara): como está a funcionar o Townly Up.

Os tempos são calculados em Python a partir das datas (funciona igual em PostgreSQL e SQLite).
"""
from datetime import datetime, timedelta, timezone

import services as sv

FMT = "%Y-%m-%d %H:%M:%S"
STALE_DAYS = 7  # pedidos «Registada» há mais de 7 dias sem resposta


def _dt(value):
    return datetime.strptime(str(value)[:19], FMT) if value else None


def _avg_days(pairs):
    deltas = [(b - a).total_seconds() / 86400 for a, b in pairs if a and b and b >= a]
    return round(sum(deltas) / len(deltas), 1) if deltas else None


def overview(db):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    week = (now - timedelta(days=7)).strftime(FMT)
    stale_limit = now - timedelta(days=STALE_DAYS)
    counts = sv.status_counts(db)
    active = sum(counts[s] for s in sv.ACTIVE_STATUSES)
    closed = counts[sv.RESOLVIDA] + counts[sv.REJEITADA] + counts[sv.DUPLICADA]
    total = active + closed

    incidents = db.execute(
        """SELECT i.id, i.ticket, i.status, i.created_at, i.resolved_at, i.parish_code, i.organization_id,
                  o.name AS organization, p.short_name AS parish, c.name AS category
             FROM incidents i LEFT JOIN organizations o ON o.id = i.organization_id
             LEFT JOIN parishes p ON p.code = i.parish_code JOIN categories c ON c.id = i.category_id
            WHERE i.deleted_at IS NULL""").fetchall()
    # 1.ª resposta = primeira mudança de estado feita por uma entidade
    first = {r["incident_id"]: _dt(r["first_at"]) for r in db.execute(
        """SELECT h.incident_id, MIN(h.changed_at) AS first_at FROM status_history h JOIN users u ON u.id = h.actor_id
            WHERE u.role != 'citizen' AND h.previous_status IS NOT NULL AND h.previous_status != h.new_status
            GROUP BY h.incident_id""")}

    resolution = _avg_days([(_dt(i["created_at"]), _dt(i["resolved_at"])) for i in incidents if i["status"] == sv.RESOLVIDA])
    response = _avg_days([(_dt(i["created_at"]), first.get(i["id"])) for i in incidents if i["id"] in first])
    stale = [i for i in incidents if i["status"] == sv.REGISTADA and _dt(i["created_at"]) < stale_limit]

    by_org = {}
    for i in incidents:
        o = by_org.setdefault(i["organization"] or "Sem entidade", {"name": i["organization"] or "Sem entidade",
                                                                    "active": 0, "resolved": 0, "stale": 0, "pairs": []})
        if i["status"] in sv.ACTIVE_STATUSES:
            o["active"] += 1
        if i["status"] == sv.RESOLVIDA:
            o["resolved"] += 1
            o["pairs"].append((_dt(i["created_at"]), _dt(i["resolved_at"])))
        if i in stale:
            o["stale"] += 1
    orgs = sorted(({**o, "avg": _avg_days(o.pop("pairs"))} for o in by_org.values()),
                  key=lambda o: (-o["active"], o["name"]))

    users = {r["role"]: r["n"] for r in db.execute(
        "SELECT role, COUNT(*) AS n FROM users WHERE active = TRUE GROUP BY role")}
    new_users = db.execute("SELECT COUNT(*) FROM users WHERE role = 'citizen' AND created_at >= ?", (week,)).fetchone()[0]
    new_incidents = sum(1 for i in incidents if str(i["created_at"]) >= week)
    activity = db.execute(
        """SELECT h.changed_at, h.previous_status, h.new_status, h.note, i.ticket, i.id AS incident_id,
                  u.name AS actor, u.role AS actor_role, o.name AS actor_org
             FROM status_history h JOIN incidents i ON i.id = h.incident_id JOIN users u ON u.id = h.actor_id
             LEFT JOIN organizations o ON o.id = u.organization_id
            WHERE i.deleted_at IS NULL ORDER BY h.changed_at DESC, h.id DESC LIMIT 12""").fetchall()
    moderation = db.execute(
        "SELECT COUNT(*) FROM moderation_events WHERE created_at >= ?", ((now - timedelta(days=30)).strftime(FMT),)).fetchone()[0]
    suspended = db.execute("SELECT COUNT(*) FROM users WHERE role = 'citizen' AND active = FALSE").fetchone()[0]
    deleted = db.execute("SELECT COUNT(*) FROM incidents WHERE deleted_at IS NOT NULL").fetchone()[0]

    return {
        "counts": counts, "active": active, "total": total,
        "resolution_rate": round(100 * counts[sv.RESOLVIDA] / closed) if closed else None,
        "avg_resolution": resolution, "avg_response": response,
        "stale": sorted(stale, key=lambda i: i["created_at"])[:10], "stale_total": len(stale), "stale_days": STALE_DAYS,
        "orgs": orgs, "parishes": [p for p in sv.parish_overview(db) if p["active"] or p["resolved"]][:10],
        "categories": sv.category_counts(db),
        "citizens": users.get("citizen", 0), "staff": users.get("staff", 0) + users.get("admin", 0),
        "new_users": new_users, "new_incidents": new_incidents, "activity": activity,
        "moderation": moderation, "suspended": suspended, "deleted": deleted,
    }
