"""Constants for SimplyGreen."""
DOMAIN = "simplygreen"
CONF_MLIID = "mliid"
CONF_BACKFILL_DAYS = "backfill_days"
DEFAULT_BACKFILL_DAYS = 30
UPDATE_HOURS = 3
REFETCH_DAYS = 3  # data arrives delayed; re-import the last days on every run
