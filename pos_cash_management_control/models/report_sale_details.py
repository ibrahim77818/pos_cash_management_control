# -*- coding: utf-8 -*-
from odoo import _, api, models
from odoo.exceptions import AccessError


class ReportSaleDetails(models.AbstractModel):
    _inherit = 'report.point_of_sale.report_saledetails'

    @api.model
    def _pcm_check_daily_sale_access(self, config_ids=False, session_ids=False):
        """Server-side guard of 'Hide Daily Sale from Cashiers'.

        Both the POS download (session ids) and the printed ticket
        (get_sale_details RPC) go through here, so hiding the buttons is not
        the only protection.
        """
        if self.env.su:
            return
        configs = self.env['pos.config']
        if config_ids:
            configs |= configs.browse(config_ids)
        if session_ids:
            configs |= self.env['pos.session'].sudo().browse(session_ids).config_id.with_env(self.env)
        for config in configs.sudo().filtered('pcm_hide_daily_sale'):
            if not config._pcm_user_is_manager(self.env.user):
                raise AccessError(_(
                    "Only Point of Sale managers can view the Daily Sale report of %s.", config.name))

    @api.model
    def get_sale_details(self, date_start=False, date_stop=False, config_ids=False, session_ids=False):
        self._pcm_check_daily_sale_access(config_ids, session_ids)
        return super().get_sale_details(date_start, date_stop, config_ids, session_ids)
