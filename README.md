<h1 align="center">SupplyPulse</h1>

<p align="center"><b>Enterprise-grade supply-chain early warning for small manufacturers, delivered as a conversation in ASI:One.</b></p>

<p align="center">
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white">
  <img alt="Fetch.ai uAgents" src="https://img.shields.io/badge/Fetch.ai-uAgents-0b1220">
  <img alt="ASI:One agent" src="https://img.shields.io/badge/ASI%3AOne-chat%20agent-6366f1">
  <img alt="Docker ready" src="https://img.shields.io/badge/docker-ready-2496ED?logo=docker&logoColor=white">
</p>

<p align="center">
  <a href="#-the-problem">Problem</a> ·
  <a href="#-see-it-work">Demo</a> ·
  <a href="#-quick-start">Quick start</a> ·
  <a href="#-commands">Commands</a> ·
  <a href="#-architecture">Architecture</a> ·
  <a href="#-risk-model">Risk model</a> ·
  <a href="#-limitations">Limitations</a>
</p>

---

## 🚨 The problem

A garment exporter in Tiruppur depends on five to twenty suppliers across India, China, Vietnam or Bangladesh.
When a cyclone closes a port, a supplier's region floods, or a supplier's parent company runs into trouble, large
enterprises know within hours, because they pay for risk-monitoring platforms. A small manufacturer finds out
**weeks later**, when a shipment misses its date and an export order is penalised or lost.

## 💡 The solution

SupplyPulse watches the weather, natural disasters, local news and financial health around every supplier and port a
manufacturer depends on, and explains in plain language which risks matter *for their business* and what to do next.

> **The core idea: rank risk by exposure, not just by events.**
> A typhoon near a single-source supplier matters greatly when you hold ten days of stock. It matters far less when
> you have a backup supplier and sixty days of stock. SupplyPulse computes this from the user's own data, which a
> generic news feed cannot do.

| 🗣️ Conversational | 🎯 Exposure-based | 🛡️ Resilient | 🤝 Actionable |
|---|---|---|---|
| Describe your suppliers in a sentence. No forms, no dashboards to learn. | Risk = likelihood × impact, from your stock, lead times and backups. | Slow or failed sources degrade gracefully, and gaps are never shown as "all clear". | Finds alternative suppliers, drafts the email, and alerts you when a risk level changes. |

---

## 🎬 See it work

A real run on a recorded event (Cyclone Michaung, Chennai, December 2023), lightly condensed. The same cyclone turns
**two** suppliers red, because they are single-source with little stock, while the well-stocked ones stay green.

```text
You:  replay chennai-cyclone
You:  scan

SupplyPulse risk check — overall risk: 🔴 HIGH
2 suppliers need action now.

🔴 act now · 🟠 keep an eye on it · 🟢 fine for now · ⚪ not enough data

2. 🔴 Zippers from Shenzhen
    Orange-level alert: tropical cyclone MICHAUNG-23 is 137 km from Chennai port. It's your only
    supplier for this item, and your 10 days of stock would run out before a replacement could
    arrive (about 30 days).

3. 🔴 Buttons from Ningbo
    Orange-level alert: tropical cyclone MICHAUNG-23 is 137 km from Chennai port. It's your only
    supplier for this item, and your 8 days of stock would run out before a replacement could
    arrive (about 25 days).

1. 🟢 Fabric from Erode
    No significant risk found. Your 30 days of stock covers the roughly 7 days a replacement would take.

4. 🟢 Dyes from Ankleshwar
    No significant risk found. Your 30 days of stock covers the roughly 7 days a replacement would take.

How complete is this check?
✅ Weather, cyclone, flood and earthquake alerts: checked for all 4 suppliers.

What next? `why 2` shows the evidence · `alternatives 2` looks for other suppliers ·
`email 2` drafts a message to your supplier · `scan` checks again · `plans` shows pricing
```

Every row ends with a plain-language reason, and the report always says what could and could not be checked.

---

## 🚀 Quick start

