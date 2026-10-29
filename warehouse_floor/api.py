"""Read-only data feed for the 3D warehouse floor page.

Which warehouses to read is decided, per role (Stores, Work In Progress, Finished Goods), in this order:
  1. the "Warehouse Floor Settings" page in ERPNext (each role can list several warehouses),
  2. sites/<site>/site_config.json: wf_stores, wf_wip, wf_fg (a name or a list of names),
  3. the WIP / FG defaults in Manufacturing Settings,
  4. the first warehouse whose name starts with "Stores", "Work In Progress" or "Finished Goods".
Anything that cannot be resolved or looks wrong is reported in `setup.problems`, which the page shows as a banner.

Other settings (settings page first, then site_config, then the default):
  wf_default_capacity               per-item capacity when no Putaway Rule exists (1000)
  wf_fg_capacity                    Finished Goods capacity (5000)
  wf_max_items, wf_max_workstations how many silos / workbenches to draw (10 / 6)
  wf_down_statuses                  Workstation statuses drawn as "down"
"""
import frappe
from frappe.utils import cint, flt, get_datetime, today

SETTINGS = "Warehouse Floor Settings"
SETTINGS_URL = "/app/warehouse-floor-settings"
# site_config key -> field on the settings page
FIELDS = {
    "wf_default_capacity": "default_capacity", "wf_fg_capacity": "fg_capacity",
    "wf_max_items": "max_items", "wf_max_workstations": "max_workstations",
    "wf_down_statuses": "down_statuses",
}
# role, table field on the settings page, site_config key, Manufacturing Settings field, warehouse name prefix, label
ROLES = [
    ("stores", "stores_warehouses", "wf_stores", None, "Stores", "Stores"),
    ("wip", "wip_warehouses", "wf_wip", "default_wip_warehouse", "Work In Progress", "Work In Progress"),
    ("fg", "fg_warehouses", "wf_fg", "default_fg_warehouse", "Finished Goods", "Finished Goods"),
]


def _settings():
    try:  # the page may not exist yet (before `bench migrate`), so never fail on it
        return frappe.get_doc(SETTINGS), True
    except Exception:
        return {}, False


def _cfg(S, key, default=None):
    value = S.get(FIELDS[key])
    if value in (None, "", "0", 0):
        value = frappe.conf.get(key)
    return default if value in (None, "") else value


def _as_list(value):
    if not value:
        return []
    return [v for v in (value if isinstance(value, (list, tuple)) else [value]) if v]


def _by_prefix(prefix, company=None):
    filters = {"is_group": 0, "disabled": 0, "name": ["like", prefix + "%"]}
    if company:
        filters["company"] = company
    return frappe.db.get_value("Warehouse", filters, "name")


def _warehouses(S):
    """Returns ({role: [warehouse, ...]}, [problem, ...], company)."""
    ms = frappe.db.get_singles_dict("Manufacturing Settings") or {}
    company = S.get("company") or None
    reviewed = cint(S.get("reviewed"))  # set when the settings page has been saved once
    found, problems, owner = {}, [], {}
    for role, field, conf_key, ms_key, prefix, label in ROLES:
        names = [r.warehouse for r in (S.get(field) or []) if r.warehouse]
        if not names:
            names = _as_list(frappe.conf.get(conf_key))
        if not names and ms_key and ms.get(ms_key):
            names = [ms.get(ms_key)]
        if not names:
            guess = _by_prefix(prefix, company)
            names = [guess] if guess else []
        good = []
        for w in dict.fromkeys(names):
            info = frappe.db.get_value("Warehouse", w, ["is_group", "disabled", "company"], as_dict=True)
            if not info:
                problems.append(f"{label}: warehouse '{w}' does not exist.")
            elif info.is_group:
                problems.append(f"{label}: '{w}' is a group warehouse. Choose the warehouses that hold the stock.")
            elif info.disabled:
                problems.append(f"{label}: warehouse '{w}' is disabled.")
            elif company and info.company != company:
                problems.append(f"{label}: '{w}' belongs to {info.company}, not {company}.")
            elif w in owner:
                problems.append(f"'{w}' is listed under both {owner[w]} and {label}.")
            else:
                owner[w] = label
                good.append(w)
        found[role] = good
        if not good and (role == "stores" or not reviewed):
            problems.append(f"No {label} warehouse found. Choose one in Warehouse Floor Settings"
                            + ("." if role == "stores" else f", or save the page empty if this site has no {label} warehouse."))
    companies = {frappe.db.get_value("Warehouse", w, "company") for ws in found.values() for w in ws}
    if not company and len(companies) > 1:
        problems.append("The warehouses belong to more than one company (" + ", ".join(sorted(map(str, companies)))
                        + "). Pick a company in Warehouse Floor Settings.")
    return found, problems, company or (next(iter(companies)) if len(companies) == 1 else None)


