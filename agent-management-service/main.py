from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Depends, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, Text, Boolean, DateTime, Float, ForeignKey, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import uvicorn
import os
import re
import requests
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
import json
from rag_service import rag_service
from twilio.rest import Client
import httpx
from dateutil import parser
import pytz
import dateparser

# Load environment variables
load_dotenv()

# Ultravox API configuration
ULTRAVOX_API_KEY = os.getenv("ULTRAVOX_API_KEY")
ULTRAVOX_BASE_URL = "https://api.ultravox.ai/api"

# Initialize Twilio client
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WEBHOOK_BASE_URL = os.getenv("TWILIO_WEBHOOK_BASE_URL", "http://localhost:3000")

twilio_client = None
if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
    try:
        twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    except Exception as e:
        print(f"Warning: Could not initialize Twilio client: {e}")

# Database setup
SQLALCHEMY_DATABASE_URL = "sqlite:///./agents.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class WebAgent(Base):
    __tablename__ = "web_agents"
    __table_args__ = {'sqlite_autoincrement': True}  # Ensures IDs are never reused
    
    id = Column(Integer, primary_key=True, index=True)
    agent_name = Column(String(100), nullable=False)
    company_name = Column(String(200), nullable=True)
    company_id = Column(Integer, nullable=True)
    company_slug = Column(String(200), unique=True, nullable=False)
    greeting_message = Column(Text, nullable=True)
    custom_prompt = Column(Text, nullable=True)  # Full custom system prompt - if set, replaces default entirely
    knowledge_base = Column(Text, nullable=True)  # Store raw content for reference
    knowledge_type = Column(String(20), default="text")  # text, file, url
    knowledge_source = Column(String(500), nullable=True)  # URL or filename
    is_active = Column(Boolean, default=True)
    twilio_phone_number = Column(String(20), nullable=True)  # Assigned Twilio number
    api_Token = Column(String(500), nullable=True)  # JWT Token for API authorization
    ultravox_agent_id = Column(String(100), nullable=True)  # Ultravox agent ID
    psid = Column(String(100), nullable=True)  # Project/Service ID
    voice = Column(String(100), nullable=True)  # Ultravox voice ID
    web_call_pin = Column(Integer, nullable=True)  # 4-digit PIN for web call access
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class CallRecord(Base):
    __tablename__ = "call_records"
    
    id = Column(Integer, primary_key=True, index=True)
    call_sid = Column(String(100), unique=True, index=True, nullable=False)
    agent_id = Column(Integer, index=True, nullable=False)
    caller_number = Column(String(20), nullable=True)
    status = Column(String(50), default="initiated")
    recording_url = Column(String(500), nullable=True)
    duration = Column(Integer, nullable=True)
    transcript = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)
    is_billed = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Company(Base):
    __tablename__ = "companies"
    
    id = Column(Integer, primary_key=True, index=True)
    company_name = Column(String(200), nullable=False)
    company_slug = Column(String(200), unique=True, index=True, nullable=False)
    balance_minutes = Column(Float, default=20.0)
    balance_amount = Column(Float, default=10.0)
    call_rate = Column(Float, default=0.5)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class DeletedAgent(Base):
    """Audit log of all deleted agents. Admin-only. IDs are never reused."""
    __tablename__ = "deleted_agents"

    id = Column(Integer, primary_key=True, index=True)
    original_agent_id = Column(Integer, nullable=False, index=True)  # The ID of the deleted agent
    agent_name = Column(String(100), nullable=True)
    company_name = Column(String(200), nullable=True)
    company_id = Column(Integer, nullable=True)
    company_slug = Column(String(200), nullable=True)
    ultravox_agent_id = Column(String(100), nullable=True)
    api_Token = Column(String(500), nullable=True)
    is_active = Column(Boolean, nullable=True)
    created_at = Column(DateTime, nullable=True)    # Original creation date
    deleted_at = Column(DateTime, default=datetime.utcnow)  # When it was deleted

class BalanceHistory(Base):
    __tablename__ = "balance_history"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, index=True, nullable=False)  # FK to companies.id
    company_slug = Column(String(200), nullable=True)
    amount_minutes = Column(Float, nullable=False)         # how many minutes were added
    balance_after = Column(Float, nullable=False)          # running balance after top-up
    note = Column(String(300), nullable=True)               # optional note / reference
    recharged_at = Column(DateTime, default=datetime.utcnow)

