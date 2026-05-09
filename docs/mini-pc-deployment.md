# Mini PC Deployment

SelfieTL is deployed permanently on the mini PC over Tailscale.

- Service name: `selfietl.service`
- Host: `100.96.182.111`
- Port: `8766`
- URL: `http://100.96.182.111:8766`
- Working directory: `/home/davis/selfietl`
- Data directory: `/home/davis/.selfietl`
- Env file: `/home/davis/.config/selfietl/selfietl.env`

The app is managed as a system systemd service, similar to `drive-web.service`, and runs as user `davis`.

Useful commands on the mini PC:

```bash
sudo systemctl status selfietl.service
sudo systemctl restart selfietl.service
sudo journalctl -u selfietl.service -f
```

Deployment command:

```bash
/home/davis/selfietl/.venv/bin/python -m selfietl serve --host 0.0.0.0 --port 8766 --data-dir /home/davis/.selfietl
```

Tailscale Serve is intentionally left unchanged because `/` currently routes to Drive Web on port `8730`.
