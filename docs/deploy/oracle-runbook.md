# OmicGraph — Oracle Cloud deployment runbook (free tier)

A step-by-step guide to run OmicGraph 24/7 on one Oracle Cloud **Always-Free Ampere
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
ls ~/.ssh/id_ed25519.pub 2>/dev/null || ssh-keygen -t ed25519 -C "omicgraph" -f ~/.ssh/id_ed25519 -N ""
cat ~/.ssh/id_ed25519.pub    # copy this whole line — you'll paste it in Phase 2
```

---

## Phase 2 — Create the VM (Oracle console)

1. Sign in at <https://cloud.oracle.com> → hamburger menu → **Compute → Instances → Create instance**.
2. **Name:** `omicgraph`.
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
docker logs omicgraph-neo4j 2>&1 | tail -60            # raw docker logs need no --env-file
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
sudo apt-get install -y python3-venv    # Ubuntu minimal ships without ensurepip -> venv fails
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
docker exec omicgraph-neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" \
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

---

## Phase 8 — Operating (monitor · update · reinitialize)

Day-2 operations. Shorthand used below: `DC = docker compose -f
docker-compose.prod.yml --env-file deploy/.env.prod` (the `--env-file` is required on
*every* subcommand — see Troubleshooting). `PW=$(grep -E '^NEO4J_PASSWORD='
deploy/.env.prod | cut -d= -f2-)` reads the DB password from your env file.

### Monitor the enrichment crawls / ETL

The **topology is already 100% loaded** (622,813 nodes / 2,042,539 edges from the
dump). The host crawls (`16_gnomad_af`, `06_uniprot_enrich`) don't add nodes/edges —
they **set properties** on existing nodes. "Progress" = coverage climbing:
```bash
tail -f ~/gnomad.log ~/uniprot.log        # live crawl output
pgrep -af 'etl/16_gnomad_af|etl/06_uniprot'   # still running?

# coverage climbing in the graph (re-run periodically — the counts go up):
docker exec omicgraph-neo4j cypher-shell -u neo4j -p "$PW" \
  "MATCH (v:Variant) RETURN count(v.gnomad_af) AS variants_with_af;
   MATCH (p:Protein) RETURN count(p.summary_text) AS proteins_with_summary,
                            count(p.embedding)    AS proteins_with_embedding;"
```
If a crawl dies (e.g. box reboot), just re-launch it — both are `IS NULL`-guarded and
resume where they left off (re-export `NEO4J_URI`/`NEO4J_PASSWORD` first, per Phase 7).

### Update the deployment: **code** changes (the common case — test local → push)

Your laptop is where you develop; the cloud is the source of truth for **data**. For a
code/config change, ship the code and leave the graph alone:
```bash
# on your laptop: commit + push your tested branch
git push origin feat/roadmap-enrichment-and-chatbot

# on the VM: pull + rebuild. The named neo4j volume is NOT touched by a rebuild, so
# your graph + all enrichment survive. deploy/.env.prod is gitignored -> survives pull.
cd ~/Project_OMNI && git pull
DC up -d --build
```
A rebuild only replaces the backend/web images. The graph lives in the `neo4j_data`
named volume and persists across `up`/`down`/`--build`. Only `down -v` or an explicit
`docker volume rm` deletes it.

### Enable the literature review dashboard (Feature 2, ADR-0014)

The extractor and its review dashboard ship **OFF**. The `/admin` route is already
reverse-proxied by [`deploy/Caddyfile`](../../deploy/Caddyfile) and the dashboard is in the
web build, so enabling it is env-only + a rebuild:

```bash
# on the VM, in deploy/.env.prod (gitignored — survives pulls):
EXTRACTION_AGENT_ENABLED=true            # enables the extractor + dashboard write actions
ADMIN_TOKEN=<a long random secret>       # REQUIRED on a public host; gates every /admin write

