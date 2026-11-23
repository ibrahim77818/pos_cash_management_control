# -*- coding: utf-8 -*-
from odoo import _, fields, models
from odoo.exceptions import UserError
from odoo.tools import format_amount, plaintext2html

CASH_BALANCE_FIELDS = [
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
    pcm_float_statement_line_id = fields.Many2one(
        'account.bank.statement.line', string='Opening Float Transfer', readonly=True, copy=False,
        help="Cash journal line booking the cash moved between the drawer and the safe to reach the fixed float.")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _pcm_is_cash_managed(self):
        """Our rules apply only to cash-controlled, non-rescue sessions."""
        self.ensure_one()
        return bool(self.config_id.cash_control and not self.rescue and self.cash_journal_id)

    def _pcm_last_session(self):
        """Previous session of the same POS."""
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
        loss or profit, so it is booked like a Cash In/Out on the cash journal
        (counterpart: the float transfer account, or the suspense account).

        Since Odoo 18 the expected cash of a session starts from its opening
        balance, so this line is deliberately NOT linked to the session:
        linking it would count the transfer twice in the expected cash. It is
        traced on the session through `pcm_float_statement_line_id`.
        """
        self.ensure_one()
        currency = self.currency_id
        previous = self.cash_register_balance_start
        adjustment = currency.round(float_amount - previous)
        if not currency.is_zero(adjustment):
            vals = {
                'journal_id': self.cash_journal_id.id,
                'amount': adjustment,
                'date': fields.Date.context_today(self),
                'payment_ref': _("%s - Opening float adjustment", self.config_id.name),
            }
            if self.config_id.pcm_float_account_id:
                vals['counterpart_account_id'] = self.config_id.pcm_float_account_id.id
            line = self.env['account.bank.statement.line'].sudo().with_company(self.company_id).create(vals)
            self.sudo().pcm_float_statement_line_id = line
            self.message_post(body=_(
                "Opening float adjustment of %(amount)s: drawer reset from %(previous)s to the fixed float %(float)s.",
                amount=format_amount(self.env, adjustment, currency),
                previous=format_amount(self.env, previous, currency),
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
            if session._pcm_effective_opening_mode() in ('server_last_closing', 'server_fixed_float'):
                # Public entry point: also assigns the session sequence.
                session.set_opening_control(0.0, _("Opening cash set by the server (no count)."))
                session.pcm_opened_by_server = True
        return res

    def _set_opening_control_data(self, cashbox_value, notes):
        """Enforce the opening mode, whatever amount the client sends.

        Server modes ignore the submitted amount entirely, so no client call
        can choose the opening balance of a server-opened POS. Odoo itself
        refuses a second opening (state is no longer opening_control).
        """
        if len(self) != 1 or self.state != 'opening_control' or not self._pcm_is_cash_managed():
            return super()._set_opening_control_data(cashbox_value, notes)
        mode = self._pcm_effective_opening_mode()
        if mode == 'standard':
            return super()._set_opening_control_data(cashbox_value, notes)

        if mode == 'server_last_closing':
            cashbox_value = self.cash_register_balance_start
        elif mode == 'server_fixed_float':
            self._pcm_post_float_adjustment(self.config_id.pcm_float_amount)
            cashbox_value = self.config_id.pcm_float_amount
        else:  # suggest_float
            try:
                cashbox_value = float(cashbox_value or 0.0)
            except (TypeError, ValueError):
                raise UserError(_("The opening cash amount is not a valid number."))
            if cashbox_value < 0:
                raise UserError(_("The opening cash amount cannot be negative."))
            self._pcm_post_float_adjustment(self.config_id.pcm_float_amount)
        return super()._set_opening_control_data(cashbox_value, notes)

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
            # pos_hr (a dependency, so already applied by super()) adds a
            # per-employee breakdown that would let the cashier rebuild the total.
            for key in ('amount_per_employee', 'moves_per_employee'):
                if key in cash:
                    cash[key] = []
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

    def _post_cash_details_message(self, state, expected, difference, notes):
        # A blind count must not reveal the expected cash or the difference to
        # the cashier through the chatter; both stay on the session form for
        # POS managers and in the cash register for accountants.
        if (state == 'Closing' and len(self) == 1 and self._pcm_is_cash_managed()
                and self.config_id.pcm_closing_mode == 'blind' and not self._pcm_is_manager()):
            if notes:
                self.message_post(body=plaintext2html(notes))
            return
        return super()._post_cash_details_message(state, expected, difference, notes)

    # ------------------------------------------------------------------
    # POS UI data (loaded once with the session, no extra RPC)
    # ------------------------------------------------------------------
    def _load_pos_data(self, data):
        res = super()._load_pos_data(data)
        records = res.get('data') or []
        if records:
            session = self.browse(records[0]['id'])
            settings = session.config_id._pcm_get_ui_settings()
            settings.update({
                'configured_opening_mode': session.config_id.pcm_opening_mode or 'standard',
                'opening_mode': session._pcm_effective_opening_mode(),
                'opened_by_server': session.pcm_opened_by_server,
                'user_is_manager': session._pcm_is_manager(),
            })
            records[0]['_pcm_cash_control'] = settings
        return res