**Prerequisites:** Python 3.10 or newer, an [Agentverse](https://agentverse.ai) account, and an
[ASI:One API key](https://asi1.ai/dashboard/api-keys). Docker is optional.

### 1. Get the code and install

```powershell
git clone https://github.com/Vedanshishah123/SupplyPulse.git
cd SupplyPulse
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure

```powershell
copy .env.example .env
```

Open `.env` and set at least `ASI1_API_KEY` and a long random `AGENT_SEED_PREFIX`. See [Configuration](#-configuration).

### 3. Connect to ASI:One (one time)

```powershell
python -m agents.orchestrator
```

Open the **Inspector link** it prints, sign in to Agentverse, click **Connect**, choose **Mailbox**, then stop the
process with `Ctrl+C`. The mailbox is tied to the agent's address, so you never repeat this unless you change
`AGENT_SEED_PREFIX`.

### 4. Run

<table>
<tr>
<th width="50%">🐍 Locally</th>
<th width="50%">🐳 With Docker</th>
</tr>
<tr>
<td>

```powershell
python -m agents.run_all
```

</td>
<td>

```bash
docker compose up --build -d
docker compose logs -f
docker compose down
```

</td>
</tr>
</table>

Prefer plain Docker?

```bash
docker build -t supplypulse .
docker run -d --name supplypulse --env-file .env \
  -v supplypulse-data:/data --restart unless-stopped supplypulse
```

The container reads your `.env` at start (it is excluded from the image) and keeps sessions and plans in the
`supplypulse-data` volume, so they survive restarts. Use the **same `AGENT_SEED_PREFIX`** as in step 3, so the
connected mailbox still belongs to the running agent.

### 5. Chat

Open the `supplypulse` agent in Agentverse, choose **Chat with Agent**, and describe your supply chain:

> *I make cotton garments in Tiruppur. Fabric comes from a mill in Erode, zippers from Shenzhen, buttons from
> Ningbo, dyes from Ankleshwar. I ship to Europe through Chennai port.*

---

## 💬 Commands

Commands are plain messages typed in the chat. `N` is a supplier number shown in the risk check.

**Set up your supply chain**

| Command | Example | What it does |
|---|---|---|
| A sentence about your suppliers | `Zippers from Shenzhen, I ship through Chennai port` | Starts onboarding, or replaces a map you have not confirmed |
| `ITEM: key=value ...` | `zippers: stock=10 critical=high backup=yes lead=30` | Corrects a supplier before you confirm |
| `confirm` | `confirm` | Locks in the map and runs the first risk check |
| `restart` | `restart` | Starts onboarding again |

Correction keys: `stock` (days of stock), `critical` (low, medium, high), `backup` (yes or no), `lead` (days to
replace the supplier) and `company` (a listed company name, which adds a financial-health check), for example
`zippers: company=YKK Corp stock=12`.

**Check and understand risk**

| Command | Example | What it does |
|---|---|---|
| `scan` | `scan` | Runs a fresh risk check |
| `results` | `results` | Shows the latest risk check or draft again |
| `why N` | `why 2` | Shows the evidence and score breakdown for supplier N |

**Act on a risk**

| Command | Example | What it does |
|---|---|---|
| `alternatives N` | `alternatives 1` | Lists candidate alternative suppliers for supplier N |
| `email N` | `email 1` | Drafts a delay inquiry to your current supplier N |
| `inquire M` | `inquire 2` | Drafts an inquiry to alternative M from the last `alternatives` list |

**Plans and account**

| Command | Example | What it does |
|---|---|---|
| `plans` | `plans` | Shows the Free, Pro and Deep report plans |
| `upgrade` | `upgrade` | Pays for Pro in chat |
| `buy deep N` | `buy deep 1` | Pays for a deep report on supplier N |
| `delete my data` | `delete my data` | Erases your suppliers and session |
| `help` | `help` | Lists the commands |

**Monitoring and demonstration**

| Command | Example | What it does |
|---|---|---|
| `alerts` | `alerts` | Shows monitoring alerts |
| `dismiss` | `dismiss` | Clears the alerts |
| `replay list` | `replay list` | Lists the recorded scenarios |
| `replay NAME` | `replay chennai-cyclone` | Runs checks on a recorded real event, always labelled as a replay |
| `replay alert` | `replay alert` | Shows what a monitoring alert looks like for the active replay |
| `replay off` | `replay off` | Returns to live data |

---

## 🧩 Architecture

![SupplyPulse architecture: the orchestrator fans each scan out to the hazard, events and financial agents, the scorer returns a risk report, the mitigation agent finds alternatives, and the monitor agent triggers scheduled re-scans](docs/architecture.svg)

Seven uAgents run in one process. Only the **orchestrator** is registered on Agentverse and reachable from ASI:One;
the six internal agents exchange typed messages in memory.

| Step | What happens |
|---|---|
| 1. Scan | Each check gets a unique ID. The orchestrator asks the hazard, events and financial agents about every supplier **in parallel**. |
| 2. Collect | The scorer gathers results until all arrive or **20 seconds** pass, so a slow source never blocks the answer. Repeated messages with the same ID are ignored. |
| 3. Explain | The scorer turns evidence into plain-language reasons. **The LLM never produces a score.** |
| 4. Pay | The **Agent Payment Protocol** requests payment, the user's wallet commits it, and the transfer is verified on the Fetch.ai ledger before access is granted. |

The **Chat Protocol** carries the conversation. The ASI:One API is used only to understand the user's description,
draft emails and suggest where to look.

---

## 🧮 Risk model

Each supplier's risk is deterministic, so every score traces back to its evidence:

```text
Risk = Likelihood × Impact

Likelihood = weighted mean of the signals that are available
    hazard         0.45   weather now and forecast, cyclone / flood / earthquake alerts near supplier and port
    events         0.25   strikes, closures and fires in local news
    financial      0.10   news sentiment and fundamentals of a listed supplier
    concentration  0.20   single-source items, suppliers sharing a city

Impact = importance × (backup? 0.6 : 1.0) × exposure
    exposure = replacement time ÷ days of stock    (high when stock runs out before a replacement arrives)
```

Scores map to 🔴 high (≥ 0.50), 🟠 medium (≥ 0.20) and 🟢 low.

> **A missing signal is never read as low risk.** Unavailable signals are excluded, named in the report, and a
> supplier with no external data shows ⚪ unknown.

## 🛡️ Reliability

| Mechanism | What it does |
|---|---|
| Adapter per data source | A timeout, then the last cached result marked with its age |
| Circuit breaker | After 3 failures a source is skipped for 5 minutes instead of waited on |
| Honest partial results | Every report states what could and could not be checked, in plain words |
| Background refresh | Slow sources (GDACS) refresh in the background; rate-limited ones (GDELT) are throttled and cached |
| Idempotent scans | A repeated message with the same scan ID is ignored |
| Payment safety | Access is granted only after on-chain verification; a deep-report credit survives a failed delivery |
| Scenario replay | A recorded real event runs through the same pipeline, so the system can be demonstrated on a calm day |

<details>
<summary><b>📡 Data sources</b></summary>

| Signal | Source | Key |
|---|---|---|
| Place to coordinates | Built-in city list, then Open-Meteo geocoding | none |
| Weather now and 5-day forecast | Open-Meteo | none |
| Cyclones, floods | GDACS | none |
| Earthquakes | USGS | none |
| Local news | GDELT | none |
| Ticker, fundamentals, sentiment | Alpha Vantage | `ALPHAVANTAGE_API_KEY` (optional) |
| Alternative suppliers | Geoapify Places | `GEOAPIFY_API_KEY` |
| Understanding, emails, suggestions | ASI:One API | `ASI1_API_KEY` |

</details>

---

## 💳 Plans

| Plan | Includes | Price (testnet) |
|---|---|---|
| **Free** | One risk check, up to 5 suppliers, one alternatives/email preview | free |
| **Pro** | Unlimited checks and suppliers, scheduled monitoring and alerts, alternatives and emails, 30 days | 0.5 FET |
| **Deep report** | Alternatives and drafted emails for one supplier | 0.2 FET |

Prices are configurable (`PRO_PRICE_FET`, `DEEP_PRICE_FET`). Beyond direct sign-up, industry associations and export
councils could offer SupplyPulse to all members, and freight forwarders could white-label it for their clients.

## 🔧 Configuration

| Variable | Purpose |
|---|---|
| `ASI1_API_KEY` | Understanding free-text descriptions, emails, suggestions (falls back to a simple parser and templates) |
| `GEOAPIFY_API_KEY` | Alternative-supplier search |
| `ALPHAVANTAGE_API_KEY` | Optional financial check |
| `AGENT_SEED_PREFIX` | Fixes the agents' addresses. Use a long random value and never change it |
| `USE_MAILBOX` | `1` for ASI:One (default), `0` for local-only testing |
| `MONITOR_INTERVAL_S` | Monitoring interval in seconds (86400 = daily) |
| `ALLOW_UNVERIFIED_PAYMENTS` | `1` skips only the on-chain check, for testing. Keep `0` |

## 🧰 Utilities

```powershell
python -m scripts.payment_roundtrip      # simulates a buyer wallet through the full payment flow
python -m scripts.check_balance <wallet> # checks a testnet FET balance
```

<details>
<summary><b>🗂️ Project layout</b></summary>

```text
agents/       orchestrator, hazard, events, financial, scorer, mitigation, monitor, billing, dialog, run_all
adapters/     resilient data access: timeout, circuit breaker, cache, and the public-API sources
scoring/      deterministic risk model, hazard / event / financial scoring, plain-language explanations
mitigation/   alternative-supplier search, sourcing guidance, email drafting
onboarding/   free text to structured suppliers
render/       the text cards shown in chat
replay/       recorded scenarios
models/       shared message models
store.py      minimal JSON persistence
scripts/      payment simulation and wallet balance check
docs/         architecture diagram
```

</details>

---

## 🚧 Limitations

- ASI:One's chat protocol has no buttons, so interactive "cards" are formatted text with commands such as `why 2`.
- Replies the agent sends on its own after the first acknowledgement may not appear in ASI:One; `results` shows the
  latest output on demand, and `alerts` lists monitoring alerts.
- Most small suppliers are not listed companies, so the financial signal is often absent. Hazard and event
  signals carry most of the weight, and the report says so.
- Map data is sparse for small suppliers and poor inside China. Alternatives are businesses whose *name* matches
  the component and are always presented as unverified, with sourcing leads to check by hand.
- Local news depends on GDELT, which rate-limits heavily. When it is unavailable the report says so rather than
  implying there is no news.
- Open-Meteo's free tier is non-commercial; a commercial deployment needs its paid plan.
- Payments use the Fetch.ai **testnet**. The request format follows Fetch.ai's reference seller and the flow has been
  exercised with a simulated buyer; a payment from a funded ASI:One wallet is still to be verified end to end.
- Stored supplier data is plain JSON, kept to the minimum and removable with `delete my data`; encryption at rest
  is future work.
- The Docker setup has not been built or run on the machine this was developed on.
