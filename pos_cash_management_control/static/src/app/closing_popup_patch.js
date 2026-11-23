/** @odoo-module */

import { ClosePosPopup } from "@point_of_sale/app/components/popups/closing_popup/closing_popup";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { ask } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { patch } from "@web/core/utils/patch";
import { parseFloat } from "@web/views/fields/parsers";
import { _t } from "@web/core/l10n/translation";

patch(ClosePosPopup.prototype, {
    setup() {
        super.setup(...arguments);
        const cash = this.props.default_cash_details;
        const hasCash = Boolean(this.pos.config.cash_control && cash && cash.id);
        // `pcm_masked` comes from the server (cashier user); the role check
        // covers pos_hr, where the employee may be a cashier while the
        // logged-in user is a manager.
        this.pcmBlind = hasCash && (Boolean(cash.pcm_masked) || this.pos.pcmIsBlindClosing());
        this.pcmServerCount =
            hasCash &&
            !this.pcmBlind &&
            (Boolean(cash.pcm_server_count) || this.pos.pcmIsServerClosingCount());
        this.pcmHideDailySale = !this.pos.pcmCanSeeDailySale();
        if (this.pcmBlind) {
            this._pcmMaskCashDetails(cash);
        }
        if (this.pcmServerCount) {
            this.state.payments[cash.id].counted = this.env.utils.formatCurrency(cash.amount, false);
        }
    },
    /**
     * Blind count: whatever template renders the cash overview (core, or the
     * per-employee one of pos_hr), it only ever receives neutral values.
     * The server already masks them for cashier users; this also covers a
     * pos_hr cashier employee working under a manager login.
     */
    _pcmMaskCashDetails(cash) {
        Object.assign(cash, { amount: 0, opening: 0, payment_amount: 0, moves: [] });
        if ("amount_per_employee" in cash) {
            cash.amount_per_employee = [];
        }
        if ("moves_per_employee" in cash) {
            cash.moves_per_employee = [];
        }
        if (this.props.orders_details) {
            this.props.orders_details.amount = 0;
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
    autoFillCashCount() {
        // The fill button copies the expected cash: never in blind or server count.
        if (this.pcmBlind || this.pcmServerCount) {
            return;
        }
        return super.autoFillCashCount(...arguments);
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
                this.dialog.add(AlertDialog, {
                    title: _t("Invalid Cash Count"),
                    body: _t("Please enter the counted cash before closing the session."),
                });
                return;
            }
            const confirmed = await ask(this.dialog, {
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
            this.dialog.add(AlertDialog, {
                title: _t("Access Denied"),
                body: _t("Only Point of Sale managers can view the Daily Sale report."),
            });
            return;
        }
        return super.downloadSalesReport(...arguments);
    },
});
