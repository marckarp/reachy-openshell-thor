# openshell/ — the control plane, and how it was set up on this Thor

OpenShell runs the agent in a sandbox and decides, per request, what may leave it. Files here:

| File | Purpose |
|---|---|
| `policy-motion-enabled.yaml` | sandbox may call `POST /api/move/goto`: robot moves |
| `policy-motion-disabled.yaml` | same minus that one rule: robot refuses with 403 |
| `reachy-nvidia.yaml` | provider profile: NVIDIA key, allowed host, allowed binaries |
| `local-vllm.yaml` | provider profile for the Thor's own vLLM on port 8001, no credential |
| `gateway.toml` | copy of `~/.config/openshell/gateway.toml` (microVM driver) |
| `99-kvm-openshell.rules` | udev rule installed to `/etc/udev/rules.d/` for `/dev/kvm` access |

Both policies also allow the daemon's read endpoints (`GET /api/daemon/status`,
`GET /api/move/running`), `POST /api/move/stop`, and the speaker service on port 8042
(`GET /sounds`, `POST /play`).
The model endpoint is not in the policies: the provider grants it.

## Everyday commands

```sh
openshell status                                    # gateway up?
openshell sandbox get reachy-agent                  # Phase: Ready?
openshell policy set reachy-agent --policy openshell/policy-motion-enabled.yaml --wait
openshell policy set reachy-agent --policy openshell/policy-motion-disabled.yaml --wait
openshell logs reachy-agent | grep -E "ALLOWED|DENIED" | tail
openshell sandbox exec --name reachy-agent -- sh    # shell inside the sandbox
```

## Setup steps that were run (2026-10-12), in order

1. **Install** (needs sudo). Installs CLI, gateway binary, a systemd user unit.
   ```sh
   curl -LsSf https://raw.githubusercontent.com/NVIDIA/OpenShell/main/install.sh | sudo sh
   ```
2. **Docker access for the user** (the gateway needs it even in VM mode to read images).
   ```sh
   sudo usermod -aG docker $USER      # takes effect at next login
   sudo setfacl -m u:$USER:rw /var/run/docker.sock   # until then
   ```
3. **Gateway config.** The Jetson kernel has no Landlock, so the Docker driver fails closed.
   Use the KVM microVM driver. Write `~/.config/openshell/gateway.toml` (copy of `gateway.toml` here).
4. **KVM access.** logind rewrites ACLs on `/dev/kvm`, so use a udev rule:
   ```sh
   sudo cp openshell/99-kvm-openshell.rules /etc/udev/rules.d/
   sudo udevadm control --reload-rules && sudo udevadm trigger --name-match=kvm
   sudo usermod -aG kvm $USER
   ```
5. **Start and register the gateway.**
   ```sh
   systemctl --user enable --now openshell-gateway
   sudo loginctl enable-linger $USER
   openshell gateway add https://127.0.0.1:17670 --local --name openshell
   openshell status
   ```
6. **Provider** (API key stays in the gateway; export it first from `~/.bashrc`).
   ```sh
   openshell profile import -f openshell/reachy-nvidia.yaml
   openshell provider create --name reachy-nvidia --type reachy-nvidia --credential NVIDIA_API_KEY
   ```
7. **Build the image and create the sandbox.** See `agent/README.md`.

## Two policy details that bit us

- OpenShell matches the real binary, not the symlink. Policies list both
  `/opt/venv/bin/python` and `/usr/local/bin/python3.12`.
- In VM mode `host.openshell.internal` is reached via host loopback, so `allowed_ips`
  includes `127.0.0.1/32`.
