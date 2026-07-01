# OmniGraph — Oracle Cloud deployment runbook (free tier)

A step-by-step guide to run OmniGraph 24/7 on one Oracle Cloud **Always-Free Ampere
A1** VM. Written for someone new to cloud. Architecture rationale lives in
[`cloud-migration.md`](../design/cloud-migration.md); this is the hands-on version.

**End state:** one Ubuntu ARM VM running Neo4j + the FastAPI backend + the frontend
(behind Caddy), reachable at `http://<your-vm-ip>/`. The long enrichment crawls (06,
gnomAD) finish there because the box is always on.

**Free-tier caveats you're accepting** (see `cloud-migration.md`): 2 CPU / 12 GB total,
and Oracle deletes the instance if it's idle >7 days — Phase 7 adds a keep-alive cron.

---

## Phase 1 — SSH key (on your laptop)

You log into the VM with an SSH key, not a password. Make one if you don't have it:

```bash
ls ~/.ssh/id_ed25519.pub 2>/dev/null || ssh-keygen -t ed25519 -C "omnigraph" -f ~/.ssh/id_ed25519 -N ""
cat ~/.ssh/id_ed25519.pub    # copy this whole line — you'll paste it in Phase 2
```

---

## Phase 2 — Create the VM (Oracle console)

1. Sign in at <https://cloud.oracle.com> → hamburger menu → **Compute → Instances → Create instance**.
2. **Name:** `omnigraph`.
3. **Image and shape → Edit → Change shape → Ampere** → pick **VM.Standard.A1.Flex**.
   Set **1 or 2 OCPUs** and **6–12 GB** memory (free tier allows up to 4/24 total, but
   free accounts are now capped at 2/12 — start with **2 OCPU / 12 GB**).
4. **Image:** Change image → **Canonical Ubuntu 22.04** (make sure it's the **aarch64/Arm**
   build — A1 is ARM).
5. **Add SSH keys → Paste public keys** → paste the `id_ed25519.pub` line from Phase 1.
6. **Networking:** leave "Create new VCN" + "Assign a public IPv4 address" (default).
7. **Create.**

