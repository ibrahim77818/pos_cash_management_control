/** @odoo-module */

import { ClosePosPopup } from "@point_of_sale/app/navbar/closing_popup/closing_popup";
import { ConfirmPopup } from "@point_of_sale/app/utils/confirm_popup/confirm_popup";
import { ErrorPopup } from "@point_of_sale/app/errors/popups/error_popup";
import { patch } from "@web/core/utils/patch";
import { parseFloat } from "@web/views/fields/parsers";
import { _t } from "@web/core/l10n/translation";

patch(ClosePosPopup.prototype, {
    setup() {
        super.setup(...arguments);
        const cash = this.props.default_cash_details;
        const hasCash = Boolean(this.pos.config.cash_control && cash);
        // `pcm_masked` comes from the server (cashier user); the role check
        // covers pos_hr, where the employee may be a cashier while the
        // logged-in user is a manager.
        this.pcmBlind = hasCash && (Boolean(cash.pcm_masked) || this.pos.pcmIsBlindClosing());
        this.pcmServerCount =
            hasCash &&
            !this.pcmBlind &&
            (Boolean(cash.pcm_server_count) || this.pos.pcmIsServerClosingCount());
        this.pcmHideDailySale = !this.pos.pcmCanSeeDailySale();
        if (this.pcmServerCount) {
            this.state.payments[cash.id].counted = this.env.utils.formatCurrency(cash.amount, false);
        }
    },
    _pcmIsCashEntry(paymentId) {
        return (
            Boolean(this.props.default_cash_details) &&
            parseInt(paymentId) === this.props.default_cash_details.id
        );
    },
    /**
     * Differences the cashier may see and be judged on: blind and server
     * counts leave the cash line out.
     */
    //@override
    getMaxDifference() {
        const excludeCash = this.pcmBlind || this.pcmServerCount;
        const ids = Object.keys(this.state.payments)
            .map((id) => parseInt(id))
            .filter((id) => !(excludeCash && this._pcmIsCashEntry(id)));
        if (!ids.length) {
            return 0;
        }
        return Math.max(...ids.map((id) => Math.abs(this.getDifference(id))));
    },
    //@override
    async confirm() {
        if (this.pcmBlind) {
            const cashId = this.props.default_cash_details.id;
            let counted = NaN;
            try {
                counted = parseFloat(this.state.payments[cashId].counted);
            } catch {
                counted = NaN;
            }
            if (isNaN(counted) || counted < 0) {
                await this.popup.add(ErrorPopup, {
                    title: _t("Invalid Cash Count"),
                    body: _t("Please enter the counted cash before closing the session."),
                });
                return;
            }
            const { confirmed } = await this.popup.add(ConfirmPopup, {
                title: _t("Confirm Blind Count"),
                body: _t(
                    "You are declaring %s as the counted cash.\nThe expected amount is hidden; any difference will be reviewed by a manager.",
                    this.env.utils.formatCurrency(counted)
                ),
            });
            if (!confirmed) {
                return;
            }
        }
        return super.confirm(...arguments);
    },
    //@override
    async openDetailsPopup() {
        if (this.pcmServerCount) {
            return;
        }
        return super.openDetailsPopup(...arguments);
    },
    //@override
    async downloadSalesReport() {
        if (this.pcmHideDailySale) {
            await this.popup.add(ErrorPopup, {
                title: _t("Access Denied"),
                body: _t("Only Point of Sale managers can view the Daily Sale report."),
            });
            return;
        }
        return super.downloadSalesReport(...arguments);
    },
});
