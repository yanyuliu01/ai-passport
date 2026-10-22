<p align="right"><a href="README.zh_CN.md">简体中文</a> · <strong>English</strong></p>

# Independent encrypted relay

This is a deployment template for a public-reachable host with Docker Compose,
a domain pointing to it, and inbound TCP 80/443. It does not require Tailscale.
A QR code pairs devices; it does not create an Internet route. A separately
purchased server is optional when an existing reachable host is available.
The current Mac has no verified direct public route. This template has not
been deployed or validated on an external host.

From `companion/desktop/`, run `npm run build`, then copy
`dist-main/relay/server.js` into this folder. Deploy a copy of this folder to
an authorized host. Run `sh setup.sh relay.example.com`, review `.env`, then
`docker compose up -d --build`. Setup preserves any existing `.env` and never
prints the access token. Keep it private. The relay container has no published
port; Caddy provides HTTPS/WSS and automatic certificates. No request/content
access logs are configured. The relay forwards bounded authenticated ciphertext
and keeps no chat history or offline mailbox; endpoint software decrypts it.

Enter the WSS URL and access token once in desktop Advanced connection settings.
Generate a two-minute QR code. In Android 0.29.0+, use Connection and settings →
Scan to connect a computer; compare the fingerprint and approve on the computer.
The app retains up to eight computer targets and a separate pinned device key
per connection. Same-space history uses the Runtime's canonical identities;
scanning unrelated spaces does not silently merge their histories.

Stop with `docker compose down`. Preserve `.env` and certificate volumes for
recovery; restarting the relay does not delete endpoint history/pairing/outboxes.
Change the relay token and redistribute it if access must be revoked. Per-device
revocation UI is not implemented. No service was deployed by this change.
