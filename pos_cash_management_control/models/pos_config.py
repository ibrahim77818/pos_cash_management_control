# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

OPENING_MODES = [
    ('standard', 'Standard Count'),
    ('suggest_float', 'Count with Suggested Fixed Float'),
    ('server_last_closing', 'Open on Server with Last Closing Balance'),
    ('server_fixed_float', 'Open on Server with Fixed Float'),
]

CLOSING_MODES = [
    ('standard', 'Standard Count (Show Difference)'),
    ('blind', 'Blind Count (Hide Expected Cash from Cashiers)'),
    ('server_no_count', 'No Count (Server Takes Expected Cash as Counted)'),
]

FLOAT_OPENING_MODES = ('suggest_float', 'server_fixed_float')
SERVER_OPENING_MODES = ('server_last_closing', 'server_fixed_float')

# Fields that the running POS caches at load time; they may not change while
# a session of the POS is in progress, otherwise UI and server would diverge.
PCM_LOCKED_FIELDS = (
    'pcm_opening_mode', 'pcm_float_amount', 'pcm_float_account_id',
    'pcm_closing_mode', 'pcm_hide_daily_sale',
)


class PosConfig(models.Model):
    _inherit = 'pos.config'

    pcm_opening_mode = fields.Selection(
        OPENING_MODES, string='Opening Cash Control', required=True, default='standard',
        help="How the opening cash of a session is determined:\n"
             "- Standard Count: the cashier counts the cash; Odoo pre-fills the last closing balance.\n"
             "- Count with Suggested Fixed Float: the cashier counts the cash; the fixed float is pre-filled "
             "and the gap with the last closing balance is booked as a float transfer.\n"
             "- Open on Server with Last Closing Balance: no count, the session opens with the last "
             "closing balance (the very first session falls back to a standard count).\n"
             "- Open on Server with Fixed Float: no count, the session opens with the fixed float and "
             "the gap with the last closing balance is booked as a float transfer.")
    pcm_float_amount = fields.Monetary(
        string='Fixed Float Amount', currency_field='currency_id',
        help="Cash float the drawer starts each session with.")
    pcm_float_account_id = fields.Many2one(
        'account.account', string='Float Transfer Account',
        domain="[('company_ids', 'parent_of', company_id), ('deprecated', '=', False)]",
        help="Counterpart account of the float adjustment (cash moved between the drawer and the safe). "
             "Leave empty to book it on the suspense account, like a standard Cash In/Out.")
    pcm_closing_mode = fields.Selection(
        CLOSING_MODES, string='Closing Cash Control', required=True, default='standard',
        help="How the closing cash of a session is counted:\n"
             "- Standard Count: the cashier sees the expected cash and the difference.\n"
             "- Blind Count: cashiers enter the counted cash without seeing the expected amount or the "
             "difference; POS managers keep full visibility.\n"
             "- No Count: the server records the expected cash as counted; no difference is posted.")
    pcm_hide_daily_sale = fields.Boolean(
        string='Hide Daily Sale from Cashiers',
        help="Only POS managers can download or print the Daily Sale report of this Point of Sale.")

    @api.constrains('pcm_float_amount')
    def _check_pcm_float_amount(self):
        for config in self:
            if config.pcm_float_amount < 0:
                raise ValidationError(_("The fixed float amount of %s cannot be negative.", config.name))

    @api.constrains('pcm_float_account_id', 'company_id')
    def _check_pcm_float_account_company(self):
        for config in self:
            account = config.pcm_float_account_id
            if account and not account.filtered_domain(
                    self.env['account.account']._check_company_domain(config.company_id)):
                raise ValidationError(_(
                    "The float transfer account of %s must belong to the company %s.",
                    config.name, config.company_id.name))

    def _pcm_has_running_session(self):
        self.ensure_one()
        return bool(self.session_ids.filtered(lambda s: s.state != 'closed' and not s.rescue))

    def write(self, vals):
        locked = [f for f in PCM_LOCKED_FIELDS if f in vals]
        if locked:
            for config in self:
                changed = [
                    f for f in locked
                    if config._fields[f].convert_to_write(config[f], config) != vals[f]
                    and not (not config[f] and not vals[f])
                ]
                if changed and config._pcm_has_running_session():
                    raise UserError(_(
                        "You cannot change the cash control settings of %s while a session is in progress. "
                        "Close the session first.", config.name))
        return super().write(vals)

    def _pcm_get_ui_settings(self):
        """Settings sent once to the POS UI at session load."""
        self.ensure_one()
        return {
            'opening_mode': self.pcm_opening_mode or 'standard',
            'closing_mode': self.pcm_closing_mode or 'standard',
            'float_amount': self.pcm_float_amount or 0.0,
            'hide_daily_sale': bool(self.pcm_hide_daily_sale),
        }

    def _pcm_user_is_manager(self, user=None):
        """Role check used everywhere server-side: POS manager group of the config."""
        self.ensure_one()
        if self.env.su and user is None:
            return True
        user = user or self.env.user
        group = self.group_pos_manager_id or self.env.ref('point_of_sale.group_pos_manager')
        return group in user.groups_id
