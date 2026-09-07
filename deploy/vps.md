# Deploy Janus to a VPS

This guide deploys Janus to a Hetzner Cloud VPS with Ubuntu 24.04, runs it
under systemd, and exposes it at your domain with Caddy for automatic HTTPS.

Domain: janusapp.xyz (registered through Vercel)

Tested state: 25 tests green, mainnet proof live.

---

## 1. Rent the server

In the Hetzner Cloud console create a server:

- Location: your nearest region
- Image: Ubuntu 24.04
- Type: smallest available CX plan with NVMe (CX23 if in stock, else CX33)
- Add your SSH key during creation

Note the server IPv4 address and the root password if no key was added.

The server is billed hourly with a monthly cap. It bills until deleted even
when powered off, so delete it if you stop using it.

## 2. First login

From your machine:

```
ssh root@YOUR_SERVER_IP
```

If you used a key it connects directly. If not, it asks for the root password.

## 3. Create a normal user

Run everything as a normal user, not root. Janus runs as this user.

```
adduser janus
usermod -aG sudo janus
su - janus
```

## 4. Install uv

Janus needs Python 3.12 and uv. uv installs and manages the exact Python.

```
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.local/bin/env
uv --version
```

## 5. Get the code

```
cd ~
git clone https://github.com/vickman787/janus.git
cd janus
uv sync
```

This installs every dependency including sibyl memory, web3, and fastapi.

## 6. Configure the environment

```
cp .env.example .env
nano .env
```

Set:

```
JANUS_RPC_URL=https://mainnet.base.org
JANUS_CHAIN_ID=8453
JANUS_EXPLORER_URL=https://basescan.org
JANUS_OWNER_KEY=YOUR_OWNER_KEY
JANUS_TARGET_KEY=YOUR_TARGET_KEY
```

The memory database defaults to `~/.sibyl-memory/janus.db` for the janus user.
It lives on the server disk and survives restarts. That file is the persistent
memory Janus depends on.

Keep the .env readable only by the janus user:

```
chmod 600 .env
```

## 7. Verify the app runs

```
uv run python -c "from janus.web import make_app; print('import ok')"
```

Then start it briefly by hand to confirm it serves:

```
uv run uvicorn janus.web:make_app --factory --host 127.0.0.1 --port 8000
```

Visit http://YOUR_SERVER_IP:8000 if your firewall allows, or just confirm the
log shows "Application startup complete", then stop it with Ctrl + C.

## 8. Run under systemd

Create the service file:

```
sudo nano /etc/systemd/system/janus.service
```

Paste exactly this, replacing the path only if your home directory differs:

```
[Unit]
Description=Janus restart safe onchain execution agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=janus
WorkingDirectory=/home/janus/janus
ExecStart=/home/janus/.local/bin/uv run uvicorn janus.web:make_app --factory --host 127.0.0.1 --port 8000
Restart=always
RestartSec=3
EnvironmentFile=/home/janus/janus/.env
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Enable and start it:

```
sudo systemctl daemon-reload
sudo systemctl enable --now janus
sudo systemctl status janus
```

Confirm it is listening:

```
curl -s http://127.0.0.1:8000/api/config
```

The Restart=always line is not decoration. A crash or reboot restarts Janus,
which is the exact interruption Janus is designed to survive. After a restart,
the checkpoint in the memory db is still there.

## 9. Install Caddy for HTTPS

Caddy gets certificates automatically and proxies to Janus.

```
sudo apt update
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install -y caddy
```

Create the site config:

```
sudo nano /etc/caddy/Caddyfile
```

Paste:

```
janusapp.xyz {
    reverse_proxy 127.0.0.1:8000
}
```

Reload Caddy:

```
sudo systemctl reload caddy
```

Caddy fetches the certificate from Let's Encrypt automatically and renews it.

## 10. Point the domain at the server

The domain was registered through Vercel. In the Vercel domain settings for
janusapp.xyz, add an A record pointing janusapp.xyz at YOUR_SERVER_IP, and
optionally a CNAME or A record for www if you want the www host.

DNS can take minutes to hours to propagate. Wait, then check:

```
dig janusapp.xyz
```

## 11. Verify the whole stack

From the server:

```
curl -s https://janusapp.xyz/api/config
```

You should see chain id 8453 and mainnet true. Then open
https://janusapp.xyz in a browser. The dashboard shows the operations present
in the server memory db.

The live memory on this fresh server starts empty. To populate it with the
three real mainnet operations, run the seed script described in the README or
re run the operations from this server. Without seeding, the dashboard shows
zero operations but the network guard, memory explorer, and status page all
work.

## 12. Firewall

Hetzner has a cloud firewall you can attach in the console. Allow SSH on port
22, HTTPS on 443, and HTTP on 80. Do not expose port 8000 publicly. Caddy
owns ports 80 and 443 and Janus only listens on 127.0.0.1.

If you also use ufw on the server:

```
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

## 13. Seed the server memory from your local proof

The server starts with an empty memory db. The three real mainnet operations
live in the Sibyl memory file on your development machine. To show them on the
hosted app, copy that file. This moves real state, not fake data, and it is a
working proof that memory is portable.

From your development machine:

```
scp ~/.sibyl-memory/janus.db janus@YOUR_SERVER_IP:~/.sibyl-memory/janus.db
```

Best done while Janus is stopped on the server so the file is not being
written:

```
sudo systemctl stop janus
scp ~/.sibyl-memory/janus.db janus@YOUR_SERVER_IP:~/.sibyl-memory/janus.db
sudo systemctl start janus
```

The file contains checkpoints, contract addresses, wallet addresses, and
transaction hashes. All of that is public onchain data. It does not contain
private keys. The verification script in the repo checks this on the real db:

```
uv run python scripts/check_memory_no_keys.py
```

It scans the file for every 64 char hex blob and confirms neither configured
key is present. Run it before copying if you want proof on your own machine.

Seeding does not re run the transfers. Nothing is signed or sent. The server
reads the imported checkpoints and reconciles them against live Base mainnet,
which works because the contracts are real and public.

## 14. Common issues

App starts but domain times out. The A record is wrong or still propagating,
or Caddy has no cert yet. Check `sudo systemctl status caddy` and run
`sudo journalctl -u janus -n 50`.

Page loads but shows not mainnet. The .env chain id or rpc url is wrong.
Check `curl -s https://janusapp.xyz/api/config`.

Deploy and begin buttons error. The keys are missing or wrong in .env. This
is expected if you intentionally left keys out for a read only public site.

Memory resets on restart. The memory db path is not where you think. Confirm
`~/.sibyl-memory/janus.db` exists for the janus user and is on the server
disk, not in a tmpfs.

Reboot the server once at the end and confirm Janus and Caddy come back on
their own. That is the real cold start test, and it is the product's thesis.
