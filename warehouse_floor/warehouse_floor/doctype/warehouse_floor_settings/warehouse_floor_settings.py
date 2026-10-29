import frappe
from frappe import _
from frappe.model.document import Document

ROLES = (
	("stores_warehouses", "Stores"),
	("wip_warehouses", "Work In Progress"),
	("fg_warehouses", "Finished Goods"),
)


class WarehouseFloorSettings(Document):
	def validate(self):
		self.reviewed = 1  # tells the page that a person has looked at this setup
		seen, companies = {}, set()
		for field, label in ROLES:
			for row in self.get(field) or []:
				w = row.warehouse
				if not w:
					continue
				if w in seen:
					frappe.throw(_("Warehouse {0} is listed under {1} and under {2}. Each warehouse can have one role.").format(
						frappe.bold(w), seen[w], label))
				seen[w] = label
				info = frappe.db.get_value("Warehouse", w, ["is_group", "disabled", "company"], as_dict=True)
				if not info:
					frappe.throw(_("{0}: warehouse {1} does not exist.").format(label, frappe.bold(w)))
				if info.is_group:
					frappe.throw(_("{0}: {1} is a group warehouse. Choose the warehouses that hold the stock.").format(label, frappe.bold(w)))
				if info.disabled:
					frappe.throw(_("{0}: warehouse {1} is disabled.").format(label, frappe.bold(w)))
				if self.company and info.company != self.company:
					frappe.throw(_("{0}: warehouse {1} belongs to {2}, not {3}.").format(label, frappe.bold(w), info.company, self.company))
				companies.add(info.company)
		if not self.company and len(companies) > 1:
			frappe.throw(_("These warehouses belong to more than one company ({0}). Pick a company above.").format(", ".join(sorted(companies))))
