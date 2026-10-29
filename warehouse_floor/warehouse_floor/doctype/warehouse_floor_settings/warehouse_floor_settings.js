frappe.ui.form.on("Warehouse Floor Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Open 3D floor"), () => {
			window.open("/assets/warehouse_floor/warehouse-floor.html", "_blank");
		});
	},
});
