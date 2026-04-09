# 🚀 Voicebot SaaS Platform — Taxi Booking AI Voice Agents

A production-ready SaaS platform for AI-powered taxi booking voice agents using **Twilio + Ultravox AI**.

## Architecture

```
Internet → Nginx (80/443)
              ├── /api/*         → agent-management (port 5005) — FastAPI/Python
              ├── /cromwell/*    → agent-management (port 5005)
              ├── /static/*      → agent-management (port 5005)
              ├── /twilio/*      → twilio-dispatcher (port 3000) — Express/Node.js
              └── /make/*        → twilio-dispatcher (port 3000)
```

---

## 🐳 Deploy on Any Server (Single Command)

### 1. Install Docker & Docker Compose
```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER && newgrp docker
```

### 2. Clone / Copy the project
```bash
# Transfer files to new server however you prefer (scp, rsync, git, etc.)
scp -r ./voicebot user@new-server:~/voicebot
cd ~/voicebot
```

### 3. Set up environment variables
```bash
# Fill in your real API keys
cp .env.example agent-management-service/.env
cp .env.example cromwell-cars-ai-dispatcher/.env

# Edit both files with your actual credentials
nano agent-management-service/.env
nano cromwell-cars-ai-dispatcher/.env
```

### 4. Set up SSL (Let's Encrypt)
```bash
sudo apt install certbot
sudo certbot certonly --standalone -d your-domain.com
```

### 5. Update Nginx config with your domain
```bash
# Replace all occurrences of agent.cabex.co.uk with your domain
sed -i 's/agent.cabex.co.uk/your-domain.com/g' nginx-agent.cabex.co.uk.conf
```

### 6. Start everything 🚀
```bash
docker compose up -d --build
```

That's it! Your platform is running.

---

## 📋 Useful Commands

```bash
# Start all services
docker compose up -d --build

# Stop all services
docker compose down

# View logs
docker compose logs -f                          # all services
docker compose logs -f agent-management         # Python service only
docker compose logs -f twilio-dispatcher        # Node.js service only

# Restart a single service (after code change)
docker compose restart agent-management

# Rebuild and restart a single service
docker compose up -d --build agent-management

# Check service health
docker compose ps

# Access a running container shell
docker compose exec agent-management bash
```

---

## 📁 Project Structure

```
voicebot/
├── agent-management-service/      # Core backend (FastAPI/Python)
│   ├── main.py                    # All routes, DB models, billing logic
│   ├── rag_service.py             # Knowledge base (Chroma vector store)
│   ├── config/agent_config.py     # Per-company AI prompts
│   ├── routes/
│   │   ├── cromwell_routes.py     # Cromwell Cars booking API
│   │   └── ultravox_routes.py     # Ultravox web-call routes
│   ├── static/                    # Dashboard + customer pages
│   └── Dockerfile
│
├── cromwell-cars-ai-dispatcher/   # Twilio webhook handler (Node.js/Express)
│   ├── index.js                   # Express entry point
│   ├── routes/
│   │   ├── twilio.js              # Inbound call handling
│   │   └── cromwell.js            # Cromwell booking via voice
│   └── Dockerfile
│
├── docker-compose.yml             # ← Run everything with one command
├── nginx-agent.cabex.co.uk.conf   # Nginx reverse proxy config
├── .env.example                   # Template for environment variables
└── logs/                          # Runtime logs (mounted into containers)
```

---

## 🔧 Environment Variables

See `.env.example` for all required variables. The main ones are:

| Variable | Description |
|---|---|
| `TWILIO_ACCOUNT_SID` | Your Twilio Account SID |
| `TWILIO_AUTH_TOKEN` | Your Twilio Auth Token |
| `TWILIO_PHONE_NUMBER` | Your Twilio inbound number |
| `ULTRAVOX_API_KEY` | Ultravox AI API key |
| `TWILIO_WEBHOOK_BASE_URL` | Your public domain (e.g. `https://your-domain.com`) |
| `CABEE_JWT_TOKEN` | Cromwell Cars/Cabee JWT token for booking API |

---

## 💾 Data Persistence

Data is stored in Docker named volumes so it **survives container restarts and updates**:

| Volume | Contents |
|---|---|
| `voicebot_agent_db` | SQLite database (agents, companies, call history, billing) |
| `voicebot_chroma_data` | ChromaDB vector store (knowledge bases) |

To back up your data:
```bash
# Backup database
docker run --rm -v voicebot_agent_db:/data -v $(pwd):/backup alpine \
  cp /data/agents.db /backup/agents_backup.db

# Restore database
docker run --rm -v voicebot_agent_db:/data -v $(pwd):/backup alpine \
  cp /backup/agents_backup.db /data/agents.db
```