> ⚠️ **"Out of capacity" / "Out of host capacity"** is the #1 free-tier snag — Oracle
> often has no free ARM capacity in a region. Workarounds: try again (a script that
> retries every few minutes often succeeds within a day), try a **different Availability
> Domain** (the AD-1/AD-2/AD-3 dropdown), or pick a **different home region** when you
> first create the account (you can't change it later). Keep retrying — it does clear.

When it's running, copy the **Public IP address** from the instance page.

Log in from your laptop:
```bash
ssh ubuntu@<VM_IP>        # 'ubuntu' is the default user for the Ubuntu image
```

---

## Phase 3 — Open the firewall (BOTH layers)

Oracle blocks everything but SSH by default, in **two** places. You must open both or
the site loads for you locally but not from the internet.

**3a. OCI Security List (the cloud firewall):**
Console → **Networking → Virtual Cloud Networks →** your VCN → **Security Lists →**
Default Security List → **Add Ingress Rules**. Add two:
- Source `0.0.0.0/0`, IP Protocol **TCP**, Destination port **80** (HTTP)
- Source `0.0.0.0/0`, IP Protocol **TCP**, Destination port **443** (HTTPS, for later)

**3b. The instance's own firewall (Ubuntu ships iptables rules that drop everything):**
On the VM:
```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save     # persist across reboots
```

> Do NOT open 7474/7687 (Neo4j) to the internet — the compose file keeps Neo4j private.

---

## Phase 4 — Install Docker + clone the repo (on the VM)

```bash
# Docker Engine + compose plugin
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && newgrp docker    # run docker without sudo

# clone (public repo; use your branch)
git clone https://github.com/owardhana/Project_OMNI.git
cd Project_OMNI
git checkout feat/roadmap-enrichment-and-chatbot
```

---

## Phase 5 — Move the graph up

You can either **transfer your current graph** (fast, keeps everything you've built) or
**rebuild from raw** on the box (slower, needs the 1.8 GB raw data + hours). Transfer is
recommended.

**On your laptop:**
```bash
bash scripts/dump_graph.sh                          # -> dumps/neo4j.dump
scp dumps/neo4j.dump ubuntu@<VM_IP>:~/Project_OMNI/dumps/
```
(The dump is a few hundred MB; the copy takes a few minutes.)

We finish loading it in Phase 6 (after the volume exists).

---

## Phase 6 — Configure and run

**On the VM**, set secrets:
```bash
cp deploy/.env.prod.example deploy/.env.prod
nano deploy/.env.prod      # set NEO4J_PASSWORD + OPENROUTER_API_KEY; leave SITE_ADDRESS=:80
```

Create the volume + load the graph, then bring the whole stack up:
```bash
mkdir -p dumps   # (already has neo4j.dump from Phase 5)

# create the neo4j volume by starting it once, then load the dump into it
docker compose -f docker-compose.prod.yml --env-file deploy/.env.prod up -d neo4j
bash scripts/restore_graph.sh

# build + start backend + web (first build takes a few minutes on 2 CPUs)
docker compose -f docker-compose.prod.yml --env-file deploy/.env.prod up -d --build
```

Check it (⚠️ **every** `docker compose` subcommand needs `--env-file deploy/.env.prod`,
not just `up` — compose interpolates `NEO4J_PASSWORD` even for `ps`/`logs`/`stop`, and
only reads the file when you pass it):
```bash
docker compose -f docker-compose.prod.yml --env-file deploy/.env.prod ps   # all healthy?
curl -s localhost/api/gene/TP53 | head -c 200          # backend via Caddy
docker logs omnigraph-neo4j 2>&1 | tail -60            # raw docker logs need no --env-file
```
Then open **`http://<VM_IP>/`** in your browser — the 3D graph + the chat panel.

> **HTTPS later:** buy/point a domain at `<VM_IP>` (an A record), set
> `SITE_ADDRESS=yourdomain.com` in `deploy/.env.prod`, and
> `docker compose -f docker-compose.prod.yml --env-file deploy/.env.prod up -d web`.
> Caddy fetches a Let's Encrypt cert automatically. (Let's Encrypt can't certify a bare
> IP, which is why we start on HTTP.)

---

## Phase 7 — Keep it alive & healthy

**Finish the enrichment crawls** (they run happily here 24/7). Neo4j is bound to
`127.0.0.1:7687` on the box, so run them from the host with a tiny venv (these two
crawls only need `neo4j httpx python-dotenv` — no pandas/scipy). Use `nohup` so they
survive your SSH session:
```bash
cd ~/Project_OMNI
python3 -m venv etl/.venv && etl/.venv/bin/pip install -q neo4j httpx python-dotenv
# point ETL at the host-local Neo4j + your password
export NEO4J_URI=bolt://localhost:7687 NEO4J_PASSWORD=<your NEO4J_PASSWORD>
nohup etl/.venv/bin/python -u etl/16_gnomad_af.py   > ~/gnomad.log 2>&1 &   # gnomAD AF
nohup etl/.venv/bin/python -u etl/06_uniprot_enrich.py > ~/uniprot.log 2>&1 &  # protein text
```
Both are resumable (safe to re-run if interrupted). **Embeddings need a driven
backfill, not just the nightly agent:** the transferred graph has ~20k proteins with a
`summary_text` but no `embedding` (the nightly EmbeddingAgent does one batch of
`EMBEDDING_AGENT_BATCH_SIZE`=50, which would take months to clear 20k). Two levers:
raise `EMBEDDING_AGENT_BATCH_SIZE` in `deploy/.env.prod` (re-up the backend), and call
the run endpoint repeatedly until none remain. Check what's left directly in Neo4j:
```bash
docker exec omnigraph-neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" \
  "MATCH (p:Protein) WHERE p.summary_text IS NOT NULL AND p.embedding IS NULL RETURN count(p)"
# then, until that count is 0:
curl -s -XPOST localhost/api/admin/agents/embedding/run
```
(~1–2h and ~$0.20 total for text-embedding-3-small once the batch size is raised.)

**Keep-alive cron (avoids the 7-day idle deletion on free tier):** generate a little
traffic/CPU periodically so Oracle doesn't flag the box idle.
```bash
( crontab -l 2>/dev/null; echo "*/30 * * * * curl -s localhost/api/gene/TP53 >/dev/null 2>&1" ) | crontab -
```

**Backups (you own reliability when self-hosting):** dump the graph weekly.
```bash
( crontab -l 2>/dev/null; echo "0 4 * * 0 cd ~/Project_OMNI && bash scripts/dump_graph.sh" ) | crontab -
```

**Updating the app later:**
```bash
cd ~/Project_OMNI && git pull
docker compose -f docker-compose.prod.yml --env-file deploy/.env.prod up -d --build
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| **SSH itself times out** after editing the Security List (timeout, not "refused") | You broke the default **port-22** ingress rule. Re-add ingress: Source `0.0.0.0/0`, TCP, **Destination** port `22` (a classic slip is typing 22 in *Source* port), **Stateless = No**. Confirm it's the SL attached to *this VM's subnet* (Instance → Attached VNICs → VNIC → Subnet). |
| Pasted Phase 4 block silently skipped `git clone` (empty home dir) | `newgrp docker` opens a subshell that **swallows the rest of a pasted block**. Run the `git clone` lines on their own, or `exit` and re-SSH first (re-login also activates docker-group membership, so `docker` works without sudo). |
| `required variable NEO4J_PASSWORD is missing a value` | You omitted `--env-file deploy/.env.prod` on that compose command. It's required on *every* subcommand (ps/logs/stop/down), not just `up`. Your password isn't wrong. |
| SSH works, site doesn't load | Firewall — you missed Phase **3b** (iptables) or **3a** (Security List). |
| "Out of host capacity" on create | Retry / different AD / different region (Phase 2 note). |
| Backend unhealthy, Neo4j OOM | Lower `NEO4J_HEAP`/`NEO4J_PAGECACHE` in `deploy/.env.prod` (try 2G/3G) and re-up. 12 GB is tight. |
| Chat streams all at once | Confirm you're hitting it through Caddy (`/api/chat/stream`), which sets `flush_interval -1`. |
| Instance got deleted | It was idle >7 days — recreate and add the Phase 7 keep-alive cron (or upgrade to PAYG). |
