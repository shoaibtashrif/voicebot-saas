#!/bin/bash

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║           🚀 Starting Full SaaS Platform                       ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""

# Create logs directory if it doesn't exist
mkdir -p /home/ubuntu/voicebot/logs

# Function to check if port is in use
check_port() {
    lsof -i:$1 > /dev/null 2>&1
    return $?
}

# Function to wait for service
wait_for_service() {
    local url=$1
    local name=$2
    local max_attempts=30
    local attempt=0
    
    echo -ne "${YELLOW}⏳ Waiting for $name to start...${NC}"
    
    while [ $attempt -lt $max_attempts ]; do
        if curl -s -f "$url" > /dev/null 2>&1; then
            echo -e "\r${GREEN}✅ $name is ready!${NC}                    "
            return 0
        fi
        sleep 1
        ((attempt++))
        echo -ne "\r${YELLOW}⏳ Waiting for $name... ($attempt/$max_attempts)${NC}"
    done
    
    echo -e "\r${RED}❌ $name failed to start${NC}                    "
    return 1
}

# Function to wait for port to be free
wait_for_port_free() {
    local port=$1
    local max_attempts=10
    local attempt=0
    
    while lsof -ti:$port > /dev/null 2>&1 && [ $attempt -lt $max_attempts ]; do
        lsof -ti:$port | xargs kill -9 > /dev/null 2>&1
        sleep 1
        ((attempt++))
    done
}

# Stop existing services
echo -e "${BLUE}🛑 Stopping existing services...${NC}"
pkill -9 -f "python.*main.py" > /dev/null 2>&1
pkill -9 -f "node.*index.js" > /dev/null 2>&1
pkill -9 -f "python3.*run.py" > /dev/null 2>&1
pkill -9 -f "python.*run.py" > /dev/null 2>&1
pkill -9 -f "uvicorn" > /dev/null 2>&1
sleep 2

# Ensure ports are free
wait_for_port_free 5005
wait_for_port_free 3000

echo -e "${GREEN}✅ Old services stopped${NC}"
echo ""

# Start Agent Management Service
echo -e "${BLUE}1. Starting Agent Management Service (Port 5005)...${NC}"
cd /home/ubuntu/voicebot/agent-management-service
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}⚠️  Creating virtual environment...${NC}"
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt > /dev/null 2>&1
else
    source venv/bin/activate
fi

nohup python main.py > /home/ubuntu/voicebot/logs/agent-management.log 2>&1 &
AGENT_MGMT_PID=$!
echo $AGENT_MGMT_PID > /tmp/agent-mgmt.pid
sleep 3

if wait_for_service "http://localhost:5005/health" "Agent Management Service"; then
    echo -e "${GREEN}   PID: $AGENT_MGMT_PID${NC}"
else
    echo -e "${RED}   Check logs: tail -f /home/ubuntu/voicebot/logs/agent-management.log${NC}"
    exit 1
fi
echo ""

# Start Twilio Dispatcher
echo -e "${BLUE}2. Starting Twilio Dispatcher (Port 3000)...${NC}"
cd /home/ubuntu/voicebot/cromwell-cars-ai-dispatcher

if [ ! -d "node_modules" ]; then
    echo -e "${YELLOW}⚠️  Installing npm dependencies...${NC}"
    npm install > /dev/null 2>&1
fi

nohup node index.js > /home/ubuntu/voicebot/logs/twilio-dispatcher.log 2>&1 &
TWILIO_PID=$!
echo $TWILIO_PID > /tmp/twilio.pid
sleep 3

if wait_for_service "http://localhost:3000/twilio/active-calls" "Twilio Dispatcher"; then
    echo -e "${GREEN}   PID: $TWILIO_PID${NC}"
else
    echo -e "${RED}   Check logs: tail -f /home/ubuntu/voicebot/logs/twilio-dispatcher.log${NC}"
    exit 1
fi
echo ""


# Test all services
echo -e "${BLUE}🧪 Running Health Checks...${NC}"
echo ""

# Test Agent Management
if curl -s http://localhost:5005/health | grep -q "healthy"; then
    echo -e "${GREEN}✅ Agent Management Service - HEALTHY${NC}"
else
    echo -e "${RED}❌ Agent Management Service - FAILED${NC}"
fi

# Test Twilio Dispatcher
if curl -s http://localhost:3000/twilio/active-calls > /dev/null 2>&1; then
    echo -e "${GREEN}✅ Twilio Dispatcher - HEALTHY${NC}"
else
    echo -e "${RED}❌ Twilio Dispatcher - FAILED${NC}"
fi

# Test Web Interface / Agent Pages (now on 5005)
if curl -s http://localhost:5005/api/config > /dev/null 2>&1; then
    echo -e "${GREEN}✅ Web Interface (via Agent Management) - HEALTHY${NC}"
else
    echo -e "${RED}❌ Web Interface (via Agent Management) - FAILED${NC}"
fi



echo "╔════════════════════════════════════════════════════════════════╗"
echo "║           🎉 ALL SERVICES STARTED SUCCESSFULLY!                ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""
echo -e "${GREEN}📊 Service Status:${NC}"
echo -e "   ✅ Agent Management:  http://localhost:5005     (PID: $AGENT_MGMT_PID)"
echo -e "   ✅ Twilio Dispatcher:  http://localhost:3000     (PID: $TWILIO_PID)"
echo -e "   ℹ️  Web Interface:      now served on port 5005 (merged)"

echo ""
echo -e "${BLUE}🌐 Access Your Platform:${NC}"
echo -e "   📱 Dashboard:          ${GREEN}https://agent.cabex.co.uk/dashboard${NC}"

echo ""
echo -e "${BLUE}📝 View Logs:${NC}"
echo -e "   Agent Management:  tail -f /home/ubuntu/voicebot/logs/agent-management.log"
echo -e "   Twilio Dispatcher: tail -f /home/ubuntu/voicebot/logs/twilio-dispatcher.log"
echo ""
echo -e "${BLUE}🛑 Stop Services:${NC}"
echo -e "   Run: ${YELLOW}pkill -f 'python.*main.py|node.*index.js|python3.*run.py'${NC}"
echo ""
echo -e "${GREEN}Your platform is ready! Create your first agent at the dashboard! 🚀${NC}"
echo ""

# When run by systemd, keep running to prevent restart loops
if [ ! -z "$INVOCATION_ID" ]; then
    echo ""
    echo "📍 Running under systemd - keeping process alive..."
    echo "   (Services will stay active in background)"
    
    # Keep the script running indefinitely
    while true; do
        sleep 3600  # Sleep 1 hour at a time
    done
fi

