import { SaleDetailsButton } from "@point_of_sale/app/components/navbar/sale_details_button/sale_details_button";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";

/**
 * The navbar "Print Report" entry is hidden through
 * `accessRight.canPrintReport`; the closing popup print button is guarded here.
 */
patch(SaleDetailsButton.prototype, {
    //@override
    async onClick() {
        if (!this.pos.pcmCanSeeDailySale()) {
            this.dialog.add(AlertDialog, {
                title: _t("Access Denied"),
                body: _t("Only Point of Sale managers can view the Daily Sale report."),
            });
            return;
        }
        return super.onClick(...arguments);
    },
});