def _currency(company):
    try:
        company = company or frappe.db.get_single_value("Global Defaults", "default_company")
        if company:
            return frappe.get_cached_value("Company", company, "default_currency")
        return frappe.db.get_single_value("Global Defaults", "default_currency")
    except Exception:
        return None


def _ms(row):
    try:
        return get_datetime(f"{row.posting_date} {row.posting_time}").timestamp() * 1000
    except Exception:
        return 0


@frappe.whitelist()
def snapshot():
    frappe.has_permission("Bin", "read", throw=True)
    S, has_page = _settings()
    found, problems, company = _warehouses(S)
    stores, wip, fg = found["stores"], found["wip"], found["fg"]
    stores_set, wip_set, fg_set = set(stores), set(wip), set(fg)
    whs = stores + wip + fg
    max_items, max_ws = cint(_cfg(S, "wf_max_items", 10)), cint(_cfg(S, "wf_max_workstations", 6))
    default_cap = max(flt(_cfg(S, "wf_default_capacity", 1000)), 1)

    # ---- stock by item: Stores + WIP merged, Finished Goods kept separate
    by_item = {}
    bins = frappe.get_list("Bin", filters={"warehouse": ["in", whs]},
                           fields=["item_code", "warehouse", "actual_qty", "valuation_rate"], limit_page_length=0) if whs else []
    for b in bins:
        d = by_item.setdefault(b.item_code, {"stores": 0, "wip": 0, "fg": 0, "rate": 0, "raw": False})
        qty = flt(b.actual_qty)
        if b.warehouse in fg_set:
            d["fg"] += qty
        elif b.warehouse in wip_set:
            d["wip"] += qty; d["raw"] = True
        elif b.warehouse in stores_set:
            d["stores"] += qty; d["raw"] = True
        d["rate"] = d["rate"] or flt(b.valuation_rate)

    value = lambda c: (by_item[c]["stores"] + by_item[c]["wip"]) * by_item[c]["rate"]
    raw = sorted([c for c, d in by_item.items() if d["raw"]], key=value, reverse=True)[:max_items]
    fgc = sorted([c for c, d in by_item.items() if d["fg"] > 0], key=lambda c: by_item[c]["fg"], reverse=True)[:6]
    codes = list(dict.fromkeys(raw + fgc))

    meta = {i.name: i for i in frappe.get_all("Item", filters={"name": ["in", codes]},
                                              fields=["name", "item_name", "stock_uom"])}
    # optional: Putaway Rule capacity per item. Raw items add up Stores + WIP, Finished Goods use their own warehouses.
    caps, fg_caps = {}, {}
    if codes and whs:
        try:
            for r in frappe.get_all("Putaway Rule", filters={"item_code": ["in", codes], "warehouse": ["in", whs]},
                                    fields=["item_code", "warehouse", "capacity"]):
                if r.warehouse in fg_set:
                    fg_caps[r.item_code] = fg_caps.get(r.item_code, 0) + flt(r.capacity)
                else:
                    caps[r.item_code] = caps.get(r.item_code, 0) + flt(r.capacity)
        except Exception:
            pass

    def row(c):
        m = meta.get(c)
        return {"code": c, "name": (m.item_name if m else c), "uom": (m.stock_uom if m else "pcs"),
                "rate": round(by_item[c]["rate"], 2)}

    items = [dict(row(c), stores=by_item[c]["stores"], wip=by_item[c]["wip"],
                  cap=max(caps.get(c) or default_cap, 1)) for c in raw]
    fg_lines = []
    for c in fgc:
        line = dict(row(c), qty=by_item[c]["fg"])
        if fg_caps.get(c):  # only when a rule exists; otherwise the page shares the warehouse capacity equally
            line["cap"] = fg_caps[c]
        fg_lines.append(line)

    # ---- movements from the stock ledger
    log, fg_log = {}, []
    if codes:
        sle = frappe.get_list("Stock Ledger Entry",
                              filters={"item_code": ["in", codes], "warehouse": ["in", whs], "is_cancelled": 0},
                              fields=["item_code", "warehouse", "actual_qty", "voucher_type", "posting_date", "posting_time"],
                              order_by="posting_date desc, posting_time desc, creation desc",
                              limit_page_length=max(60, len(codes) * 8))
        for e in sle:
            if e.warehouse in fg_set:
                if len(fg_log) < 8:
                    fg_log.append({"t": e.voucher_type, "d": flt(e.actual_qty), "at": _ms(e), "item": e.item_code})
            elif len(log.setdefault(e.item_code, [])) < 6:
                tag = " (WIP)" if e.warehouse in wip_set else ""
                log[e.item_code].append({"t": e.voucher_type + tag, "d": flt(e.actual_qty), "at": _ms(e)})

    # ---- workstations and open job cards
    has_status = frappe.get_meta("Workstation").has_field("status")
    stations = frappe.get_list("Workstation", fields=["name", "production_capacity"] + (["status"] if has_status else []),
                               limit_page_length=0)
    cards = frappe.get_list("Job Card",
                            filters={"status": ["in", ["Open", "Work In Progress", "Material Transferred"]], "docstatus": ["<", 2]},
                            fields=["name", "workstation", "status", "for_quantity", "total_completed_qty", "work_order", "operation"],
                            order_by="creation asc", limit_page_length=0)
    done = {}
    for c in frappe.get_list("Job Card", filters={"status": "Completed", "modified": [">=", today()]},
                             fields=["workstation", "total_completed_qty"], limit_page_length=0):
        done[c.workstation] = done.get(c.workstation, 0) + flt(c.total_completed_qty)
    jobs = {}
    for c in cards:
        jobs.setdefault(c.workstation, []).append({
            "jc": c.name, "wo": c.work_order, "op": c.operation, "qty": flt(c.for_quantity),
            "done": flt(c.total_completed_qty), "state": "run" if c.status == "Work In Progress" else "open"})
    stations.sort(key=lambda s: (s.name not in jobs, s.name))
    down = _cfg(S, "wf_down_statuses", ["Problem", "Maintenance"])
    down_states = [x.strip() for x in down.split(",") if x.strip()] if isinstance(down, str) else list(down)
    ws = [{"name": s.name, "cap": max(cint(s.production_capacity), 1),
           "op": (jobs.get(s.name) or [{"op": "Workstation"}])[0]["op"], "doneToday": done.get(s.name, 0),
           "down": bool(has_status and s.get("status") in down_states), "jobs": jobs.get(s.name, []), "log": []}
          for s in stations[:max_ws]]

    # ---- routing: newest Routing, else workstation order
    index = {w["name"]: i for i, w in enumerate(ws)}
    routes, rt = [], frappe.get_all("Routing", fields=["name"], order_by="modified desc", limit_page_length=1)
    if rt:
        steps = frappe.get_all("BOM Operation", filters={"parent": rt[0].name, "parenttype": "Routing"},
                               fields=["operation", "workstation"], order_by="sequence_id asc, idx asc")
        routes.append({"name": rt[0].name, "label": rt[0].name,
                       "steps": [{"ws": index.get(s.workstation, -1), "op": s.operation} for s in steps]})
    if not routes or not routes[0]["steps"]:
        routes = [{"name": "Workstations", "label": "Workstation order",
                   "steps": [{"ws": i, "op": w["op"]} for i, w in enumerate(ws)]}]

    return {"items": items, "currency": _currency(company),
            "setup": {"ok": not problems, "problems": problems, "settings_url": SETTINGS_URL},
            "warehouses": found,
            "fg": {"name": fg[0] if len(fg) == 1 else "Finished Goods", "cap": flt(_cfg(S, "wf_fg_capacity", 5000)),
                   "lines": fg_lines, "log": fg_log},
            "ws": ws, "routes": routes, "log": log,
            "debug": {"stores": stores, "wip": wip, "fg": fg, "bin_rows": len(by_item), "items_found": len(items),
                      "settings_page": has_page}}