class JobRecord(Base):
    __tablename__ = "jobs"
    
    id = Column(Integer, primary_key=True, index=True)
    jobNO = Column(String(50), nullable=True)
    bookingId = Column(String(50), nullable=True)
    passengerName = Column(String(200), nullable=True)
    customerPrice = Column(String(50), nullable=True)
    date = Column(String(100), nullable=True)
    origin = Column(String(500), nullable=True)
    destination = Column(String(500), nullable=True)
    vehicleType = Column(String(50), nullable=True)
    call_sid = Column(String(100), index=True, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class VehicleCapacity(Base):
    __tablename__ = "vehicle_capacities"
    
    id = Column(Integer, primary_key=True, index=True)
    vehicle_name = Column(String(100), unique=True, nullable=False, index=True)
    passenger_capacity = Column(Integer, nullable=False)
    luggage_capacity = Column(Integer, nullable=False)  # total cases/suitcases
    hand_luggage_capacity = Column(Integer, default=0)
    capacity_description = Column(String(300), nullable=True)  # human-readable string

# Create tables
Base.metadata.create_all(bind=engine)
# NOTE: To seed vehicle capacity data run: python3 seed_vehicle_capacities.py

# Pydantic models
class VerifyTimeRequest(BaseModel):
    dateTimeString: str

class ValidateCapacityRequest(BaseModel):
    vehicleName: str
    passengers: int
    luggage: Optional[int] = 0

class AgentCreate(BaseModel):
    agent_name: str
    company_name: Optional[str] = None
    company_id: Optional[int] = None
    greeting_message: Optional[str] = None
    knowledge_base: Optional[str] = None
    knowledge_type: str = "text"
    api_Token: Optional[str] = None
    ultravox_agent_id: Optional[str] = None
    voice: Optional[str] = None
    psid: Optional[str] = None
    web_call_pin: int

class AgentUpdate(BaseModel):
    agent_name: Optional[str] = None
    company_name: Optional[str] = None
    company_id: Optional[int] = None
    greeting_message: Optional[str] = None
    knowledge_base: Optional[str] = None
    knowledge_type: Optional[str] = None
    is_active: Optional[bool] = None
    api_Token: Optional[str] = None
    ultravox_agent_id: Optional[str] = None
    voice: Optional[str] = None
    psid: Optional[str] = None
    web_call_pin: Optional[int] = None

class CallLogRequest(BaseModel):
    call_id: str
    caller_number: Optional[str] = None
    status: str = "initiated"
    duration: Optional[int] = None

class JobCreate(BaseModel):
    call_sid: str
    jobNO: Optional[str] = None
    bookingId: Optional[str] = None
    passengerName: Optional[str] = None
    customerPrice: Optional[str] = None
    date: Optional[str] = None
    origin: Optional[str] = None
    destination: Optional[str] = None
    vehicleType: Optional[str] = None

class AgentResponse(BaseModel):
    id: int
    agent_name: str
    company_name: Optional[str]
    company_id: Optional[int]
    company_slug: str
    greeting_message: Optional[str]
    custom_prompt: Optional[str]
    knowledge_base: Optional[str]
    knowledge_type: str
    knowledge_source: Optional[str]
    is_active: bool
    twilio_phone_number: Optional[str]
    api_Token: Optional[str]
    ultravox_agent_id: Optional[str]
    voice: Optional[str]
    psid: Optional[str]
    web_call_pin: Optional[int]
    created_at: datetime
    updated_at: datetime

class CallResponse(BaseModel):
    call_id: str
    agent_id: Optional[str] = None
    status: str
    created_at: str
    joined_at: Optional[str] = None
    ended_at: Optional[str] = None
    end_reason: Optional[str] = None
    duration: Optional[int] = None
    billed_duration: Optional[str] = None
    caller_phone_number: Optional[str] = None
    short_summary: Optional[str] = None
    summary: Optional[str] = None
    recording_enabled: Optional[bool] = None
    
class CallRecordingResponse(BaseModel):
    call_id: str
    recording_url: Optional[str] = None
    transcript: Optional[str] = None
    summary: Optional[str] = None
    short_summary: Optional[str] = None
    messages: Optional[List[Dict[str, Any]]] = None

class WebCallResponse(BaseModel):
    callId: str
    joinUrl: str
    agent_id: int
    agent_name: str
    company_slug: str

class CallHistoryResponse(BaseModel):
    id: int
    call_sid: str
    agent_id: int
    caller_number: Optional[str]
    status: str
    recording_url: Optional[str]
    duration: Optional[int]
    created_at: datetime

class JobRecordResponse(BaseModel):
    id: int
    jobNO: Optional[str] = None
    bookingId: Optional[str] = None
    passengerName: Optional[str] = None
    customerPrice: Optional[str] = None
    date: Optional[str] = None
    origin: Optional[str] = None
    destination: Optional[str] = None
    vehicleType: Optional[str] = None
    call_sid: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True

class CallListResponse(BaseModel):
    results: List[CallResponse]
    next: Optional[str] = None
    previous: Optional[str] = None
    total: Optional[int] = None

# FastAPI app
import jwt

app = FastAPI(
    title="Agent Management Service",
    description="Multi-agent web system for taxi booking",
    version="1.0.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Include sub-routers (Cromwell booking tools + Ultravox web-call interface)
from routes.cromwell_routes import router as cromwell_router
from routes.ultravox_routes import router as ultravox_router
app.include_router(cromwell_router, prefix="/cromwell")
app.include_router(ultravox_router, prefix="/api")

# JWT config for web-call PIN authentication
JWT_SECRET = os.getenv("JWT_SECRET", "cabex-web-call-secret-key")
JWT_ALGORITHM = "HS256"
SESSION_LIFETIME_MINUTES = 5

# Database dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Utility functions
def create_slug(company_name: str) -> str:
    """Create a URL-friendly slug from company name"""
    if not company_name:
        return f"agent-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    
    # Convert to lowercase and replace spaces/special chars with hyphens
    slug = re.sub(r'[^a-zA-Z0-9\s-]', '', company_name.lower())
    slug = re.sub(r'\s+', '-', slug.strip())
    return slug

def load_default_prompt() -> str:
    """Load the default system prompt from agent_config.py"""
    try:
        from config.agent_config import SYSTEM_PROMPT
        return SYSTEM_PROMPT
    except Exception as e:
        print(f"Error loading default prompt: {e}")
        return "Default system prompt not available"

import pytz

def merge_prompt_with_customizations(agent: WebAgent) -> str:
    """
    If agent has custom_prompt set, use it entirely and ONLY inject time awareness.
    Otherwise, use default prompt with company-specific substitutions.
    """
    # Get current London time for injection
    london_tz = pytz.timezone('Europe/London')
    now_london = datetime.now(london_tz)
    current_time_str = now_london.strftime("%A, %B %d, %Y at %I:%M %p")
    
    # Time awareness instruction (always inject this)
    london_tz = pytz.timezone('Europe/London')
    current_time_str = datetime.now(london_tz).strftime("%Y-%m-%d %H:%M:%S")
    
    time_instruction = f"""
############################################################
# CRITICAL HARD GATE: DATE & TIME VALIDATION
############################################################

Current Date and Time (London): {current_time_str}

MANDATORY VALIDATION TOOL: verifyTime
You MUST use verifyTime for EVERY booking request before BookCab.

ABSOLUTE RULES (NO EXCEPTIONS):
1) You may ONLY create bookings for a FUTURE date/time.
2) If verifyTime returns status "past", you MUST reject the request.
3) NEVER auto-correct, roll forward, or assume a different date/time.
4) If the user provides an incomplete datetime, you MUST collect the missing part.
5) If the user provides an ambiguous date format, you MUST ask them to clarify.

MANDATORY REJECTION (say verbatim, exactly):
"I'm sorry, that time has already passed. Could you please provide a future date and time?"

MANDATORY CLARIFICATION PROMPTS:
- If DATE is missing:
  "Sure — what date would you like to book for (day/month/year)?"
- If TIME is missing (and date is still valid):
  "Got it — let me check the availability for that date. " -> tool verifyTime call with date 
- If ambiguous date (e.g., 03/04/2026):
  "Just to confirm — is that day/month/year or month/day/year?"

------------------------------------------------------------
RELATIVE TIME NORMALIZATION (REQUIRED)
Because the bot only knows time from {current_time_str}:
- If user says "now" / "right now" / "asap" / "immediately":
  Interpret requested datetime as {current_time_str} + 15 minutes.
  Then call verifyTime with that computed dateTimeString.
- If user says "in N minutes" or "in N hours":
  Interpret relative to {current_time_str}.
  Then call verifyTime with that computed dateTimeString.

------------------------------------------------------------
DATE-ONLY VALIDATION (CRITICAL — STOPS "March 2" BUG)
If the user gives ONLY a date (no time), you MUST STILL use verifyTime:
- Call verifyTime using the same date at "11:59 PM" (end-of-day) for validation only.
- If verifyTime returns status "past", reject immediately with the mandatory rejection line.
- If verifyTime returns status "future", then ask for the exact pickup time (AM/PM), and run verifyTime again for the exact datetime once provided.

This does NOT set the booking time. It only prevents accepting dates that are already fully in the past.

------------------------------------------------------------
VALIDATION PROCEDURE (MUST FOLLOW, SILENTLY):
A) If user provided date-only → run DATE-ONLY VALIDATION first.
B) Once you have a complete datetime → call verifyTime with dateTimeString.
C) If status is "past" → mandatory rejection.
D) If status is "future" → you may proceed to capacity validation.

TOOL CALL GUARDRAIL:
- You MUST NOT call BookCab unless verifyTime has returned status "future" for the exact datetime being booked.
############################################################
""".replace("{current_time_str}", current_time_str)
    
    # Company ID instruction (always inject this if company_id exists)
    company_id_instruction = ""
    if agent.company_id:
        company_id_instruction = f"\n\n### Company Information\n* Company ID: {agent.company_id}\n* IMPORTANT: You MUST include the parameter `companyId: {agent.company_id}` in ALL tool calls (BookCab, checkPricing, etc.). This is mandatory."
    
    # CHECK: Does agent have a custom prompt?
    if agent.custom_prompt and agent.custom_prompt.strip():
        # YES - Use custom prompt entirely, only inject time + company ID
        final_prompt = time_instruction + "\n\n" + agent.custom_prompt + company_id_instruction
        print(f"✅ Using CUSTOM prompt for agent {agent.agent_name}")
        return final_prompt
    
    # NO - Use default prompt with substitutions
    print(f"✅ Using DEFAULT prompt with substitutions for agent {agent.agent_name}")
    default_prompt = load_default_prompt()
    default_prompt = time_instruction + "\n\n" + default_prompt
    
    # Replace agent name
    if agent.agent_name:
        default_prompt = re.sub(r'\*   \*\*Your Name:\*\* Alex', f'*   **Your Name:** {agent.agent_name}', default_prompt)
        default_prompt = re.sub(r'This is Alex\.', f'This is {agent.agent_name}.', default_prompt)
    
    # Replace company name in ALL instances
    if agent.company_name:
        greeting_pattern = r"Thank you for calling Cromwell Cars\. This is"
        replacement = f"Thank you for calling {agent.company_name}. This is"
        default_prompt = re.sub(greeting_pattern, replacement, default_prompt)
        
        role_pattern = r"You are a professional and efficient dispatcher for Cromwell Cars, a London-based taxi service\."
        replacement = f"You are a professional and efficient dispatcher for {agent.company_name}, a taxi service."
        default_prompt = re.sub(role_pattern, replacement, default_prompt)
        
        default_prompt = re.sub(r'Cromwell Cars', agent.company_name, default_prompt)
    
    # Inject Company ID
    default_prompt += company_id_instruction
    
    # Add custom greeting message if provided
    if agent.greeting_message:
        greeting_section = f"**Custom Greeting:** {agent.greeting_message}\n\n"
        call_flow_start = default_prompt.find("**1. Greeting & Triage**")
        if call_flow_start != -1:
            default_prompt = default_prompt[:call_flow_start] + greeting_section + default_prompt[call_flow_start:]
    
    # Add knowledge base instruction if provided
    if agent.knowledge_base:
        knowledge_instruction = f"\n\n### Knowledge Base Access\nYou have access to company-specific information through the 'getCompanyInfo' tool. Use this tool when customers ask about company details, services, policies, or any information not covered in your standard taxi booking procedures."
        default_prompt += knowledge_instruction
    
    return default_prompt
def get_agent_tools(agent: WebAgent) -> List[Dict[str, Any]]:
    """Get tools configuration for the agent including knowledge base tool"""
    # Import the base tools
    try:
        from config.agent_config import get_selected_tools
        base_tools = get_selected_tools()
    except Exception as e:
        print(f"Error loading base tools: {e}")
        base_tools = []
    
    # Add knowledge base tool if agent has knowledge base
    if agent.knowledge_base:
        knowledge_tool = {
            "temporaryTool": {
                "modelToolName": "getCompanyInfo",
                "description": "Get company-specific information and policies",
                "dynamicParameters": [
                    {
                        "name": "query",
                        "location": "PARAMETER_LOCATION_BODY",
                        "schema": {
                            "description": "The question or topic to search for in company information",
                            "type": "string",
                        },
                        "required": True,
                    }
                ],
                "http": {
                    "baseUrlPattern": f"{os.getenv('TOOLS_BASE_URL', 'https://agent.cabex.co.uk')}/api/agents/{agent.id}/knowledge/search",
                    "httpMethod": "POST",
                },
            },
        }
        base_tools.append(knowledge_tool)
    
    # Add Time Verification tool
    time_verification_tool = {
        "temporaryTool": {
            "modelToolName": "verifyTime",
            "description": "Verifies if a given date and time is in the past or future relative to London time. MUST be used for every booking request.",
            "dynamicParameters": [
                {
                    "name": "dateTimeString",
                    "location": "PARAMETER_LOCATION_BODY",
                    "schema": {
                        "description": "The date and time string to verify (e.g., 'tomorrow at 3pm', '2026-02-25 10:00')",
                        "type": "string",
                    },
                    "required": True,
                }
            ],
            "http": {
                "baseUrlPattern": f"{os.getenv('TOOLS_BASE_URL', 'https://agent.cabex.co.uk')}/api/utils/verify-time",
                "httpMethod": "POST",
            },
        },
    }
    base_tools.append(time_verification_tool)

    # Add dynamic Vehicle Types tool (replaces hardcoded validateCapacity)
    get_vehicle_types_tool = {
        "temporaryTool": {
            "modelToolName": "getVehicleTypes",
            "description": "Gets the list of available vehicle types and their passenger/luggage capacities for this company. MUST be called whenever the user asks about available vehicles, provides passenger count, or mentions luggage requirements.",
            "dynamicParameters": [
                {
                    "name": "companySlug",
                    "location": "PARAMETER_LOCATION_BODY",
                    "schema": {
                        "description": "Company Slug",
                        "type": "string",
                    },
                    "required": True,
                },
            ],
            "http": {
                "baseUrlPattern": f"{os.getenv('TOOLS_BASE_URL', 'https://agent.cabex.co.uk')}/cromwell/getVehicleTypes",
                "httpMethod": "POST",
            },
        },
    }
    base_tools.append(get_vehicle_types_tool)

    # Dynamically inject identifiers into tools that require them
    for tool in base_tools:
        if "temporaryTool" in tool:
            temp_tool = tool["temporaryTool"]
            if "dynamicParameters" in temp_tool:
                for param in temp_tool["dynamicParameters"]:
                    if param["name"] == "companyId" and agent.company_id:
                        if "schema" in param:
                            param["schema"]["default"] = agent.company_id
                            param["schema"]["description"] = f"Company ID (Must be {agent.company_id})"
                    elif param["name"] == "companySlug" and agent.company_slug:
                        if "schema" in param:
                            param["schema"]["default"] = agent.company_slug
                            param["schema"]["description"] = f"Company Slug (Must be {agent.company_slug})"

    return base_tools

async def make_ultravox_request(method: str, endpoint: str, data: dict = None) -> dict:
    """Make authenticated request to Ultravox API"""
    if not ULTRAVOX_API_KEY:
        raise HTTPException(status_code=503, detail="Ultravox API key not configured")
    
    headers = {
        "X-API-Key": ULTRAVOX_API_KEY,
        "Content-Type": "application/json"
    }
    
    url = f"{ULTRAVOX_BASE_URL}/{endpoint}"
    print(f"DEBUG: Requesting {method} {url}")
    print(f"DEBUG: Headers: {headers}")
    
    async with httpx.AsyncClient() as client:
        try:
            if method.upper() == "GET":
                response = await client.get(url, headers=headers)
            elif method.upper() == "POST":
                response = await client.post(url, headers=headers, json=data)
            else:
                raise HTTPException(status_code=400, detail=f"Unsupported HTTP method: {method}")
            
            response.raise_for_status()
            return response.json()
            
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=e.response.status_code, detail=f"Ultravox API error: {e.response.text}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error calling Ultravox API: {str(e)}")

# API Routes
@app.get("/api/companies/{company_slug}/balance")
async def get_company_balance(company_slug: str, db: Session = Depends(get_db)):
    """Get the current balance and available call minutes for a specific company."""
    company = db.query(Company).filter(Company.company_slug == company_slug).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    
    return {
        "company_name": company.company_name,
        "company_slug": company.company_slug,
        "balance_minutes": company.balance_minutes,
        "balance_gbp": company.balance_amount,
        "call_rate": company.call_rate,
        "can_make_calls": company.balance_minutes > 0
    }

@app.post("/api/companies/{company_slug}/balance/add")
async def add_company_balance(
    company_slug: str, 
    amount_gbp: float = Form(...), 
    db: Session = Depends(get_db)
):
    """Add funds to a company's wallet. 1 GBP adds 1 minute of call time."""
    if amount_gbp <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")
        
    company = db.query(Company).filter(Company.company_slug == company_slug).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    
    company.balance_amount += amount_gbp
    # Sync minutes (float available)
    if company.call_rate and company.call_rate > 0:
        company.balance_minutes = company.balance_amount / company.call_rate
    else:
        company.balance_minutes = company.balance_amount / 0.5
        
    db.commit()
    db.refresh(company)

    # Record in balance history
    history_entry = BalanceHistory(
        company_id=company.id,
        company_slug=company.company_slug,
        amount_minutes=amount_gbp,
        balance_after=company.balance_minutes,
    )
    db.add(history_entry)
    db.commit()

    return {
        "message": f"Added £{amount_gbp} to balance",
        "new_balance_amount": company.balance_amount,
        "new_balance_minutes": company.balance_minutes
    }

@app.post("/api/companies/{company_slug}/balance/deduct")
async def deduct_company_balance(
    company_slug: str, 
    minutes: float = Form(...), 
    db: Session = Depends(get_db)
):
    """Deduct minutes from a company's wallet."""
    if minutes <= 0:
        raise HTTPException(status_code=400, detail="Minutes must be positive")
        
    company = db.query(Company).filter(Company.company_slug == company_slug).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    
    cost = minutes * (company.call_rate or 0.5)
    company.balance_amount = max(0.0, company.balance_amount - cost)
    
    # Sync minutes
    if company.call_rate and company.call_rate > 0:
        company.balance_minutes = company.balance_amount / company.call_rate
    else:
        company.balance_minutes = company.balance_amount / 0.5
        
    db.commit()
    db.refresh(company)
    
    return {
        "message": f"Deducted {minutes} minutes (£{cost:.2f})",
        "remaining_amount": company.balance_amount,
        "remaining_minutes": company.balance_minutes
    }

@app.get("/api/companies/{company_id}/balance/history")
async def get_balance_history(company_id: int, db: Session = Depends(get_db)):
    """Return full recharge history for a company, plus days since last recharge."""
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    records = (
        db.query(BalanceHistory)
        .filter(BalanceHistory.company_id == company_id)
        .order_by(BalanceHistory.recharged_at.desc())
        .all()
    )

    # Days since last recharge
    now = datetime.utcnow()
    days_since_last_recharge = None
    if records:
        delta = now - records[0].recharged_at
        days_since_last_recharge = delta.days  # e.g. 4 days if recharged 4 full days ago

    history = [
        {
            "id": r.id,
            "amount_minutes": r.amount_minutes,
            "balance_after": r.balance_after,
            "note": r.note,
            "recharged_at": r.recharged_at.isoformat(),
        }
        for r in records
    ]

    return {
        "company_id": company.id,
        "company_name": company.company_name,
        "company_slug": company.company_slug,
        "current_balance_minutes": company.balance_minutes,
        "total_recharges": len(records),
        "days_since_last_recharge": days_since_last_recharge,
        "last_recharged_at": records[0].recharged_at.isoformat() if records else None,
        "history": history,
    }

@app.get("/", response_class=HTMLResponse)
async def admin_dashboard():
    """Serve the admin dashboard"""
    with open("static/admin.html", "r") as f:
        return HTMLResponse(content=f.read())

@app.post("/api/jobs")
async def create_job(job: JobCreate, db: Session = Depends(get_db)):
    """Create a new job record associated with a call"""
    db_job = JobRecord(
        jobNO=job.jobNO,
        bookingId=job.bookingId,
        passengerName=job.passengerName,
        customerPrice=job.customerPrice,
        date=job.date,
        origin=job.origin,
        destination=job.destination,
        vehicleType=job.vehicleType,
        call_sid=job.call_sid
    )
    db.add(db_job)
    db.commit()
    db.refresh(db_job)
    return {"status": "success", "job_id": db_job.id}

@app.get("/api/jobs/{call_sid}", response_model=List[JobRecordResponse])
async def get_jobs_by_call_sid(call_sid: str, db: Session = Depends(get_db)):
    """Get all job records for a specific call SID"""
    jobs = db.query(JobRecord).filter(JobRecord.call_sid == call_sid).all()
    return jobs

@app.get("/api/agents", response_model=List[AgentResponse])
async def get_agents(db: Session = Depends(get_db)):
    """Get all agents"""
    agents = db.query(WebAgent).all()
    return agents

@app.get("/api/agents/{agent_id}", response_model=AgentResponse)
async def get_agent(agent_id: int, db: Session = Depends(get_db)):
    """Get a specific agent by ID"""
    agent = db.query(WebAgent).filter(WebAgent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent

@app.get("/api/agents/slug/{company_slug}", response_model=AgentResponse)
async def get_agent_by_slug(company_slug: str, db: Session = Depends(get_db)):
    """Get agent by company slug"""
    agent = db.query(WebAgent).filter(WebAgent.company_slug == company_slug).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent

@app.post("/api/agents", response_model=AgentResponse)
async def create_agent(agent: AgentCreate, db: Session = Depends(get_db)):
    """Create a new agent"""
    # Generate company slug
    company_slug = create_slug(agent.company_name)
    
    # Check if slug already exists
    existing_agent = db.query(WebAgent).filter(WebAgent.company_slug == company_slug).first()
    if existing_agent:
        # Add timestamp to make it unique
        company_slug = f"{company_slug}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    
    import uuid
    api_token = agent.api_Token if agent.api_Token else str(uuid.uuid4())

    # Create new agent
    db_agent = WebAgent(
        agent_name=agent.agent_name,
        company_name=agent.company_name,
        company_id=agent.company_id,
        company_slug=company_slug,
        greeting_message=agent.greeting_message,
        knowledge_base=agent.knowledge_base,
        knowledge_type=agent.knowledge_type,
        api_Token=api_token,
        ultravox_agent_id=agent.ultravox_agent_id,
        voice=agent.voice,
        psid=agent.psid,
        web_call_pin=agent.web_call_pin
    )
    
    db.add(db_agent)
    db.commit()
    db.refresh(db_agent)
    
    # Ensure company exists in companies table
    company = db.query(Company).filter(Company.company_slug == company_slug).first()
    if not company:
        new_company = Company(
            company_name=agent.company_name or agent.agent_name,
            company_slug=company_slug,
            balance_minutes=20  # Default balance
        )
        # If company_id is provided and doesn't conflict, we could potentially use it
        # but the table uses AUTOINCREMENT for ID, so we'll let it handle it.
        db.add(new_company)
        db.commit()
    
    # Ingest knowledge base if provided
    if agent.knowledge_base and agent.knowledge_base.strip():
        try:
            if agent.knowledge_type == 'url':
                result = rag_service.ingest_url(db_agent.id, agent.knowledge_base)
                print(f"✅ Ingested URL knowledge: {result.get('chunks_created', 0)} chunks")
            else:  # text or file (file content passed as text in creation)
                result = rag_service.ingest_text(db_agent.id, agent.knowledge_base, source_type=agent.knowledge_type or "text")
                print(f"✅ Ingested {agent.knowledge_type} knowledge: {result.get('chunks_created', 0)} chunks")
            
            # Update knowledge source
            db_agent.knowledge_source = agent.knowledge_base[:100] if agent.knowledge_type == 'url' else "direct_input"
            db.commit()
        except Exception as e:
            print(f"❌ Error ingesting knowledge: {e}")
            # Don't fail agent creation if knowledge ingestion fails
    
    return db_agent

@app.put("/api/agents/{agent_id}", response_model=AgentResponse)
async def update_agent(agent_id: int, agent: AgentUpdate, db: Session = Depends(get_db)):
    """Update an existing agent"""
    db_agent = db.query(WebAgent).filter(WebAgent.id == agent_id).first()
    if not db_agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    # Update fields
    # Update fields only if they were explicitly set in the request
    update_data = agent.model_dump(exclude_unset=True)
    
    if "agent_name" in update_data:
        db_agent.agent_name = update_data["agent_name"]
    
    if "company_name" in update_data:
        old_name = db_agent.company_name
        db_agent.company_name = update_data.get("company_name", db_agent.company_name)
        # Only update slug if company name actually changed
        if old_name != db_agent.company_name:
            new_slug = create_slug(db_agent.company_name)
            existing_agent = db.query(WebAgent).filter(WebAgent.company_slug == new_slug).first()
            if existing_agent and existing_agent.id != db_agent.id:
                new_slug = f"{new_slug}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
            db_agent.company_slug = new_slug
    
    if "company_id" in update_data:
        db_agent.company_id = update_data["company_id"]
    if "greeting_message" in update_data:
        db_agent.greeting_message = update_data["greeting_message"]
    if "knowledge_base" in update_data:
        db_agent.knowledge_base = update_data["knowledge_base"]
    if "knowledge_type" in update_data:
        db_agent.knowledge_type = update_data["knowledge_type"]
    if "is_active" in update_data:
        db_agent.is_active = update_data["is_active"]
    if "api_Token" in update_data:
        db_agent.api_Token = update_data["api_Token"]
    if "ultravox_agent_id" in update_data:
        db_agent.ultravox_agent_id = update_data["ultravox_agent_id"]
    if "voice" in update_data:
        db_agent.voice = update_data["voice"]
    if "psid" in update_data:
        db_agent.psid = update_data["psid"]
    if "web_call_pin" in update_data:
        db_agent.web_call_pin = update_data["web_call_pin"]
    
    db_agent.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(db_agent)
    
    # Ensure company exists in companies table after update
    company = db.query(Company).filter(Company.company_slug == db_agent.company_slug).first()
    if not company:
        new_company = Company(
            company_name=db_agent.company_name or db_agent.agent_name,
            company_slug=db_agent.company_slug,
            balance_minutes=20
        )
        db.add(new_company)
        db.commit()
    
    return db_agent

@app.delete("/api/agents/{agent_id}")
async def delete_agent(agent_id: int, db: Session = Depends(get_db)):
    """Delete an agent. Logs a snapshot to deleted_agents before removal."""
    agent = db.query(WebAgent).filter(WebAgent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    # Audit logging before deletion
    deleted_agent = DeletedAgent(
        original_agent_id=agent.id,
        agent_name=agent.agent_name,
        company_name=agent.company_name,
        company_id=agent.company_id,
        company_slug=agent.company_slug,
        ultravox_agent_id=agent.ultravox_agent_id,
        was_active=agent.is_active,
        agent_created_at=agent.created_at
    )
    db.add(deleted_agent)
    
    db.delete(agent)
    db.commit()
    return {"message": "Agent deleted successfully", "audit_id": deleted_agent.id}

@app.post("/api/agents/{agent_id}/web-call", response_model=WebCallResponse)
async def create_web_call(
    agent_id: int, 
    request: Request,
    db: Session = Depends(get_db)
):
    """Create a direct Ultravox web call for an agent. Requires Bearer token authentication."""
    
    # 1. Fetch Agent
    agent = db.query(WebAgent).filter(WebAgent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    if not agent.is_active:
        raise HTTPException(status_code=403, detail="Agent is inactive")

    # 2. Authenticate via Bearer Token
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    
    provided_token = auth_header.split(" ")[1]
    if provided_token != agent.api_Token:
        raise HTTPException(status_code=401, detail="Invalid API Token")

    # 3. Check Company Balance
    company = db.query(Company).filter(Company.company_slug == agent.company_slug).first()
    if not company:
        # Fallback to check by ID if slug fails
        company = db.query(Company).filter(Company.id == agent.company_id).first()
    
    if not company or company.balance_minutes <= 0:
        raise HTTPException(status_code=402, detail="Insufficient balance. Please top up.")

    # 4. Prepare Ultravox Configuration
    if not ULTRAVOX_API_KEY:
        raise HTTPException(status_code=500, detail="ULTRAVOX_API_KEY not configured on server")

    agent_prompt = merge_prompt_with_customizations(agent)
    agent_tools = get_agent_tools(agent)
    
    # Use agent's configured voice or fallback to default
    voice = agent.voice or "terrence"
    
    time_limit_seconds = int(company.balance_minutes * 60)
    
    ultravox_config = {
        "systemPrompt": agent_prompt,
        "model": "fixie-ai/ultravox",
        "voice": voice,
        "temperature": 0.3,
        "firstSpeaker": "FIRST_SPEAKER_AGENT",
        "selectedTools": agent_tools,
        "recordingEnabled": True,
        "maxDuration": f"{time_limit_seconds}s",
        "timeExceededMessage": "I'm sorry, but your company's balance has run out. Please contact your administrator to add funds. Goodbye!",
        "joinTimeout": "1800s",
        "inactivityMessages": [
            {
                "duration": "10s",
                "message": "Are you still with me? Or would you like me to rephrase the question?"
            }
        ],
        "callbacks": {
            "ended": {
                "url": "https://agent.cabex.co.uk/api/webhooks/ultravox"
            }
        }
    }

    # 5. Create Ultravox Call
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{ULTRAVOX_BASE_URL}/calls",
                headers={
                    "X-API-Key": ULTRAVOX_API_KEY,
                    "Content-Type": "application/json"
                },
                json=ultravox_config,
                timeout=30.0
            )
            
            if response.status_code not in [200, 201]:
                error_detail = f"Ultravox API error: {response.status_code} - {response.text}"
                print(f"❌ {error_detail}")
                raise HTTPException(status_code=response.status_code, detail=error_detail)
            
            result = response.json()
            call_id = result.get('callId')
            join_url = result.get('joinUrl')

            # 6. Log Call Initiation
            call_record = CallRecord(
                call_sid=call_id,
                agent_id=agent.id,
                status="initiated",
                caller_number="Web Call",
                created_at=datetime.utcnow()
            )
            db.add(call_record)
            db.commit()

            # 7. Register Call with Dispatcher (for tool call linking)
            try:
                dispatcher_url = os.getenv("TOOLS_BASE_URL", "http://localhost:3000")
                if "localhost:8000" in dispatcher_url: # Handle common default
                    dispatcher_url = "http://localhost:3000"
                
                async with httpx.AsyncClient() as dispatcher_client:
                    reg_response = await dispatcher_client.post(
                        f"{dispatcher_url}/cromwell/register-call",
                        json={"call_id": call_id},
                        timeout=5.0
                    )
                    if reg_response.status_code == 200:
                        print(f"📋 Registered call {call_id} with dispatcher")
                    else:
                        print(f"⚠️ Failed to register call {call_id}: {reg_response.status_code}")
            except Exception as reg_err:
                print(f"⚠️ Dispatcher registration error: {str(reg_err)}")

            print(f"✅ Web call created for agent {agent.id}: {call_id}")
            
            return WebCallResponse(
                callId=call_id,
                joinUrl=join_url,
                agent_id=agent.id,
                agent_name=agent.agent_name,
                company_slug=agent.company_slug
            )

    except Exception as e:
        print(f"❌ Error creating web call: {str(e)}")
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=f"Internal server error while creating call: {str(e)}")

@app.get("/api/agents/{agent_id}/prompt")
async def get_agent_prompt(agent_id: int, db: Session = Depends(get_db)):
    """Get the merged prompt for an agent"""
    agent = db.query(WebAgent).filter(WebAgent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    merged_prompt = merge_prompt_with_customizations(agent)
    return {"prompt": merged_prompt}

@app.get("/api/agents/{agent_id}/tools")
async def get_agent_tools_endpoint(agent_id: int, db: Session = Depends(get_db)):
    """Get the tools configuration for an agent"""
    agent = db.query(WebAgent).filter(WebAgent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    tools = get_agent_tools(agent)
    return {"tools": tools}

@app.post("/api/agents/{agent_id}/knowledge/file")
async def upload_knowledge_file(
    agent_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    """Upload a knowledge base file for an agent"""
    agent = db.query(WebAgent).filter(WebAgent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    # Check file type
    allowed_extensions = ['.txt', '.md', '.csv', '.json', '.pdf']
    file_extension = os.path.splitext(file.filename)[1].lower()
    
    if file_extension not in allowed_extensions:
        raise HTTPException(status_code=400, detail=f"File type {file_extension} not supported. Allowed: {', '.join(allowed_extensions)}")
    
    # Read file content
    content = await file.read()
    
    try:
        # Process and ingest file using RAG service
        result = rag_service.ingest_file(agent_id, content, file.filename)
        
        # Store raw content for reference (truncated)
        if file_extension == '.pdf':
            text_content = rag_service.process_pdf(content)
        else:
            text_content = content.decode('utf-8')
            
        text_preview = text_content[:500] + "..." if len(text_content) > 500 else text_content
        
        # Update agent
        agent.knowledge_base = text_preview
        agent.knowledge_type = "file"
        agent.knowledge_source = file.filename
        agent.updated_at = datetime.utcnow()
        
        db.commit()
        
        return {
            "message": f"Knowledge base file '{file.filename}' uploaded successfully",
            "chunks_created": result.get("chunks_created", 0)
        }
            
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error processing file: {str(e)}")

@app.post("/api/agents/{agent_id}/knowledge/search")
async def search_knowledge_base(
    agent_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """Search the agent's knowledge base using vector similarity"""
    agent = db.query(WebAgent).filter(WebAgent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    try:
        body = await request.json()
        query = body.get("query", "")
        
        if not query:
            return {"result": "Please provide a search query"}
        
        # Use RAG service for semantic search
        result = rag_service.search(agent_id, query, top_k=3)
        
        return result
            
    except Exception as e:
        return {"result": f"Error searching knowledge base: {str(e)}"}

@app.post("/api/agents/{agent_id}/knowledge/url")
async def set_knowledge_url(
    agent_id: int,
    url: str = Form(...),
    db: Session = Depends(get_db)
):
    """Scrape URL and ingest content into knowledge base"""
    agent = db.query(WebAgent).filter(WebAgent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    try:
        # Scrape and ingest URL using RAG service
        result = rag_service.ingest_url(agent_id, url)
        
        # Get a preview of the scraped content
        scraped_text = rag_service.scrape_url(url)
        text_preview = scraped_text[:500] + "..." if len(scraped_text) > 500 else scraped_text
        
        # Update agent
        agent.knowledge_base = text_preview
        agent.knowledge_type = "url"
        agent.knowledge_source = url
        agent.updated_at = datetime.utcnow()
        
        db.commit()
        
        return {
            "message": "Knowledge base URL scraped and ingested successfully",
            "chunks_created": result.get("chunks_created", 0),
            "url": url
        }
            
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid URL or unable to fetch content: {str(e)}")

@app.post("/api/agents/{agent_id}/knowledge/text")
async def set_knowledge_text(
    agent_id: int,
    text: str = Form(...),
    db: Session = Depends(get_db)
):
    """Set text-based knowledge for an agent"""
    agent = db.query(WebAgent).filter(WebAgent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    try:
        # Ingest text using RAG service
        result = rag_service.ingest_text(agent_id, text, source_type="text")
        
        # Store preview
        text_preview = text[:500] + "..." if len(text) > 500 else text
    
    # Update agent
        agent.knowledge_base = text_preview
        agent.knowledge_type = "text"
        agent.knowledge_source = "direct_input"
        agent.updated_at = datetime.utcnow()
        
        db.commit()
        
        return {
            "message": "Knowledge base text ingested successfully",
            "chunks_created": result.get("chunks_created", 0)
        }
            
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error ingesting text: {str(e)}")

# Agent Calls API Endpoints

# Agent Calls API Endpoints

@app.get("/api/agents/{agent_id}/calls", response_model=CallListResponse)
async def get_agent_calls(
    agent_id: int,
    limit: Optional[int] = 50,
    cursor: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Get all calls for a specific agent from Ultravox by fetching all calls and filtering locally"""
    agent = db.query(WebAgent).filter(WebAgent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    # if not agent.ultravox_agent_id:
    #     raise HTTPException(status_code=400, detail="Agent has no Ultravox agent ID configured")
    
    try:
        # Fetch all calls from Ultravox (we'll need to handle pagination to get all calls)
        all_calls = []
        next_cursor = cursor
        calls_fetched = 0
        max_fetch_limit = 1000  # Prevent infinite loops
        
        while calls_fetched < max_fetch_limit:
            if next_cursor:
                # If we have a next cursor (which is a full URL), use it directly
                # We need to strip the base URL because make_ultravox_request adds it
                if next_cursor.startswith(ULTRAVOX_BASE_URL):
                    endpoint = next_cursor[len(ULTRAVOX_BASE_URL):].lstrip('/')
                else:
                    # Fallback if URL format is different
                    endpoint = next_cursor
            else:
                # First request
                endpoint = "calls?limit=100"
            
            # Make request to Ultravox API to get all calls
            response_data = await make_ultravox_request("GET", endpoint)
            
            batch_calls = response_data.get("results", [])
            if not batch_calls:
                break
                
            # Filter calls for this specific agent
            for call_data in batch_calls:
                match_found = False
                
                # 1. Check by Ultravox Agent ID (if matched, it's definitive)
                if agent.ultravox_agent_id and call_data.get("agentId") == agent.ultravox_agent_id:
                    match_found = True
                
                # 2. Check by Phone Number (if agent has one)
                elif agent.twilio_phone_number:
                    medium = call_data.get("medium", {})
                    # Check incoming from (if user called agent)
                    if "twilio" in medium and "incoming" in medium["twilio"]:
                        if medium["twilio"]["incoming"].get("to") == agent.twilio_phone_number:
                            match_found = True
                    # Check outgoing to (if agent called user - less likely for this use case but good to have)
                    elif "twilio" in medium and "outgoing" in medium["twilio"]:
                        if medium["twilio"]["outgoing"].get("from") == agent.twilio_phone_number:
                            match_found = True
                            
                # 3. Check by System Prompt (fuzzy match on agent name)
                if not match_found and agent.agent_name:
                    system_prompt = call_data.get("systemPrompt", "")
                    # Check for "Your Name: {agent_name}" pattern or just the name
                    if system_prompt and agent.agent_name in system_prompt:
                        # Verify it's not a false positive (e.g. "Alex" in "Alexander")
                        # Ideally we'd use regex but simple containment is a good start for now
                        match_found = True
                
                if match_found:
                    all_calls.append(call_data)
            
            calls_fetched += len(batch_calls)
            
            # Check if there are more calls to fetch
            next_cursor = response_data.get("next")
            if not next_cursor:
                break
            
            # If we have enough calls for this agent, we can stop
            if len(all_calls) >= limit:
                break
        
        # Sort calls by creation date (newest first)
        all_calls.sort(key=lambda x: x.get("created", ""), reverse=True)
        
        # Apply limit to the filtered results
        limited_calls = all_calls[:limit] if limit else all_calls
        
        # Transform the response to match our model
        calls = []
        for call_data in limited_calls:
            # Extract caller phone number from medium if available
            caller_phone = None
            medium = call_data.get("medium", {})
            if "twilio" in medium:
                if "outgoing" in medium["twilio"]:
                    caller_phone = medium["twilio"]["outgoing"].get("to")
                elif "incoming" in medium["twilio"]:
                    caller_phone = medium["twilio"]["incoming"].get("from")
            elif "sip" in medium:
                if "outgoing" in medium["sip"]:
                    caller_phone = medium["sip"]["outgoing"].get("to")
                elif "incoming" in medium["sip"]:
                    caller_phone = medium["sip"]["incoming"].get("from")
                
            call = CallResponse(
                call_id=call_data.get("callId", ""),
                agent_id=call_data.get("agentId", ""),
                status=call_data.get("endReason", "unknown") if call_data.get("ended") else "active",
                created_at=call_data.get("created", ""),
                joined_at=call_data.get("joined"),
                ended_at=call_data.get("ended"),
                end_reason=call_data.get("endReason"),
                duration=None,  # Calculate from timestamps if needed
                billed_duration=call_data.get("billedDuration"),
                caller_phone_number=caller_phone,
                short_summary=call_data.get("shortSummary"),
                summary=call_data.get("summary"),
                recording_enabled=call_data.get("recordingEnabled", False)
            )
            
            # Calculate duration if we have both created and ended timestamps
            if call_data.get("created") and call_data.get("ended"):
                try:
                    created = datetime.fromisoformat(call_data["created"].replace('Z', '+00:00'))
                    ended = datetime.fromisoformat(call_data["ended"].replace('Z', '+00:00'))
                    call.duration = int((ended - created).total_seconds())
                except:
                    pass
            
            calls.append(call)
        
        return CallListResponse(
            results=calls,
            next=None,  # We're handling pagination locally, so no next cursor
            previous=None,
            total=len(all_calls)  # Total filtered calls for this agent
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching agent calls: {str(e)}")

@app.get("/api/agents/{agent_id}/calls/{call_id}/recording/stream")
async def stream_call_recording(
    agent_id: int,
    call_id: str,
    db: Session = Depends(get_db)
):
    """Redirect to the actual recording URL for a specific call"""
    # Check local database first if it's a Twilio SID
    if call_id.startswith('CA'):
        local_call = db.query(CallRecord).filter(CallRecord.call_sid == call_id).first()
        if local_call and local_call.agent_id == agent_id and local_call.recording_url:
            return RedirectResponse(url=local_call.recording_url)

    try:
        # Get call recording from Ultravox API
        recording_endpoint = f"calls/{call_id}/recording"
        recording_data = await make_ultravox_request("GET", recording_endpoint)
        
        recording_url = recording_data.get("recordingUrl")
        if not recording_url:
            raise HTTPException(status_code=404, detail="Recording URL not found")
            
    except HTTPException as e:
        # If Ultravox says recording not enabled, we can't do much
        if "Recording was not enabled" in str(e.detail):
            raise HTTPException(status_code=404, detail="Recording not enabled for this call.")
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching recording: {str(e)}")

@app.get("/api/agents/{agent_id}/calls/{call_id}/recording", response_model=CallRecordingResponse)
async def get_call_recording(
    agent_id: int,
    call_id: str,
    include_messages: bool = False,
    db: Session = Depends(get_db)
):
    """Get call recording and transcription for a specific call"""
    agent = db.query(WebAgent).filter(WebAgent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    # if not agent.ultravox_agent_id:
    #     raise HTTPException(status_code=400, detail="Agent has no Ultravox agent ID configured")
    
    try:
        # Check local database first if it's a Twilio SID
        if call_id.startswith('CA'):
            local_call = db.query(CallRecord).filter(CallRecord.call_sid == call_id).first()
            if local_call and local_call.agent_id == agent_id:
                return CallRecordingResponse(
                    call_id=call_id,
                    recording_url=local_call.recording_url,
                    transcript=local_call.transcript,
                    summary=local_call.summary,
                    short_summary=None,
                    messages=None
                )

        # Get call details from Ultravox if not found locally or not a Twilio SID
        call_endpoint = f"calls/{call_id}"
        call_data = await make_ultravox_request("GET", call_endpoint)
        
        # Verify this call belongs to the agent
        match_found = False
        
        # 1. Check by Ultravox Agent ID
        if agent.ultravox_agent_id and call_data.get("agentId") == agent.ultravox_agent_id:
            match_found = True
            
        # 2. Check by Phone Number
        elif agent.twilio_phone_number:
            medium = call_data.get("medium", {})
            if "twilio" in medium:
                if "incoming" in medium["twilio"] and medium["twilio"]["incoming"].get("to") == agent.twilio_phone_number:
                    match_found = True
                elif "outgoing" in medium["twilio"] and medium["twilio"]["outgoing"].get("from") == agent.twilio_phone_number:
                    match_found = True
                    
        # 3. Check by System Prompt
        if not match_found and agent.agent_name:
            system_prompt = call_data.get("systemPrompt", "")
            if system_prompt and agent.agent_name in system_prompt:
                match_found = True
                
        if not match_found:
            raise HTTPException(status_code=403, detail="Call does not belong to this agent")

        # Get call recording from Ultravox API
        recording_endpoint = f"calls/{call_id}/recording"
        recording_data = await make_ultravox_request("GET", recording_endpoint)
        
        # Get call details for additional information
        call_endpoint = f"calls/{call_id}"
        call_data = await make_ultravox_request("GET", call_endpoint)
        
        # Extract transcript from call messages if available
        transcript = None
        messages = None
        
        if include_messages:
            try:
                messages_endpoint = f"calls/{call_id}/messages"
                messages_data = await make_ultravox_request("GET", messages_endpoint)
                messages = messages_data.get("results", [])
                
                # Build transcript from messages
                transcript_parts = []
                for message in messages:
                    role = message.get("role", "")
                    text = message.get("text", "")
                    if text:
                        transcript_parts.append(f"{role}: {text}")
                transcript = "\n".join(transcript_parts)
            except:
                # If messages endpoint fails, try to get transcript from call data
                pass
        
        # Fallback: try to get transcript from call data
        if not transcript and call_data.get("transcript"):
            transcript_parts = []
            for message in call_data.get("transcript", []):
                role = message.get("role", "")
                text = message.get("text", "")
                if text:
                    transcript_parts.append(f"{role}: {text}")
            transcript = "\n".join(transcript_parts)
        
        return CallRecordingResponse(
            call_id=call_id,
            recording_url=recording_data if isinstance(recording_data, str) else recording_data.get("recordingUrl"),
            transcript=transcript,
            summary=call_data.get("summary"),
            short_summary=call_data.get("shortSummary"),
            messages=messages if include_messages else None
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching call recording: {str(e)}")

# Additional endpoint to get call details
@app.get("/api/agents/{agent_id}/calls/{call_id}", response_model=CallResponse)
async def get_call_details(
    agent_id: int,
    call_id: str,
    db: Session = Depends(get_db)
):
    """Get detailed information about a specific call"""
    agent = db.query(WebAgent).filter(WebAgent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    # if not agent.ultravox_agent_id:
    #     raise HTTPException(status_code=400, detail="Agent has no Ultravox agent ID configured")
    
    try:
        # Get call details first to verify ownership
        call_data = await make_ultravox_request("GET", f"calls/{call_id}")
        
        # Verify this call belongs to the agent
        match_found = False
        
        # 1. Check by Ultravox Agent ID
        if agent.ultravox_agent_id and call_data.get("agentId") == agent.ultravox_agent_id:
            match_found = True
            
        # 2. Check by Phone Number
        elif agent.twilio_phone_number:
            medium = call_data.get("medium", {})
            if "twilio" in medium:
                if "incoming" in medium["twilio"] and medium["twilio"]["incoming"].get("to") == agent.twilio_phone_number:
                    match_found = True
                elif "outgoing" in medium["twilio"] and medium["twilio"]["outgoing"].get("from") == agent.twilio_phone_number:
                    match_found = True
                    
        # 3. Check by System Prompt
        if not match_found and agent.agent_name:
            system_prompt = call_data.get("systemPrompt", "")
            if system_prompt and agent.agent_name in system_prompt:
                match_found = True
                
        if not match_found:
            raise HTTPException(status_code=403, detail="Call does not belong to this agent")
            
        # Extract caller phone number from medium if available
        caller_phone = None
        medium = call_data.get("medium", {})
        if "twilio" in medium:
            if "outgoing" in medium["twilio"]:
                caller_phone = medium["twilio"]["outgoing"].get("to")
            elif "incoming" in medium["twilio"]:
                caller_phone = medium["twilio"]["incoming"].get("from")
        elif "sip" in medium:
            if "outgoing" in medium["sip"]:
                caller_phone = medium["sip"]["outgoing"].get("to")
            elif "incoming" in medium["sip"]:
                caller_phone = medium["sip"]["incoming"].get("from")
        
        call = CallResponse(
            call_id=call_data.get("callId", ""),
            agent_id=call_data.get("agentId", ""),
            status=call_data.get("endReason", "unknown") if call_data.get("ended") else "active",
            created_at=call_data.get("created", ""),
            joined_at=call_data.get("joined"),
            ended_at=call_data.get("ended"),
            end_reason=call_data.get("endReason"),
            duration=None,
            billed_duration=call_data.get("billedDuration"),
            caller_phone_number=caller_phone,
            short_summary=call_data.get("shortSummary"),
            summary=call_data.get("summary"),
            recording_enabled=call_data.get("recordingEnabled", False)
        )
        
        # Calculate duration if we have both created and ended timestamps
        if call_data.get("created") and call_data.get("ended"):
            try:
                created = datetime.fromisoformat(call_data["created"].replace('Z', '+00:00'))
                ended = datetime.fromisoformat(call_data["ended"].replace('Z', '+00:00'))
                call.duration = int((ended - created).total_seconds())
            except:
                pass
        
        return call
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching call details: {str(e)}")

# Endpoint to get call messages/transcript
@app.get("/api/agents/{agent_id}/calls/{call_id}/messages")
async def get_call_messages(
    agent_id: int,
    call_id: str,
    limit: Optional[int] = 100,
    cursor: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Get all messages/transcript for a specific call"""
    agent = db.query(WebAgent).filter(WebAgent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    if not agent.ultravox_agent_id:
        raise HTTPException(status_code=400, detail="Agent has no Ultravox agent ID configured")
    
    try:
        # First verify the call belongs to this agent
        call_endpoint = f"calls/{call_id}"
        call_data = await make_ultravox_request("GET", call_endpoint)
        
        if call_data.get("agentId") != agent.ultravox_agent_id:
            raise HTTPException(status_code=403, detail="Call does not belong to this agent")
        
        # Build query parameters for messages
        params = ""
        if limit:
            params += f"limit={limit}"
        if cursor:
            params += f"&cursor={cursor}" if params else f"cursor={cursor}"
        
        # Get call messages from Ultravox API
        endpoint = f"calls/{call_id}/messages"
        if params:
            endpoint += f"?{params}"
            
        messages_data = await make_ultravox_request("GET", endpoint)
        
        return {
            "call_id": call_id,
            "agent_id": agent_id,
            "messages": messages_data.get("results", []),
            "next": messages_data.get("next"),
            "previous": messages_data.get("previous"),
            "total": messages_data.get("total")
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching call messages: {str(e)}")

@app.put("/api/agents/{agent_id}/ultravox-id")
async def set_ultravox_agent_id(
    agent_id: int,
    ultravox_agent_id: str = Form(...),
    db: Session = Depends(get_db)
):
    """Set the Ultravox agent ID for an agent"""
    agent = db.query(WebAgent).filter(WebAgent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    # Update agent
    agent.ultravox_agent_id = ultravox_agent_id
    agent.updated_at = datetime.utcnow()
    db.commit()
    
    return {
        "message": "Ultravox agent ID updated successfully",
        "agent_id": agent_id,
        "ultravox_agent_id": ultravox_agent_id
    }

# ============= ADMIN UPDATES API =============
# New dedicated endpoints for advanced admin operations

@app.put("/api/admin-updates/agents/{agent_id}/custom-prompt")
async def set_custom_prompt(
    agent_id: int,
    custom_prompt: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    """
    Set a CUSTOM prompt that completely replaces the default prompt.
    This allows companies to define their own agent behavior (e.g., medical assistant, legal advisor).
    Tools remain locked - only the prompt changes.
    
    To REMOVE custom prompt and go back to default, pass empty string or omit the parameter.
    """
    agent = db.query(WebAgent).filter(WebAgent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    # Set or clear custom prompt
    if custom_prompt and custom_prompt.strip():
        agent.custom_prompt = custom_prompt.strip()
        message = "Custom prompt set successfully. Agent will now use this prompt instead of default."
    else:
        agent.custom_prompt = None
        message = "Custom prompt removed. Agent will now use default prompt with company substitutions."
    
    agent.updated_at = datetime.utcnow()
    db.commit()
    
    return {
        "message": message,
        "agent_id": agent_id,
        "agent_name": agent.agent_name,
        "company_name": agent.company_name,
        "has_custom_prompt": agent.custom_prompt is not None and bool(agent.custom_prompt.strip()),
        "prompt_preview": agent.custom_prompt[:200] + "..." if agent.custom_prompt and len(agent.custom_prompt) > 200 else agent.custom_prompt
    }

@app.get("/api/admin-updates/agents/{agent_id}/custom-prompt")
async def get_custom_prompt(
    agent_id: int,
    db: Session = Depends(get_db)
):
    """Get the current custom prompt for an agent (if any)"""
    agent = db.query(WebAgent).filter(WebAgent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    return {
        "agent_id": agent_id,
        "agent_name": agent.agent_name,
        "company_name": agent.company_name,
        "has_custom_prompt": agent.custom_prompt is not None and bool(agent.custom_prompt.strip()),
        "custom_prompt": agent.custom_prompt,
        "using_default": agent.custom_prompt is None or not agent.custom_prompt.strip()
    }

# END ADMIN UPDATES API


# Twilio Integration Endpoints

@app.get("/api/twilio/numbers/available")
async def get_available_twilio_numbers(
    country: str = "US",
    area_code: Optional[str] = None
):
    """Get available Twilio phone numbers"""
    if not twilio_client:
        raise HTTPException(status_code=503, detail="Twilio client not configured")
    
    try:
        search_params = {
            "voice_enabled": True,
            "sms_enabled": True
        }
        
        if area_code:
            search_params["area_code"] = area_code
        
        # Search for available local numbers
        available_numbers = twilio_client.available_phone_numbers(country).local.list(
            **search_params,
            limit=20
        )
        
        numbers = [
            {
                "phone_number": num.phone_number,
                "friendly_name": num.friendly_name,
                "locality": num.locality,
                "region": num.region,
                "capabilities": {
                    "voice": num.capabilities.get("voice", False),
                    "sms": num.capabilities.get("SMS", False)
                }
            }
            for num in available_numbers
        ]
        
        return {"available_numbers": numbers, "count": len(numbers)}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching Twilio numbers: {str(e)}")

@app.get("/api/twilio/numbers/purchased")
async def get_purchased_twilio_numbers():
    """Get all purchased Twilio phone numbers"""
    if not twilio_client:
        raise HTTPException(status_code=503, detail="Twilio client not configured")
    
    try:
        incoming_numbers = twilio_client.incoming_phone_numbers.list()
        
        numbers = [
            {
                "sid": num.sid,
                "phone_number": num.phone_number,
                "friendly_name": num.friendly_name,
                "voice_url": num.voice_url,
                "sms_url": num.sms_url,
                "status": num.status
            }
            for num in incoming_numbers
        ]
        
        return {"purchased_numbers": numbers, "count": len(numbers)}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching purchased numbers: {str(e)}")

@app.post("/api/twilio/numbers/purchase")
async def purchase_twilio_number(
    phone_number: str = Form(...),
    db: Session = Depends(get_db)
):
    """Purchase a new Twilio phone number"""
    if not twilio_client:
        raise HTTPException(status_code=503, detail="Twilio client not configured")
    
    try:
        # Purchase the number
        purchased_number = twilio_client.incoming_phone_numbers.create(
            phone_number=phone_number,
            voice_url=f"{TWILIO_WEBHOOK_BASE_URL}/twilio/incoming",
            sms_url=f"{TWILIO_WEBHOOK_BASE_URL}/twilio/sms"
        )
        
        return {
            "message": "Phone number purchased successfully",
            "phone_number": purchased_number.phone_number,
            "sid": purchased_number.sid,
            "webhook_url": f"{TWILIO_WEBHOOK_BASE_URL}/twilio/incoming"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error purchasing number: {str(e)}")

@app.post("/api/agents/{agent_id}/twilio/assign")
async def assign_twilio_number(
    agent_id: int,
    phone_number: str = Form(...),
    psid: Optional[str] = Form(None),
    use_company_slug: bool = Form(default=True),
    db: Session = Depends(get_db)
):
    """Assign a Twilio number to an agent and configure webhook"""
    agent = db.query(WebAgent).filter(WebAgent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    if not twilio_client:
        raise HTTPException(status_code=503, detail="Twilio client not configured")
    
    try:
        # Find the phone number in Twilio
        incoming_numbers = twilio_client.incoming_phone_numbers.list(phone_number=phone_number)
        
        if not incoming_numbers:
            raise HTTPException(status_code=404, detail="Phone number not found in Twilio account")
        
        phone_sid = incoming_numbers[0].sid
        
        # Update webhook - use company slug for cleaner URLs
        if use_company_slug and agent.company_slug:
            webhook_url = f"{TWILIO_WEBHOOK_BASE_URL}/twilio/incoming/{agent.company_slug}"
        else:
            webhook_url = f"{TWILIO_WEBHOOK_BASE_URL}/twilio/incoming/{agent_id}"
        
        twilio_client.incoming_phone_numbers(phone_sid).update(
            voice_url=webhook_url,
            voice_method='POST'
        )
        
        # Update agent record
        agent.twilio_phone_number = phone_number
        # Use the SID from Twilio as the psid (Project/Phone SID)
        agent.psid = phone_sid
        agent.updated_at = datetime.utcnow()
        db.commit()
        
        return {
            "message": f"Phone number {phone_number} assigned to agent successfully",
            "agent_id": agent_id,
            "agent_name": agent.agent_name,
            "company_name": agent.company_name,
            "company_slug": agent.company_slug,
            "phone_number": phone_number,
            "psid": agent.psid,
            "webhook_url": webhook_url
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error assigning number: {str(e)}")

@app.delete("/api/agents/{agent_id}/twilio/unassign")
async def unassign_twilio_number(
    agent_id: int,
    db: Session = Depends(get_db)
):
    """Unassign Twilio number from an agent"""
    agent = db.query(WebAgent).filter(WebAgent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    if not agent.twilio_phone_number:
        raise HTTPException(status_code=400, detail="Agent has no assigned phone number")
    
    # Update agent record
    old_number = agent.twilio_phone_number
    agent.twilio_phone_number = None
    agent.psid = None  # Clear Phone SID — it belongs to the phone number
    agent.updated_at = datetime.utcnow()
    db.commit()
    
    return {
        "message": f"Phone number {old_number} unassigned from agent",
        "agent_id": agent_id
    }

@app.post("/twilio/incoming/{identifier}")
async def twilio_incoming(
    identifier: str,
    CallSid: str = Form(...),
    From: str = Form(None),
    To: str = Form(None),
    db: Session = Depends(get_db)
):
    """Handle incoming Twilio calls, log them, and connect to Ultravox"""
    # Find agent by ID or slug
    agent = None
    if identifier.isdigit():
        agent = db.query(WebAgent).filter(WebAgent.id == int(identifier)).first()
    else:
        agent = db.query(WebAgent).filter(WebAgent.company_slug == identifier).first()
    
    if not agent:
        # Fallback: check by To number
        if To:
            agent = db.query(WebAgent).filter(WebAgent.twilio_phone_number == To).first()
    
    if not agent:
        print(f"❌ Incoming call for unknown agent/slug: {identifier}")
        return HTMLResponse(content="<Response><Say>Sorry, this agent is not configured.</Say></Response>", media_type="application/xml")

    # Log the call
    call_record = CallRecord(
        call_sid=CallSid,
        agent_id=agent.id,
        caller_number=From,
        status="initiated"
    )
    db.add(call_record)
    db.commit()
    
    print(f"📞 Logged incoming call {CallSid} for agent {agent.agent_name}")

    # Return TwiML to connect to Ultravox
    # Note: In a real scenario, you'd use Ultravox's Twilio integration or a <Stream>
    # For now, we'll just return a placeholder TwiML that would normally be configured in Twilio
    # but we're adding the logging step here.
    
    # If the agent has an ultravox_agent_id, we could potentially trigger something here
    # but usually Twilio is configured to point to Ultravox directly.
    # By putting our service in the middle, we can log it.
    
    # For now, let's assume we want to forward to Ultravox or handle it.
    # If we don't have the Ultravox connection logic here, we should at least log it.
    
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>Connecting you to {agent.agent_name}...</Say>
    <!-- This is where you'd normally have the Ultravox <Connect><Stream> or similar -->
    <Pause length="1"/>
</Response>"""
    return HTMLResponse(content=twiml, media_type="application/xml")

@app.post("/twilio/status-callback")
async def twilio_status_callback(
    CallSid: str = Form(...),
    CallStatus: str = Form(None),
    CallDuration: int = Form(None),
    RecordingUrl: str = Form(None),
    db: Session = Depends(get_db)
):
    """Update call record status and recording URL"""
    call_record = db.query(CallRecord).filter(CallRecord.call_sid == CallSid).first()
    if call_record:
        if CallStatus:
            call_record.status = CallStatus
        if CallDuration:
            call_record.duration = CallDuration
        if RecordingUrl:
            call_record.recording_url = RecordingUrl
        
        call_record.updated_at = datetime.utcnow()
        db.commit()
        print(f"✅ Updated call {CallSid} status to {CallStatus}")
    
    return {"status": "success"}

@app.get("/api/agents/{agent_id}/call-history", response_model=List[CallHistoryResponse])
async def get_agent_call_history(
    agent_id: int,
    db: Session = Depends(get_db)
):
    """Get merged call history for an agent (local records + Ultravox fallback)"""
    agent = db.query(WebAgent).filter(WebAgent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    # 1. Get local call records — only those logged AFTER the agent was created
    #    (guards against recycled auto-increment IDs inheriting old records)
    local_calls = db.query(CallRecord).filter(
        CallRecord.agent_id == agent_id,
        CallRecord.created_at >= agent.created_at
    ).order_by(CallRecord.created_at.desc()).all()
    
    # 2. Get Ultravox calls as fallback/supplement
    # We'll use the existing get_agent_calls logic but transform it to CallHistoryResponse
    ultravox_calls_data = await get_agent_calls(agent_id=agent_id, limit=50, db=db)
    
    # Map local calls to a dictionary for easy lookup by call_sid
    merged_calls = {c.call_sid: CallHistoryResponse(
        id=c.id,
        call_sid=c.call_sid,
        agent_id=c.agent_id,
        caller_number=c.caller_number,
        status=c.status,
        recording_url=c.recording_url,
        duration=c.duration,
        created_at=c.created_at
    ) for c in local_calls}
    
            # Add Ultravox calls if not already in merged_calls
    for uv_call in ultravox_calls_data.results:
        if uv_call.call_id not in merged_calls:
            # Try to parse the date
            try:
                # Make it naive for comparison with local DB dates
                created_at = datetime.fromisoformat(uv_call.created_at.replace('Z', '+00:00')).replace(tzinfo=None)
            except:
                created_at = datetime.utcnow()
                
            merged_calls[uv_call.call_id] = CallHistoryResponse(
                id=0, # Placeholder for non-local calls
                call_sid=uv_call.call_id,
                agent_id=agent_id,
                caller_number=uv_call.caller_phone_number,
                status=uv_call.status,
                # Provide recording URL if status is not initiated (meaning it has joined or ended)
                # We'll try to provide it even if recording_enabled is false, just in case
                recording_url=f"/api/agents/{agent_id}/calls/{uv_call.call_id}/recording/stream" if uv_call.status != "initiated" else None,
                duration=uv_call.duration,
                created_at=created_at
            )
    
    # Sort by created_at descending
    result_list = list(merged_calls.values())
    result_list.sort(key=lambda x: x.created_at, reverse=True)
    
    return result_list

@app.post("/api/agents/{agent_id}/log-call")
async def log_agent_call(
    agent_id: int,
    call_data: CallLogRequest,
    db: Session = Depends(get_db)
):
    """Log or update a call record (used for both web and phone calls)"""
    agent = db.query(WebAgent).filter(WebAgent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    # Check if call already logged
    call_record = db.query(CallRecord).filter(CallRecord.call_sid == call_data.call_id).first()
    if call_record:
        # Update existing record
        if call_data.status:
            call_record.status = call_data.status
        if call_data.duration is not None:
            call_record.duration = call_data.duration
        if call_data.caller_number:
            call_record.caller_number = call_data.caller_number
            
        call_record.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(call_record)
        
        # Deduct balance if completed (handling update case)
        if not call_record.is_billed and call_data.status == "completed" and call_data.duration and call_data.duration > 0:
            company = db.query(Company).filter(Company.company_slug == agent.company_slug).first()
            if company:
                # Deduct based on precise duration
                minutes_to_deduct = call_data.duration / 60.0
                cost = minutes_to_deduct * (company.call_rate or 0.5)
                
                company.balance_amount = max(0.0, company.balance_amount - cost)
                # Sync balance_minutes
                if company.call_rate and company.call_rate > 0:
                    company.balance_minutes = company.balance_amount / company.call_rate
                else:
                    company.balance_minutes = company.balance_amount / 0.5
                
                call_record.is_billed = True
                db.commit()

        return call_record

    call_record = CallRecord(
        call_sid=call_data.call_id,
        agent_id=agent_id,
        caller_number=call_data.caller_number,
        status=call_data.status,
        duration=call_data.duration
    )
    db.add(call_record)
    
    # Deduct balance if completed (handling first-log case, though rare)
    if not call_record.is_billed and call_data.status == "completed" and call_data.duration and call_data.duration > 0:
        company = db.query(Company).filter(Company.company_slug == agent.company_slug).first()
        if company:
            # Deduct based on precise duration
            minutes_to_deduct = call_data.duration / 60.0
            cost = minutes_to_deduct * (company.call_rate or 0.5)
            
            company.balance_amount = max(0.0, company.balance_amount - cost)
            # Sync balance_minutes
            if company.call_rate and company.call_rate > 0:
                company.balance_minutes = company.balance_amount / company.call_rate
            else:
                company.balance_minutes = company.balance_amount / 0.5
            
            call_record.is_billed = True
                
    db.commit()
    db.refresh(call_record)
    
    print(f"📞 Logged/Updated call {call_data.call_id} for agent {agent.agent_name} (Status: {call_data.status})")
    return call_record

class UltravoxCallData(BaseModel):
    callId: str
    billedDuration: Optional[str] = None
    endOfCallReason: Optional[str] = None

class UltravoxWebhook(BaseModel):
    event: str
    call: UltravoxCallData

@app.post("/api/webhooks/ultravox")
async def ultravox_webhook(
    request: Request,
    db: Session = Depends(get_db)
):
    """Handle Ultravox webhooks for status updates and balance deduction"""
    try:
        payload = await request.json()
        print(f"📥 ULTRAVOX WEBHOOK PAYLOAD: {json.dumps(payload)}")
        
        event_type = payload.get("event")
        # Support both 'ended' and 'call.ended'
        if event_type not in ["ended", "call.ended"]:
            return {"status": "ignored", "event": event_type}
        
        call_data = payload.get("call", {})
        call_id = call_data.get("callId")
        
        if not call_id:
            print("⚠️ Webhook warning: No callId found in payload.")
            return {"status": "error", "message": "No callId"}

        print(f"📉 WEBHOOK RECEIVED: Call {call_id} ended.")
        
        # Find the call record
        call_record = db.query(CallRecord).filter(CallRecord.call_sid == call_id).first()
        if not call_record:
            print(f"⚠️ Webhook error: Call record {call_id} not found in database.")
            return {"status": "error", "message": "Call not found"}
        
        # Update status and duration
        call_record.status = "completed"
        
        billed_duration = 0
        billed_str = call_data.get("billedDuration")
        if billed_str:
            # billedDuration is "12.34s"
            try:
                billed_duration = float(billed_str.rstrip('s'))
                call_record.duration = int(billed_duration)
            except Exception as e:
                print(f"⚠️ Error parsing duration {billed_str}: {e}")
                
        call_record.updated_at = datetime.utcnow()
        db.commit()
        
        # Deduct balance from company
        agent = db.query(WebAgent).filter(WebAgent.id == call_record.agent_id).first()
        if not call_record.is_billed and agent and billed_duration > 0:
            company = db.query(Company).filter(Company.company_slug == agent.company_slug).first()
            if company:
                # billed_duration is in seconds, so we convert to minutes
                minutes_used = billed_duration / 60.0
                cost = minutes_used * (company.call_rate or 0.5)
                
                old_amount = company.balance_amount
                company.balance_amount = max(0.0, company.balance_amount - cost)
                
                # Sync balance_minutes (float minutes available)
                if company.call_rate and company.call_rate > 0:
                    company.balance_minutes = company.balance_amount / company.call_rate
                else:
                    # Fallback to 0.5 if rate is not set
                    company.balance_minutes = company.balance_amount / 0.5
                
                call_record.is_billed = True
                db.commit()
                print(f"💸 DEDUCTED £{cost:.2f} ({minutes_used:.2f} mins) for agent {agent.agent_name}. Amount: {old_amount} -> {company.balance_amount}. Mins: {company.balance_minutes}")
        
        return {"status": "success", "call_id": call_id}
    except Exception as e:
        print(f"❌ Webhook processing error: {str(e)}")
        return {"status": "error", "message": str(e)}

@app.get("/api/dashboard-data")
async def get_dashboard_data(
    company_id: Optional[int] = None,
    agent_id: Optional[int] = None,
    db: Session = Depends(get_db)
):
    """
    Returns company-level dashboard stats:
    1. used_minutes - total call minutes consumed by this company
    2. remaining_minutes - balance left (from Company table)
    3. total_minutes - used + remaining
    4. usage_this_month - minutes used this calendar month
    5. active_calls - calls currently in progress (status = 'initiated' or 'in-progress')
    6. total_calls - total number of calls ever
    7. weekly_usage - day-by-day minutes used over the last 7 days
    8. total_active_minutes - cumulative duration in seconds (as minutes) of all ended calls
    """
    from datetime import date, timedelta
    from sqlalchemy import func

    # --- Resolve the company ---
    company = None
    agent = None

    if company_id:
        company = db.query(Company).filter(Company.id == company_id).first()
        if not company:
            raise HTTPException(status_code=404, detail="Company not found")
        # Get all agents linked to this company
        agents = db.query(WebAgent).filter(WebAgent.company_id == company_id).all()
        agent_ids = [a.id for a in agents]
    elif agent_id:
        agent = db.query(WebAgent).filter(WebAgent.id == agent_id).first()
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        # Find the company by slug or company_id
        if agent.company_id:
            company = db.query(Company).filter(Company.id == agent.company_id).first()
        if not company:
            company = db.query(Company).filter(Company.company_slug == agent.company_slug).first()
        agent_ids = [agent.id]
    else:
        raise HTTPException(status_code=400, detail="Provide either company_id or agent_id")

    if not agent_ids:
        # Company exists but has no agents yet — return zeroes
        return {
            "company_id": company.id if company else None,
            "company_name": company.company_name if company else None,
            "used_minutes": 0,
            "remaining_minutes": company.balance_minutes if company else 0,
            "total_minutes": company.balance_minutes if company else 0,
            "usage_this_month": 0,
            "active_calls": 0,
            "total_calls": 0,
            "weekly_usage": [],
            "total_active_minutes": 0,
        }

    # --- All call records for these agents ---
    # Filter by each agent's own creation date to guard against recycled auto-increment IDs
    # inheriting call_records that belonged to a previously deleted agent with the same ID.
    agent_map = {a.id: a.created_at for a in (agents if company_id else [agent])}
    all_calls = []
    for aid, agent_created_at in agent_map.items():
        agent_calls = db.query(CallRecord).filter(
            CallRecord.agent_id == aid,
            CallRecord.created_at >= agent_created_at
        ).all()
        all_calls.extend(agent_calls)

    # 1. used_minutes — sum of duration (seconds) / 60, rounded up
    completed_calls = [c for c in all_calls if c.duration is not None]
    total_seconds_used = sum(c.duration for c in completed_calls)
    used_minutes = total_seconds_used / 60.0

    # 2. remaining_minutes — from Company balance
    remaining_minutes = company.balance_minutes if company else 0

    # 3. total_minutes
    total_minutes = used_minutes + remaining_minutes

    # 4. usage_this_month
    now = datetime.utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    monthly_calls = [c for c in completed_calls if c.created_at and c.created_at >= month_start]
    usage_this_month = sum(c.duration for c in monthly_calls) / 60.0

    # 5. active_calls — currently in-progress
    active_statuses = {"initiated", "in-progress", "in_progress", "active", "ringing"}
    active_calls = len([c for c in all_calls if c.status and c.status.lower() in active_statuses])

    # 6. total_calls
    total_calls = len(all_calls)

    # 7. weekly_usage — day-wise for last 7 days (including today)
    weekly_usage = []
    for days_ago in range(6, -1, -1):
        day = (now - timedelta(days=days_ago)).date()
        day_start = datetime.combine(day, datetime.min.time())
        day_end = datetime.combine(day, datetime.max.time())
        day_calls = [
            c for c in completed_calls
            if c.created_at and day_start <= c.created_at <= day_end
        ]
        day_minutes = sum(c.duration for c in day_calls) / 60.0
        weekly_usage.append({
            "date": day.isoformat(),
            "day": day.strftime("%A"),  # e.g. "Monday"
            "minutes": day_minutes,
            "call_count": len(day_calls)
        })

    # 8. total_active_minutes — sum of all completed call durations in minutes
    total_active_minutes = used_minutes

    return {
        "company_id": company.id if company else None,
        "company_name": company.company_name if company else (agent.company_name if agent else None),
        "agents": len(agent_ids),
        "used_minutes": used_minutes,
        "remaining_minutes": remaining_minutes,
        "total_minutes": total_minutes,
        "usage_this_month": usage_this_month,
        "active_calls": active_calls,
        "total_calls": total_calls,
        "weekly_usage": weekly_usage,
        "total_active_minutes": total_active_minutes,
    }

@app.get("/api/admin/deleted-agents")
async def list_deleted_agents(
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db)
):
    """
    Admin-only: Returns audit log of all deleted agents.
    Useful for tracking total agents ever created and reviewing deleted agent history.
    """
    total = db.query(DeletedAgent).count()
    records = (
        db.query(DeletedAgent)
        .order_by(DeletedAgent.deleted_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "total_deleted": total,
        "total_agents_ever_created": db.query(WebAgent).count() + total,
        "results": [
            {
                "audit_id": r.id,
                "original_agent_id": r.original_agent_id,
                "agent_name": r.agent_name,
                "company_name": r.company_name,
                "company_id": r.company_id,
                "company_slug": r.company_slug,
                "ultravox_agent_id": r.ultravox_agent_id,
                "was_active": r.is_active,
                "agent_created_at": r.created_at.isoformat() if r.created_at else None,
                "deleted_at": r.deleted_at.isoformat() if r.deleted_at else None,
            }
            for r in records
        ]
    }

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "agent-management",
        "twilio_configured": twilio_client is not None
    }

@app.post("/api/utils/verify-time")
async def verify_time(request: VerifyTimeRequest):
    """
    Verifies if a given date and time is in the past or future relative to London time.
    """
    try:
        # 1. SET LONDON TIMEZONE
        london_tz = pytz.timezone('Europe/London')
        now_london = datetime.now(london_tz)
        
        # 2. PARSE THE INPUT STRING
        # We use dateparser for natural language Parsing
        dt = dateparser.parse(request.dateTimeString, settings={
            'RELATIVE_BASE': now_london.replace(tzinfo=None), # Base relative parsing on current London time
            'PREFER_DATES_FROM': 'future'
        })
        
        if not dt:
            # Fallback to dateutil parser
            try:
                dt = parser.parse(request.dateTimeString)
            except:
                raise HTTPException(status_code=400, detail="Could not parse date and time string")

        # 3. ENSURE TIMEZONE AWARENESS (assume London if missing or in naive format)
        if dt.tzinfo is None:
            dt = london_tz.localize(dt)
        else:
            dt = dt.astimezone(london_tz)

        # 4. COMPARE
        is_past = dt < now_london
        
        # 5. RETURN RESULT
        return {
            "requested_time": dt.isoformat(),
            "current_london_time": now_london.isoformat(),
            "status": "past" if is_past else "future",
            "is_past": is_past,
            "message": f"The time {request.dateTimeString} is in the {'past' if is_past else 'future'}."
        }
    except Exception as e:
        print(f"Error in verify_time: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/utils/validate-capacity")
async def validate_capacity(request: ValidateCapacityRequest, db: Session = Depends(get_db)):
    """
    Validates whether the number of passengers and luggage items fits the selected vehicle.
    Returns valid=True if they fit, or valid=False with a recommended alternative vehicle.
    """
    try:
        # Normalise vehicle name for case-insensitive lookup
        vehicle = db.query(VehicleCapacity).filter(
            VehicleCapacity.vehicle_name.ilike(request.vehicleName.strip())
        ).first()

        if not vehicle:
            # Try partial match
            vehicle = db.query(VehicleCapacity).filter(
                VehicleCapacity.vehicle_name.ilike(f"%{request.vehicleName.strip()}%")
            ).first()

        if not vehicle:
            available = db.query(VehicleCapacity).order_by(VehicleCapacity.passenger_capacity).all()
            return {
                "valid": False,
                "reason": f"Vehicle '{request.vehicleName}' not found.",
                "available_vehicles": [
                    {"vehicleName": v.vehicle_name, "capacity": v.capacity_description}
                    for v in available
                ]
            }

        passengers_ok = request.passengers <= vehicle.passenger_capacity
        luggage_ok = (request.luggage or 0) <= vehicle.luggage_capacity
        valid = passengers_ok and luggage_ok

        result = {
            "valid": valid,
            "vehicleName": vehicle.vehicle_name,
            "requestedPassengers": request.passengers,
            "requestedLuggage": request.luggage or 0,
            "vehicleCapacity": vehicle.capacity_description,
        }

        if not valid:
            reasons = []
            if not passengers_ok:
                reasons.append(
                    f"too many passengers ({request.passengers} requested, max {vehicle.passenger_capacity})"
                )
            if not luggage_ok:
                reasons.append(
                    f"too much luggage ({request.luggage} items, max {vehicle.luggage_capacity} cases)"
                )
            result["reason"] = "Does not fit: " + ", ".join(reasons)

            # Recommend the smallest vehicle that fits
            recommended = db.query(VehicleCapacity).filter(
                VehicleCapacity.passenger_capacity >= request.passengers,
                VehicleCapacity.luggage_capacity >= (request.luggage or 0)
            ).order_by(VehicleCapacity.passenger_capacity).first()

            if recommended:
                result["recommendedVehicle"] = recommended.vehicle_name
                result["recommendedCapacity"] = recommended.capacity_description
                result["message"] = (
                    f"A '{vehicle.vehicle_name}' cannot accommodate your party. "
                    f"We recommend a '{recommended.vehicle_name}' "
                    f"({recommended.capacity_description})."
                )
            else:
                result["message"] = (
                    f"No vehicle in our fleet can accommodate {request.passengers} passengers "
                    f"and {request.luggage or 0} luggage items."
                )
        else:
            result["message"] = (
                f"A '{vehicle.vehicle_name}' can accommodate your party of {request.passengers} "
                f"with {request.luggage or 0} luggage item(s)."
            )

        return result

    except Exception as e:
        print(f"Error in validate_capacity: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── Web Interface Endpoints (merged from nettech-webcall-ai-agent) ──────────

class PinVerifyRequest(BaseModel):
    pin: int

@app.post("/auth/{company_slug}/verify-pin")
async def verify_agent_pin(company_slug: str, request: PinVerifyRequest, db: Session = Depends(get_db)):
    """Verify the PIN for an agent and return a 5-minute session token"""
    agent = db.query(WebAgent).filter(WebAgent.company_slug == company_slug).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    expected_pin = agent.web_call_pin
    if not expected_pin:
        raise HTTPException(status_code=400, detail="This agent does not require a PIN")

    if request.pin != expected_pin:
        raise HTTPException(status_code=401, detail="Incorrect PIN")

    expiration = datetime.utcnow() + timedelta(minutes=SESSION_LIFETIME_MINUTES)
    token_payload = {"sub": company_slug, "exp": expiration}
    token = jwt.encode(token_payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return {"session_token": token, "expires_in": SESSION_LIFETIME_MINUTES * 60}


@app.get("/{company_slug}", response_class=HTMLResponse)
async def agent_interface(company_slug: str, request: Request, session_token: str = None, db: Session = Depends(get_db)):
    """Dynamic agent interface based on company slug with PIN protection support"""

    # Skip known static/API routes
    if company_slug in ["dashboard", "api", "cromwell", "static", "health", "docs", "openapi.json", "auth", "favicon.ico"]:
        raise HTTPException(status_code=404, detail="Not Found")

    agent = db.query(WebAgent).filter(WebAgent.company_slug == company_slug).first()
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{company_slug}' not found")

    if not agent.is_active:
        raise HTTPException(status_code=404, detail=f"Agent '{company_slug}' is not active")

    # PIN protection check
    expected_pin = agent.web_call_pin
    if expected_pin:
        is_authenticated = False
        if session_token:
            try:
                payload = jwt.decode(session_token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
                if payload.get("sub") == company_slug:
                    is_authenticated = True
            except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
                pass

        if not is_authenticated:
            with open("static/pin_entry.html", "r") as f:
                return HTMLResponse(content=f.read())

    # Get merged agent prompt
    agent_prompt = merge_prompt_with_customizations(agent)

    with open("static/index.html", "r") as f:
        html_content = f.read()

    if expected_pin:
        frontend_token = session_token or ""
    else:
        frontend_token = agent.api_Token or ""

    # Inject agent branding
    html_content = html_content.replace("Cromwell Cars", agent.company_name or "AI Assistant")
    html_content = html_content.replace("AI-Powered Job Dispatcher", f"{agent.agent_name} - AI Assistant")

    # Pre-compute escaped prompt (backslash not allowed inside f-string in Python 3.12+)
    escaped_prompt = agent_prompt.replace('`', '\\`')
    agent_config_script = f"""
    <script>
        window.agentConfig = {{
            agentId: {agent.id},
            agentName: "{agent.agent_name}",
            companyName: "{agent.company_name or ''}",
            companySlug: "{agent.company_slug}",
            greetingMessage: "{agent.greeting_message or ''}",
            apiToken: "{frontend_token}",
            systemPrompt: `{escaped_prompt}`
        }};
    </script>
    """
    html_content = html_content.replace("</body>", f"{agent_config_script}</body>")
    return HTMLResponse(content=html_content)


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5005))
    host = os.getenv("HOST", "0.0.0.0")
    
    print(f"🚀 Starting Agent Management Service on {host}:{port}")
    print(f"📱 Admin Dashboard: http://{host}:{port}")
    print(f"🔧 API docs: http://{host}:{port}/docs")
    
    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=True,
        log_level="info"
    )
