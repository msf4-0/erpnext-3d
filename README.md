# Warehouse Floor

3D warehouse and production-floor dashboard for ERPNext. One horizontal line, in job-flow order:
a silo per stocked item (Stores + Work In Progress), the workbenches, then a silo per Finished Goods item.

After install it is served at `/assets/warehouse_floor/warehouse-floor.html` (log in to ERPNext first).
Data comes from `warehouse_floor.api.snapshot`, using the logged-in user's permissions. Read-only.

## Install on any ERPNext site

```bash
bench get-app https://github.com/msf4-0/erpnext-3d --branch main
bench --site your.site install-app warehouse_floor
bench build --app warehouse_floor
bench --site your.site migrate
bench restart
```

With frappe_docker, add the repo to `apps.json` and rebuild the image with `--no-cache`.

## Point it at your warehouses

Warehouse names differ on every site, so do this once per install. Open **Warehouse Floor Settings** in ERPNext
(awesomebar, or `/app/warehouse-floor-settings`):

- **Stores, Work In Progress and Finished Goods** each take a list of warehouses, and their stock is added together.
- **Company** is optional. Set it on sites with several companies: only that company's warehouses are used, and its
  currency is shown.
- Saving checks that every warehouse exists, is not a group or disabled, belongs to the company, and is used for only
  one role.
- Also on the page: default capacity per item, Finished Goods capacity, the silo and workbench limits and the
  "down" statuses. The **Open 3D floor** button opens the dashboard.

If a role is left empty, the system guesses: `site_config.json` (`wf_stores`, `wf_wip`, `wf_fg`, a name or a list),
then the Manufacturing Settings defaults, then the first warehouse named "Stores%", "Work In Progress%" or
"Finished Goods%". A guess can be wrong, so the dashboard shows a **Setup needed** banner when a warehouse is
missing, does not exist, is a group, is shared between roles, or when the warehouses belong to several companies.
Stores is always required. Work In Progress and Finished Goods only raise the banner until the settings page has
been saved once, so a site without them can save the page empty.

Other `site_config.json` keys still work as fallbacks: `wf_default_capacity` (1000), `wf_fg_capacity` (5000),
`wf_max_items` (10), `wf_max_workstations` (6), `wf_down_statuses`.

## Currency and language

The snapshot returns the default currency of the company that owns the Stores warehouse, and the page uses the browser
language for number formats. Override in a script placed before the loader:

```html
<script>window.WF_CONFIG = { currency: 'USD', locale: 'en-US' };</script>
```

## Using the page against another site (optional)

The other site must still have this app installed (the snapshot endpoint lives in it).

- Open `warehouse-floor.html?site=https://erp.example.com` (uses your browser's login to that site), or
- set `window.WF_CONFIG = { baseUrl, apiKey, apiSecret }` for token access.

The ERPNext site must allow the page's origin, in `site_config.json`:
`"allow_cors": "https://where-the-page-is-hosted.example.com"` (avoid `"*"` in production).
An API secret placed in a page is readable by anyone who can open it, so use a dedicated read-only user.

## Notes

- Capacity per Finished Goods silo comes from that item's Putaway Rule in the Finished Goods warehouse, else the warehouse
  capacity shared equally.
- Tested on ERPNext/Frappe version-16 only. Job Card, Workstation, Putaway Rule and Routing field names
  are not yet verified against real data.
- three.js r128 loads from jsDelivr, so the browser needs internet access unless it is bundled.
- Users need read permission on Bin, Item, Warehouse, Job Card, Workstation, Stock Ledger Entry.
