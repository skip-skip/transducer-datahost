# TLS certificates

Caddy terminates TLS for the ingest gateway and the read API. The certificate
must match the name clients use to reach the VM. The recommended internal name
is `transducer.internal`. Add the VM's LAN IP too if clients will connect by IP
address; IP literals are not sent as SNI, and Caddy's `default_sni` covers the
handshake, but the certificate still has to contain the IP for verification.

The certificate and key are **not** committed. This directory is mounted into
the Caddy container read-only at `/etc/caddy/certs`.

## Option A: mkcert (recommended for an internal VM)

Install [mkcert](https://github.com/FiloSottile/mkcert) on the VM, then:

```bash
cd certs
mkcert -install
mkcert \
  -cert-file server.crt \
  -key-file server.key \
  transducer.internal localhost 127.0.0.1 192.168.4.89
```

Replace `192.168.4.89` with the VM's actual LAN address (`hostname -I`).

`mkcert -install` adds the local root CA to the VM's trust store. For Windows
clients, either run `mkcert -install` there too or copy the root CA
(`mkcert -CAROOT`) and pass it to the uploader with `--ca`.

## Option B: an existing internal CA

Request a server certificate for the VM name with the internal CA and place the
full chain and key here:

```text
certs/server.crt   certificate plus intermediate chain
certs/server.key   private key (keep permissions 0600)
```

Clients must trust the issuing CA. The uploader accepts the CA bundle with
`--ca <path>`; browsers and dashboards use the operating system trust store.

## Name resolution

Add an internal DNS record (or hosts-file entry) for `transducer.internal`
pointing at the VM. If the VM name differs, set `INGEST_HOSTNAME` in `.env`;
Caddy uses it as `default_sni` and the certificate must match. The listener is a
catch-all, so requests addressed to the VM's IP also reach the APIs, provided
the certificate includes that IP (or the client skips verification).

## Rotation

Replace `server.crt` and `server.key`, then reload Caddy:

```bash
docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile
```

Never commit `server.crt`, `server.key`, or a CA private key.
