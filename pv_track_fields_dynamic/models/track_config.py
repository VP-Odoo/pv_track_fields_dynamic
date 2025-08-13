from odoo import api, fields, models

class TrackFieldConfig(models.Model):
    _name = 'track.fields.config'
    _description = 'Dynamic Field Tracking Configuration'
    _rec_name = 'model_id'
    _order = 'model_id, id'
    _table = 'track_fields_config'  # explicit for the uninstall_hook

    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        'res.company',
        default=lambda s: s.env.company,
        required=True,
        index=True,
    )

    model_id = fields.Many2one(
        'ir.model',
        required=True,
        domain=[('transient', '=', False)],
        ondelete='cascade',  # if model is uninstalled, delete config automatically
        help="Business model whose field changes should be tracked."
    )

    field_ids = fields.Many2many(
        'ir.model.fields',
        'track_fields_config_field_rel', 'config_id', 'field_id',
        string='Trackable Fields',
        domain="[('model_id', '=', model_id), ('store', '=', True), ('ttype', 'not in', ('binary',))]",
        help="Pick stored, non-binary fields to track."
    )

    exclude_empty_changes = fields.Boolean(
        default=True,
        help="Skip logs when a value effectively doesn't change (e.g., blank to blank)."
    )
    show_old_values = fields.Boolean(default=True)
    show_new_values = fields.Boolean(default=True)
    group_changes_per_record = fields.Boolean(
        default=True,
        help="One chatter message per record write instead of one per field."
    )

    _sql_constraints = [
        ('uniq_company_model', 'unique(company_id, model_id)',
         'There is already a tracking configuration for this model in this company.')
    ]

    @api.onchange('model_id')
    def _onchange_model_id_clear_fields(self):
        # Reset selected fields when the model changes to avoid cross-model leakage
        if self.model_id:
            self.field_ids = [(5, 0, 0)]
