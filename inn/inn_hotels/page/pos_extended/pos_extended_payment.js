frappe.require(["point-of-sale.bundle.js", "inn-pos.bundle.js"], function () {

    inn.PointOfSale.PosExtendedPayment = class PosExtendedPayment extends erpnext.PointOfSale.Payment {
        constructor({ wrapper, events, settings }) {
            super({ wrapper, events, settings });
        }

        bind_events() {
            super.bind_events()

            this.$component.off("click");
            this.$component.on('click', '.submit-order-btn', () => {
                const doc = this.events.get_frm().doc;
                const items = doc.items;

                // remove the validation of price amount and pay amount
                if (!items.length) {
                    const message = __("You cannot submit empty order.");
                    frappe.show_alert({ message, indicator: "orange" });
                    frappe.utils.play_sound("error");
                    return;
                }

                this.events.submit_invoice();
            });

        }
    }
});