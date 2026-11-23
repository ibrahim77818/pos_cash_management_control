# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    pos_pcm_opening_mode = fields.Selection(
        related='pos_config_id.pcm_opening_mode', readonly=False)
    pos_pcm_float_amount = fields.Monetary(
        related='pos_config_id.pcm_float_amount', readonly=False, currency_field='currency_id')
    pos_pcm_float_account_id = fields.Many2one(
        related='pos_config_id.pcm_float_account_id', readonly=False)
    pos_pcm_closing_mode = fields.Selection(
        related='pos_config_id.pcm_closing_mode', readonly=False)
    pos_pcm_hide_daily_sale = fields.Boolean(
        related='pos_config_id.pcm_hide_daily_sale', readonly=False)
