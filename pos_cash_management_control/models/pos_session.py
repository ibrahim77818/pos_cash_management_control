# -*- coding: utf-8 -*-
from odoo import _, fields, models
from odoo.exceptions import UserError
from odoo.tools import format_amount

from .pos_config import SERVER_OPENING_MODES

CASH_BALANCE_FIELDS = [
    'cash_register_total_entry_encoding',
    'cash_register_balance_end',
    'cash_register_difference',
]


class PosSession(models.Model):
    _inherit = 'pos.session'

    pcm_opening_mode = fields.Selection(related='config_id.pcm_opening_mode', string='Opening Cash Control')
    pcm_closing_mode = fields.Selection(related='config_id.pcm_closing_mode', string='Closing Cash Control')
    pcm_opened_by_server = fields.Boolean(
        string='Opened by Server', readonly=True, copy=False,
        help="The opening cash was set by the server, without a count by the cashier.")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _pcm_is_cash_managed(self):
        """Our rules apply only to cash-controlled, non-rescue sessions."""
        self.ensure_one()
        return bool(self.config_id.cash_control and not self.rescue and self.cash_journal_id)

    def _pcm_last_session(self):
        """Previous session of the same POS, same lookup as the core cash balance."""
        self.ensure_one()
        return self.search([('config_id', '=', self.config_id.id), ('id', '<', self.id)], limit=1)

    def _pcm_effective_opening_mode(self):
        """Opening mode after edge cases.

        'Open on server with last closing balance' needs a previous session:
        the very first session of a POS falls back to a standard count so the
        initial cash is declared once by a human.
        """
        self.ensure_one()
        mode = self.config_id.pcm_opening_mode or 'standard'
        if mode == 'server_last_closing' and not self._pcm_last_session():
            return 'standard'
        return mode

    def _pcm_is_manager(self):
        self.ensure_one()
        return self.config_id._pcm_user_is_manager()

    def _pcm_refresh_cash_balance(self):
        self.invalidate_recordset(CASH_BALANCE_FIELDS)

    def _pcm_expected_cash(self):
        """Theoretical closing cash, exactly as Odoo computes it for the difference."""
        self.ensure_one()
        self._pcm_refresh_cash_balance()
        return self.currency_id.round(self.sudo().cash_register_balance_end)

    def _pcm_post_float_adjustment(self, float_amount):
        """Book the gap between the cash in the drawer and the fixed float.

        It is cash moved between the drawer and the safe/bank, not a counting
        loss or profit, so it is booked like a Cash In/Out (counterpart: the
        float transfer account, or the suspense account when none is set).
        The opening balance becomes the float, so the cashier's count is then
        compared against the float.
        """
        self.ensure_one()
        currency = self.currency_id
        adjustment = currency.round(float_amount - self.cash_register_balance_start)
        if not currency.is_zero(adjustment):
            vals = {
                'journal_id': self.cash_journal_id.id,
                'amount': adjustment,
                'date': fields.Date.context_today(self),
                'pos_session_id': self.id,
                'payment_ref': _("%s - Opening float adjustment", self.name),
            }
            if self.config_id.pcm_float_account_id:
                vals['counterpart_account_id'] = self.config_id.pcm_float_account_id.id
            self.env['account.bank.statement.line'].sudo().with_company(self.company_id).create(vals)
            self.message_post(body=_(
                "Opening float adjustment of %(amount)s: drawer reset from %(previous)s to the fixed float %(float)s.",
                amount=format_amount(self.env, adjustment, currency),
                previous=format_amount(self.env, self.cash_register_balance_start, currency),
                float=format_amount(self.env, float_amount, currency)))
        self.cash_register_balance_start = float_amount

    # ------------------------------------------------------------------
    # Opening cash control
    # ------------------------------------------------------------------
    def action_pos_session_open(self):
        res = super().action_pos_session_open()
        for session in self.filtered(lambda s: s.state == 'opening_control'):
            if not session._pcm_is_cash_managed():
                continue
            mode = session._pcm_effective_opening_mode()
            if mode == 'server_last_closing':
                session._pcm_open_on_server(session.cash_register_balance_start)
            elif mode == 'server_fixed_float':
                session._pcm_post_float_adjustment(session.config_id.pcm_float_amount)
                session._pcm_open_on_server(session.config_id.pcm_float_amount)
        return res

    def _pcm_open_on_server(self, amount):
        """Open the session without a count: the opening cash equals the
        expected opening balance, so no opening difference is posted.

        The core method is reached directly (not through our public override,
        which refuses server-side modes) so no client call, whatever its
        context, can submit an opening count for a server-opened POS.
        """
        self.ensure_one()
        super(PosSession, self).set_cashbox_pos(amount, _("Opening cash set by the server (no count)."))
        self.pcm_opened_by_server = True

    def set_cashbox_pos(self, cashbox_value, notes):
        if len(self) != 1 or not self._pcm_is_cash_managed():
            return super().set_cashbox_pos(cashbox_value, notes)
        mode = self._pcm_effective_opening_mode()
        if mode == 'standard':
            return super().set_cashbox_pos(cashbox_value, notes)

        if self.state != 'opening_control':
            raise UserError(_("The opening cash of session %s has already been recorded.", self.name))
        if mode in SERVER_OPENING_MODES:
            raise UserError(_(
                "The opening cash of %s is set by the server; it cannot be submitted from the Point of Sale.",
                self.name))
        try:
            cashbox_value = float(cashbox_value or 0.0)
        except (TypeError, ValueError):
            raise UserError(_("The opening cash amount is not a valid number."))
        if cashbox_value < 0:
            raise UserError(_("The opening cash amount cannot be negative."))

        if mode == 'suggest_float':
            self._pcm_post_float_adjustment(self.config_id.pcm_float_amount)
        return super().set_cashbox_pos(cashbox_value, notes)

    # ------------------------------------------------------------------
    # Closing cash control
    # ------------------------------------------------------------------
    def get_closing_control_data(self):
        data = super().get_closing_control_data()
        cash = data.get('default_cash_details')
        if not cash or not self._pcm_is_cash_managed():
            return data
        mode = self.config_id.pcm_closing_mode or 'standard'
        cash['pcm_closing_mode'] = mode
        if mode == 'blind' and not self._pcm_is_manager():
            # Blind count: the expected cash and everything it can be rebuilt
            # from never leave the server for a cashier.
            cash.update({'amount': 0.0, 'opening': 0.0, 'payment_amount': 0.0, 'moves': [], 'pcm_masked': True})
            data['orders_details'] = dict(data.get('orders_details') or {}, amount=0.0)
            # Only existing keys are rewritten: ClosePosPopup declares strict props.
            for key in ('payments_amount', 'pay_later_amount'):
                if key in data:
                    data[key] = 0.0
        elif mode == 'server_no_count':
            cash['pcm_server_count'] = True
        return data

    def post_closing_cash_details(self, counted_cash):
        self.ensure_one()
        if self._pcm_is_cash_managed() and self.state != 'closed':
            mode = self.config_id.pcm_closing_mode or 'standard'
            if mode == 'server_no_count':
                counted_cash = self._pcm_expected_cash()
            elif mode == 'blind':
                if counted_cash is None or counted_cash is False:
                    raise UserError(_("A cash count is required to close session %s (blind count).", self.name))
                if counted_cash < 0:
                    raise UserError(_("The counted cash cannot be negative."))
        return super().post_closing_cash_details(counted_cash)

    def _pcm_enforce_server_count(self):
        """No-count closing: the counted cash is always the expected cash.

        Called right before validation, whatever the closing path (POS UI,
        back-end button, force-close wizard), so late cash moves are included
        and no cash difference line is ever posted.
        """
        for session in self:
            if (session.state != 'closed' and session._pcm_is_cash_managed()
                    and session.config_id.pcm_closing_mode == 'server_no_count'):
                session.cash_register_balance_end_real = session._pcm_expected_cash()
                session._pcm_refresh_cash_balance()

    def _validate_session(self, balancing_account=False, amount_to_balance=0, bank_payment_method_diffs=None):
        self._pcm_enforce_server_count()
        return super()._validate_session(balancing_account, amount_to_balance, bank_payment_method_diffs)

    def _post_cash_details_message(self, state, difference, notes):
        # A blind count must not reveal the difference to the cashier through
        # the chatter; the difference is still posted in the cash register
        # (visible to accountants) and on the session form for POS managers.
        if (state == 'Closing' and len(self) == 1 and self._pcm_is_cash_managed()
                and self.config_id.pcm_closing_mode == 'blind' and not self._pcm_is_manager()):
            difference = 0.0
        return super()._post_cash_details_message(state, difference, notes)

    # ------------------------------------------------------------------
    # POS UI data (loaded once, no extra RPC)
    # ------------------------------------------------------------------
    def _pos_data_process(self, loaded_data):
        super()._pos_data_process(loaded_data)
        settings = self.config_id._pcm_get_ui_settings()
        settings.update({
            'configured_opening_mode': self.config_id.pcm_opening_mode or 'standard',
            'opening_mode': self._pcm_effective_opening_mode(),
            'opened_by_server': self.pcm_opened_by_server,
            'user_is_manager': self._pcm_is_manager(),
        })
        loaded_data['pcm_cash_control'] = settings
