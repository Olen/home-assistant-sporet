"""Constants for the Sporet integration."""

DOMAIN = "sporet"

# API Configuration
API_BASE_URL = "https://api.sporet.no/loypeapi/public/skiroutes"
API_SEGMENT_URL = "https://api.sporet.no/loypeapi/public/skisegments"
UPDATE_INTERVAL_SECONDS = 900  # 15 minutes

# OpenID Connect - the same public client the sporet.no web app uses, taken
# from its own window.mapConfig. A public client, so no secret is involved.
OIDC_TOKEN_URL = "https://login.sporet.no/connect/token"
OIDC_CLIENT_ID = "geodata-public"
# Refresh this long before the access token actually expires
TOKEN_REFRESH_MARGIN_SECONDS = 3600
# ...and refresh at least this often regardless. The access token lasts 30
# days but the refresh token has a lifetime of its own (14 days by default in
# OpenIddict), reset every time it is used. Waiting for the access token to
# near its expiry would let the refresh token die first, so refresh well
# inside any plausible refresh-token lifetime and keep rotating.
TOKEN_MAX_AGE_SECONDS = 7 * 24 * 3600

# Configuration Fields
CONF_BEARER_TOKEN = "bearer_token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_IS_SEGMENT = "is_segment"
CONF_SLOPE_ID = "slope_id"

# Attribution
ATTRIBUTION = "Data provided by Sporet.no"
