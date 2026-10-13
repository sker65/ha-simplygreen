"""Constants for SimplyGreen."""
DOMAIN = "simplygreen"
CONF_MLIID = "mliid"
CONF_BACKFILL_DAYS = "backfill_days"
DEFAULT_BACKFILL_DAYS = 30
CONF_SURCHARGE_CT = "surcharge_ct"  # net ct/kWh added to EPEX (grid fees, levies, margin)
CONF_VAT_PERCENT = "vat_percent"
DEFAULT_SURCHARGE_CT = 0.0
DEFAULT_VAT_PERCENT = 19.0
UPDATE_HOURS = 3
REFETCH_DAYS = 3  # data arrives delayed; re-import the last days on every run
