import { PosStore } from "@point_of_sale/app/services/pos_store";
import { PosAccessRightPlugin } from "@point_of_sale/app/plugins/access_right_plugin";
import { PosTicketPrinterPlugin } from "@point_of_sale/app/plugins/pos_ticket_printer_plugin";
import { patch } from "@web/core/utils/patch";

const DEFAULT_SETTINGS = Object.freeze({
    opening_mode: "standard",
    configured_opening_mode: "standard",
    closing_mode: "standard",
    float_amount: 0,
    hide_daily_sale: false,
    opened_by_server: false,
    user_is_manager: false,
});

/**
 * Settings sent with the session by `load_data` (`_pcm_cash_control`):
 * opening and closing screens never need an extra RPC.
 */
function pcmReadSettings(session) {
    return Object.freeze(Object.assign({}, DEFAULT_SETTINGS, (session && session._pcm_cash_control) || {}));
}

patch(PosAccessRightPlugin.prototype, {
    /**
     * Manager role of whoever operates the POS: the employee with pos_hr,
     * otherwise the logged-in user (role computed by the server).
     */
    get pcmIsManager() {
        const cashier = this.loggedCashier;
        return Boolean(cashier && cashier._role === "manager");
    },
    get pcmCanSeeDailySale() {
        return !pcmReadSettings(this.session).hide_daily_sale || this.pcmIsManager;
    },
    //@override: menu "Print Report"
    get canPrintReport() {
        return super.canPrintReport && this.pcmCanSeeDailySale;
    },
});

patch(PosTicketPrinterPlugin.prototype, {
    /**
     * Every Daily Sale ticket goes through here, including the automatic
     * download after closing: skip it silently for cashiers when the report is
     * restricted (the server refuses it anyway).
     */
    async printSaleDetailsReceipt() {
        if (this.pcmCanSeeDailySale && !this.pcmCanSeeDailySale()) {
            return false;
        }
        return super.printSaleDetailsReceipt(...arguments);
    },
});

patch(PosStore.prototype, {
    async processServerData() {
        await super.processServerData(...arguments);
        this.pcmCashControl = pcmReadSettings(this.session);
        // The printer plugin has no access to the cashier: hand it the check.
        if (this.ticketPrinter) {
            this.ticketPrinter.pcmCanSeeDailySale = () => this.pcmCanSeeDailySale();
        }
    },
    pcmSettings() {
        return this.pcmCashControl || DEFAULT_SETTINGS;
    },
    pcmIsManager() {
        return Boolean(this.accessRight && this.accessRight.pcmIsManager);
    },
    pcmIsBlindClosing() {
        return (
            Boolean(this.config && this.config.cash_control) &&
            this.pcmSettings().closing_mode === "blind" &&
            !this.pcmIsManager()
        );
    },
    pcmIsServerClosingCount() {
        return (
            Boolean(this.config && this.config.cash_control) &&
            this.pcmSettings().closing_mode === "server_no_count"
        );
    },
    pcmCanSeeDailySale() {
        return !this.pcmSettings().hide_daily_sale || this.pcmIsManager();
    },
});
