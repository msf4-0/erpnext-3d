"""Read-only data feed for the 3D warehouse floor page.

Optional settings in sites/<site>/site_config.json (all have defaults):
  wf_stores, wf_wip, wf_fg          warehouse names (default: "Stores%", and the
                                    WIP / FG defaults from Manufacturing Settings)
  wf_default_capacity               per-item capacity when no Putaway Rule exists (1000)
  wf_fg_capacity                    Finished Goods capacity (5000)
  wf_max_items, wf_max_workstations how many bars / workbenches to draw (10 / 6)
  wf_down_statuses                  Workstation statuses drawn as "down"
"""
import frappe
from frappe.utils import cint, flt, get_datetime, today


def _conf(key, default=None):
    value = frappe.conf.get(key)
    return default if value in (None, "") else value


def _warehouses():
    ms = frappe.db.get_singles_dict("Manufacturing Settings") or {}
    stores = _conf("wf_stores") or frappe.db.get_value(
        "Warehouse", {"is_group": 0, "name": ["like", "Stores%"]}, "name")
    return stores, _conf("wf_wip") or ms.get("default_wip_warehouse"), _conf("wf_fg") or ms.get("default_fg_warehouse")


def _ms(row):
    try:
        return get_datetime(f"{row.posting_date} {row.posting_time}").timestamp() * 1000
    except Exception:
        return 0


@frappe.whitelist()
def snapshot():
    frappe.has_permission("Bin", "read", throw=True)
    stores, wip, fg = _warehouses()
    whs = [w for w in (stores, wip, fg) if w]
    max_items, max_ws = cint(_conf("wf_max_items", 10)), cint(_conf("wf_max_workstations", 6))
    default_cap = max(flt(_conf("wf_default_capacity", 1000)), 1)

    # ---- stock by item: Stores + WIP merged, Finished Goods kept separate
    by_item = {}
    for b in frappe.get_list("Bin", filters={"warehouse": ["in", whs]},
                             fields=["item_code", "warehouse", "actual_qty", "valuation_rate"], limit_page_length=0):
        d = by_item.setdefault(b.item_code, {"stores": 0, "wip": 0, "fg": 0, "rate": 0, "raw": False})
        qty = flt(b.actual_qty)
        if b.warehouse == fg:
            d["fg"] += qty
        elif b.warehouse == wip:
            d["wip"] += qty; d["raw"] = True
        elif b.warehouse == stores:
            d["stores"] += qty; d["raw"] = True
        d["rate"] = d["rate"] or flt(b.valuation_rate)

    value = lambda c: (by_item[c]["stores"] + by_item[c]["wip"]) * by_item[c]["rate"]
    raw = sorted([c for c, d in by_item.items() if d["raw"]], key=value, reverse=True)[:max_items]
    fgc = sorted([c for c, d in by_item.items() if d["fg"] > 0], key=lambda c: by_item[c]["fg"], reverse=True)[:6]
    codes = list(dict.fromkeys(raw + fgc))

    meta = {i.name: i for i in frappe.get_all("Item", filters={"name": ["in", codes]},
                                              fields=["name", "item_name", "stock_uom"])}
    caps = {}
    try:  # optional: Putaway Rule capacity per item
        for r in frappe.get_all("Putaway Rule", filters={"item_code": ["in", raw], "warehouse": ["in", [stores, wip]]},
                                fields=["item_code", "capacity"]):
            caps[r.item_code] = caps.get(r.item_code, 0) + flt(r.capacity)
    except Exception:
        pass

    def row(c):
        m = meta.get(c)
        return {"code": c, "name": (m.item_name if m else c), "uom": (m.stock_uom if m else "pcs"),
                "rate": round(by_item[c]["rate"], 2)}

    items = [dict(row(c), stores=by_item[c]["stores"], wip=by_item[c]["wip"],
                  cap=max(caps.get(c) or default_cap, 1)) for c in raw]
    fg_lines = [dict(row(c), qty=by_item[c]["fg"]) for c in fgc]

    # ---- movements from the stock ledger
    log, fg_log = {}, []
    if codes:
        sle = frappe.get_list("Stock Ledger Entry",
                              filters={"item_code": ["in", codes], "warehouse": ["in", whs], "is_cancelled": 0},
                              fields=["item_code", "warehouse", "actual_qty", "voucher_type", "posting_date", "posting_time"],
                              order_by="posting_date desc, posting_time desc, creation desc",
                              limit_page_length=max(60, len(codes) * 8))
        for e in sle:
            if e.warehouse == fg:
                if len(fg_log) < 8:
                    fg_log.append({"t": e.voucher_type, "d": flt(e.actual_qty), "at": _ms(e), "item": e.item_code})
            elif len(log.setdefault(e.item_code, [])) < 6:
                tag = " (WIP)" if e.warehouse == wip else ""
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
    down_states = _conf("wf_down_statuses", ["Problem", "Maintenance"])
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

    return {"items": items, "fg": {"name": fg or "Finished Goods", "cap": flt(_conf("wf_fg_capacity", 5000)),
                                   "lines": fg_lines, "log": fg_log},
            "ws": ws, "routes": routes, "log": log}
