# Caddy image with curl for the container healthcheck.
#
# The stock caddy image is Alpine-based but does not guarantee a client that
# can verify an internal certificate. curl lets the healthcheck call the same
# TLS listener clients use.

FROM caddy:2

RUN apk add --no-cache curl
