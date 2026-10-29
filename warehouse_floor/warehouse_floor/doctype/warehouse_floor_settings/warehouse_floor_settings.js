frappe.ui.form.on("Warehouse Floor Settings", {
	refresh(frm) {
		["stores_warehouses", "wip_warehouses", "fg_warehouses"].forEach((table) => {
			frm.set_query("warehouse", table, () => ({
				filters: Object.assign({ is_group: 0, disabled: 0 }, frm.doc.company ? { company: frm.doc.company } : {}),
			}));
		});
		frm.add_custom_button(__("Open 3D floor"), () => {
			window.open("/assets/warehouse_floor/warehouse-floor.html", "_blank");
		});
	},
});