cd ~/Project_OMNI && DC up -d --build    # rebuild picks up the flags
```

Then open **`https://<your-site>/#/admin`**, paste the `ADMIN_TOKEN` when prompted, and
review candidates (usage: [design doc §Using the dashboard](../design/feature-2-literature-extraction.md#using-the-dashboard-operator-guide)).
The token is the app-layer gate; for a second layer, wrap the `handle /admin/*` block in
the Caddyfile with [`basic_auth`](https://caddyserver.com/docs/caddyfile/directives/basic_auth).
**Never leave `ADMIN_TOKEN` empty with `EXTRACTION_AGENT_ENABLED=true` on a public box** —
the backend logs a startup warning if you do. To try it with throwaway data, run
`scripts/seed_demo_candidates.py` (see the design doc); `--clear` removes it.

### Enable the literature backfill + nightly cursor (Feature 2 P3)

Same master gate (`EXTRACTION_AGENT_ENABLED=true`). With it on, the backend already runs
the **nightly forward catch-up** cron (walks a persisted date cursor forward — no manual
step). The relation model defaults to a **free** OpenRouter slug, so the LLM side of an
always-on run costs $0; only NCBI E-utils is metered (free, rate-limited). This box is
production — enable it here, not locally.

```bash
# one-time: kick off the always-on historical backfill (2005 -> now, walks backward)
curl -X POST -H "X-Admin-Token: $ADMIN_TOKEN" https://<your-site>/admin/agents/extraction/backfill/start

# watch both cursors (dates, status, cumulative candidates)
curl -H "X-Admin-Token: $ADMIN_TOKEN" https://<your-site>/admin/agents/extraction/backfill/status

# pause / resume any time (resumes from the exact cursor date)
curl -X POST -H "X-Admin-Token: $ADMIN_TOKEN" https://<your-site>/admin/agents/extraction/backfill/pause
curl -X POST -H "X-Admin-Token: $ADMIN_TOKEN" https://<your-site>/admin/agents/extraction/backfill/resume
```

It is **interruption-safe by design**: progress is a date on an `:ExtractionCursor` node,
advanced only after each chunk finishes, so a `git pull && DC up -d --build` redeploy
mid-run resumes automatically (startup relaunches any `RUNNING` cursor). Sustained NCBI/LLM
throttling is handled gracefully — the loop backs off and retries the same window rather
than skipping papers. Tunables (`EXTRACTION_BACKFILL_FLOOR_DATE`, `_CHUNK_DAYS`,
`_LLM_CONCURRENCY`, `_MAX_PMIDS_PER_CHUNK`, `EXTRACTION_BACKFILL_CRON_HOUR`) live in
`deploy/.env.prod`; see `.env.example`. Candidates land in the same `#/admin` review queue.

### Update the deployment: **graph** changes (rarer — and it CLOBBERS)

⚠️ The cloud graph is ahead of your laptop's — the crawls run **on the cloud**, so it
accumulates enrichment your local copy doesn't have. Pushing a local dump up
**overwrites** that. Only do this if you rebuilt the *topology* locally (new data
source / schema) and accept re-running the Phase 7 crawls afterward.
```bash
# laptop: rebuild/enrich locally, then dump + upload
bash scripts/dump_graph.sh
scp dumps/neo4j.dump ubuntu@<VM_IP>:~/Project_OMNI/dumps/
# VM: load it (stops neo4j ~30s, overwrites, restarts)
cd ~/Project_OMNI && bash scripts/restore_graph.sh
```
**Safer direction — pull the cloud graph *down* to your laptop** (to develop against
the latest enriched data, or as an extra backup):
```bash
ssh ubuntu@<VM_IP> 'cd ~/Project_OMNI && bash scripts/dump_graph.sh'
scp ubuntu@<VM_IP>:~/Project_OMNI/dumps/neo4j.dump ./dumps/
bash scripts/restore_graph.sh            # loads it into your LOCAL neo4j
```

### Reinitialize

**Same VM, reload the graph from scratch** (keep the box, wipe just the DB):
```bash
cd ~/Project_OMNI
DC down                         # stop containers (volumes kept)
docker volume rm project_omni_neo4j_data
DC up -d neo4j && bash scripts/restore_graph.sh
DC up -d --build
```
**Same VM, nuke everything** (containers + all volumes incl. Caddy certs): `DC down -v`,
then redo Phase 6 (secrets already set) → restore → up.

**Brand-new VM** (box was reclaimed / you want a clean host): start over at **Phase 1**.
Every step is idempotent; the only inputs you need are your SSH key, the `neo4j.dump`,
and your `deploy/.env.prod` secrets.

### Cron (set in Phase 7 — manage them here)

The **keep-alive** (every 30 min, avoids 7-day idle reclamation) and **weekly backup**
crons are added in Phase 7. To review or edit:
```bash
crontab -l          # list active cron jobs
crontab -e          # edit (add/remove lines)
```
If the box is ever reclaimed despite the keep-alive, upgrade to Pay-As-You-Go (stays
$0 within Always-Free limits and is exempt from idle reclamation).

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
| MCP client (or the `#/api` connect config) gets an HTML page instead of an SSE handshake at `/mcp` | An older `deploy/Caddyfile` without the `@mcp` named matcher lets `/mcp/*` fall through to the SPA handler, which returns `index.html`. Confirm the Caddyfile has the `handle @mcp { reverse_proxy backend:8000 { flush_interval -1 } }` block (same buffering fix as chat) and redeploy. |
| Instance got deleted | It was idle >7 days — recreate and add the Phase 7 keep-alive cron (or upgrade to PAYG). |
