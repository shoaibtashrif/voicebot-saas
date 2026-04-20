from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Dict, Any, Optional, List
import httpx
import os
import jwt
from datetime import datetime
from dotenv import load_dotenv
from config.agent_config import ULTRAVOX_WEB_CALL_CONFIG

# Ensure environment variables are loaded
load_dotenv()

JWT_SECRET = os.getenv("JWT_SECRET", "cabex-web-call-secret-key")
JWT_ALGORITHM = "HS256"

router = APIRouter()

# Pydantic models
class CallConfig(BaseModel):
    systemPrompt: str
    model: Optional[str] = "fixie-ai/ultravox"
    languageHint: Optional[str] = "en"
    selectedTools: Optional[List[Dict[str, Any]]] = []
    voice: Optional[str] = None
    temperature: Optional[float] = 0.3
    maxDuration: Optional[str] = None
    timeExceededMessage: Optional[str] = None

class UltravoxCallResponse(BaseModel):
    callId: str
    created: datetime
    ended: Optional[datetime] = None
    model: str
    systemPrompt: str
    temperature: float
    joinUrl: str
    cabeeCallId: Optional[str] = None

@router.post("/ultravox", response_model=UltravoxCallResponse)
async def create_ultravox_call(call_config: CallConfig):
    """Create a new Ultravox call with web-specific configuration"""
    
    ultravox_api_key = os.getenv("ULTRAVOX_API_KEY")
    print(f"🔑 API Key loaded: {ultravox_api_key[:10] if ultravox_api_key else 'None'}...")
    
    if not ultravox_api_key:
        raise HTTPException(status_code=500, detail="ULTRAVOX_API_KEY not configured")
    
    # Use the web-specific configuration
    config_data = ULTRAVOX_WEB_CALL_CONFIG.copy()
    
    # Override with any provided config
    if call_config.model:
        config_data["model"] = call_config.model
    if call_config.voice:
        config_data["voice"] = call_config.voice
    if call_config.temperature is not None:
        config_data["temperature"] = call_config.temperature
    if call_config.maxDuration:
        config_data["maxDuration"] = call_config.maxDuration
    if call_config.timeExceededMessage:
        config_data["timeExceededMessage"] = call_config.timeExceededMessage
    
    # 🆔 GENERATE DYNAMIC CABEE CALL ID
    import uuid
    cabee_call_id = f"C-{uuid.uuid4().hex[:8].upper()}"
    print(f"🆔 Generated Dynamic Cabee Call ID: {cabee_call_id}")
    
    # 💉 INJECT CABEE CALL ID INTO TOOLS
    base_tools = call_config.selectedTools if call_config.selectedTools else config_data.get("selectedTools", [])
    final_tools = []
    if base_tools:
        for tool in base_tools:
            try:
                if "temporaryTool" in tool:
                    temp_tool = tool["temporaryTool"]
                    if "dynamicParameters" not in temp_tool:
                        temp_tool["dynamicParameters"] = []
                    temp_tool["dynamicParameters"].append({
                        "name": "cabee_call_id",
                        "location": "PARAMETER_LOCATION_QUERY",
                        "schema": {
                            "type": "string",
                            "default": cabee_call_id,
                            "description": "Internal Session ID"
                        },
                        "required": True
                    })
                final_tools.append(tool)
            except Exception as e:
                final_tools.append(tool)
    
    config_data["selectedTools"] = final_tools
    print(f"🛠️ Final Selected Tools: {json.dumps(final_tools, indent=2)}")

    # Log the configuration being sent
    print(f"🤖 Creating Ultravox call with config:")
    print(f"   Model: {config_data.get('model')}")
    print(f"   Voice: {config_data.get('voice')}")
    print(f"   Temperature: {config_data.get('temperature')}")
    print(f"   Tools count: {len(config_data.get('selectedTools', []))}")
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.ultravox.ai/api/calls",
                headers={
                    "X-API-Key": ultravox_api_key,
                    "Content-Type": "application/json"
                },
                json=config_data,
                timeout=30.0
            )
            
            print(f"📡 Ultravox API Response Status: {response.status_code}")
            
            if response.status_code not in [200, 201]:
                error_text = response.text
                print(f"❌ Ultravox API Error: {response.status_code}")
                print(f"❌ Error Response: {error_text}")
                
                error_detail = f"Ultravox API error: {response.status_code}"
                try:
                    error_data = response.json()
                    error_detail += f" - {error_data}"
                except:
                    error_detail += f" - {error_text}"
                raise HTTPException(status_code=response.status_code, detail=error_detail)
            
            result = response.json()
            print(f"✅ Ultravox call created successfully: {result.get('callId')}")
            
            return UltravoxCallResponse(
                callId=result["callId"],
                created=datetime.fromisoformat(result["created"].replace("Z", "+00:00")),
                ended=datetime.fromisoformat(result["ended"].replace("Z", "+00:00")) if result.get("ended") else None,
                model=result["model"],
                systemPrompt=result["systemPrompt"],
                temperature=result["temperature"],
                joinUrl=result["joinUrl"],
                cabeeCallId=cabee_call_id
            )
            
    except httpx.RequestError as e:
        print(f"❌ HTTP Request Error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Request failed: {str(e)}")
    except Exception as e:
        print(f"❌ Unexpected Error: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")

@router.post("/ultravox/{company_slug}", response_model=UltravoxCallResponse)
async def create_agent_ultravox_call(
    company_slug: str, 
    call_config: CallConfig,
    request: Request
):
    """Create a new Ultravox call with agent-specific configuration, authenticated via JWT"""
    
    ultravox_api_key = os.getenv("ULTRAVOX_API_KEY")
    if not ultravox_api_key:
        raise HTTPException(status_code=500, detail="ULTRAVOX_API_KEY not configured")
    
    # Get agent configuration
    agent_management_url = os.getenv("AGENT_MANAGEMENT_URL", "http://localhost:5005")
    try:
        async with httpx.AsyncClient() as client:
            # Get agent by slug
            agent_response = await client.get(f"{agent_management_url}/api/agents/slug/{company_slug}")
            if agent_response.status_code != 200:
                raise HTTPException(status_code=404, detail=f"Agent '{company_slug}' not found")
            
            agent = agent_response.json()
            if not agent.get("is_active", False):
                raise HTTPException(status_code=404, detail=f"Agent '{company_slug}' is not active")
            
            # --- Authentication Logic ---
            # Extract Bearer token from the request
            auth_header = request.headers.get("Authorization")
            if not auth_header or not auth_header.startswith("Bearer "):
                print(f"❌ Missing or invalid Authorization header for web call ({company_slug})")
                raise HTTPException(status_code=401, detail="Unauthorized: Missing token")
            
            provided_token = auth_header.split(" ")[1]
            master_token = agent.get("api_Token")
            pin_active = agent.get("web_call_pin") is not None
            
            is_authenticated = False
            
            # 1. Try matching the master token (used by external apps/dashboards)
            if master_token and provided_token == master_token:
                is_authenticated = True
                print(f"✅ Web call authenticated via Master API Token for agent '{company_slug}'")
                
            # 2. Try matching the 5-minute Session Token (used by public web links)
            elif pin_active:
                try:
                    payload = jwt.decode(provided_token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
                    if payload.get("sub") == company_slug:
                        is_authenticated = True
                        print(f"✅ Web call authenticated via Session Token for agent '{company_slug}'")
                except Exception as e:
                    pass
            
            # 3. Backward compatibility logic: if NO token is set and NO pin is set, allow it
            elif not master_token and not pin_active:
                is_authenticated = True
                print(f"⚠️ Agent '{company_slug}' has no Token/PIN set. Allowing public call.")
                
            if not is_authenticated:
                print(f"❌ Invalid token provided for web call ({company_slug})")
                raise HTTPException(status_code=401, detail="Unauthorized: Invalid or expired token")
            # -------------------------------------
            
            # Get agent prompt
            prompt_response = await client.get(f"{agent_management_url}/api/agents/{agent['id']}/prompt")
            if prompt_response.status_code != 200:
                raise HTTPException(status_code=500, detail="Unable to load agent configuration")
            
            agent_prompt = prompt_response.json()["prompt"]
            
            # Get agent-specific tools (including knowledge base tool if available)
            agent_tools = []
            try:
                tools_response = await client.get(f"{agent_management_url}/api/agents/{agent['id']}/tools")
                if tools_response.status_code == 200:
                    agent_tools = tools_response.json()["tools"]
            except Exception as e:
                print(f"Warning: Could not load agent tools: {e}")
                # Use default tools if agent tools fail to load
            # Fetch company balance for enforcement
            balance_response = await client.get(f"{agent_management_url}/api/companies/{agent['company_slug']}/balance")
            if balance_response.status_code == 200:
                balance_data = balance_response.json()
            else:
                print(f"⚠️ Warning: Could not fetch balance for {agent['company_slug']}")
                balance_data = {"balance_minutes": 0}

            # 🛑 REJECT IF BALANCE IS ZERO
            if balance_data.get("balance_minutes", 0) <= 0:
                print(f"❌ REJECTED: Company {agent['company_slug']} has zero balance.")
                raise HTTPException(status_code=402, detail="Insufficient balance. Please top up your account to make calls.")

    except HTTPException as e:
        raise e
    except httpx.RequestError as e:
        raise HTTPException(status_code=500, detail=f"Unable to connect to agent management service: {str(e)}")
    
    # 🆔 GENERATE DYNAMIC CABEE CALL ID
    import uuid
    cabee_call_id = f"C-{uuid.uuid4().hex[:8].upper()}"
    print(f"🆔 Generated Dynamic Cabee Call ID for Web Call: {cabee_call_id}")

    # Calculate max duration based on balance
    balance_minutes = balance_data.get("balance_minutes", 0)
    time_limit_seconds = int(balance_minutes * 60)
    
    # Use the base configuration but with agent-specific prompt and tools
    config_data = ULTRAVOX_WEB_CALL_CONFIG.copy()
    
    # CRITICAL: Always use agent's prompt, never allow override from frontend
    config_data["systemPrompt"] = agent_prompt
    
    # Determine base tools to use
    base_tools = agent_tools if agent_tools else config_data.get("selectedTools", [])
    
    # 💉 INJECT CABEE CALL ID INTO TOOLS
    final_tools = []
    if base_tools:
        for tool in base_tools:
            try:
                if "temporaryTool" in tool:
                    temp_tool = tool["temporaryTool"]
                    if "dynamicParameters" not in temp_tool:
                        temp_tool["dynamicParameters"] = []
                    
                    # Add cabee_call_id parameter to dynamicParameters
                    # We set it as a required query parameter with a default value
                    temp_tool["dynamicParameters"].append({
                        "name": "cabee_call_id",
                        "location": "PARAMETER_LOCATION_QUERY",
                        "schema": {
                            "type": "string",
                            "default": cabee_call_id,
                            "description": "Internal Session ID"
                        },
                        "required": True
                    })
                    print(f"💉 Injected cabee_call_id into dynamicParameters for: {temp_tool.get('modelToolName')}")
                final_tools.append(tool)
            except Exception as tool_err:
                print(f"⚠️ Warning: Could not inject Cabee ID into tool: {tool_err}")
                final_tools.append(tool)
                
    config_data["selectedTools"] = final_tools
    print(f"🛠️ Final Selected Tools for Ultravox: {json.dumps(final_tools, indent=2)}")
    # SET ENFORCEMENT PARAMETERS
    config_data["maxDuration"] = f"{time_limit_seconds}s"
    config_data["timeExceededMessage"] = "I'm sorry, but your company's balance has run out. Please contact your administrator to add funds. Goodbye!"

    print(f"🤖 Creating Ultravox call for agent '{agent['agent_name']}' ({company_slug})")
    print(f"   Time Limit: {time_limit_seconds}s")
    
    # Override with any provided config (BUT NOT systemPrompt or enforcement params)
    if call_config.model:
        config_data["model"] = call_config.model
    if agent.get("voice"):
        config_data["voice"] = agent["voice"]
    elif call_config.voice:
        config_data["voice"] = call_config.voice
    if call_config.temperature is not None:
        config_data["temperature"] = call_config.temperature
    # if call_config.maxDuration:
    #     config_data["maxDuration"] = call_config.maxDuration
    # if call_config.timeExceededMessage:
    #     config_data["timeExceededMessage"] = call_config.timeExceededMessage
    
    # Final verification - ensure agent prompt is still set (not overridden)
    if config_data["systemPrompt"] != agent_prompt:
        print(f"⚠️ WARNING: systemPrompt was overridden! Resetting to agent prompt.")
        config_data["systemPrompt"] = agent_prompt
    
    print(f"   Final config systemPrompt length: {len(config_data['systemPrompt'])} characters")
    print(f"   Final config systemPrompt preview: {config_data['systemPrompt'][:200]}...")
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.ultravox.ai/api/calls",
                headers={
                    "X-API-Key": ultravox_api_key,
                    "Content-Type": "application/json"
                },
                json=config_data,
                timeout=30.0
            )
            
            if response.status_code not in [200, 201]:
                error_text = response.text
                print(f"❌ Ultravox API Error: {response.status_code}")
                print(f"❌ Error Response: {error_text}")
                
                error_detail = f"Ultravox API error: {response.status_code}"
                try:
                    error_data = response.json()
                    error_detail += f" - {error_data}"
                except:
                    error_detail += f" - {error_text}"
                raise HTTPException(status_code=response.status_code, detail=error_detail)
            
            result = response.json()
            call_id = result.get('callId', '')
            print(f"✅ Ultravox call created successfully for agent '{agent['agent_name']}': {call_id}")
            # This lets bookCab look up the callId without needing HTTP headers or AI parameter passing
            try:
                from registry import register_call
                register_call(call_id, cabee_call_id=cabee_call_id)
                print(f"📌 Registered callId {call_id} with Cabee ID {cabee_call_id} in internal registry")
            except Exception as e:
                print(f"⚠️ Warning: Could not register callId internally: {e}")
            
            # Also register in legacy dispatcher just in case
            try:
                cromwell_dispatcher_url = os.getenv("CROMWELL_DISPATCHER_URL", "http://localhost:3000")
                register_response = await client.post(
                    f"{cromwell_dispatcher_url}/cromwell/register-call",
                    json={"call_id": call_id, "cabee_call_id": cabee_call_id},
                    timeout=5.0
                )
            except Exception as e:
                pass
 
            
            # Log the call to agent management service
            try:
                await client.post(
                    f"{agent_management_url}/api/agents/{agent['id']}/log-call",
                    json={
                        "call_id": result["callId"],
                        "cabee_call_id": cabee_call_id,
                        "status": "initiated",
                        "caller_number": "Web Call"
                    }
                )
                print(f"📝 Web call logged to management service with Cabee ID: {cabee_call_id}")
            except Exception as e:
                print(f"⚠️ Warning: Could not log web call: {e}")
            
            return UltravoxCallResponse(
                callId=result["callId"],
                created=datetime.fromisoformat(result["created"].replace("Z", "+00:00")),
                ended=datetime.fromisoformat(result["ended"].replace("Z", "+00:00")) if result.get("ended") else None,
                model=result["model"],
                systemPrompt=result["systemPrompt"],
                temperature=result["temperature"],
                joinUrl=result["joinUrl"],
                cabeeCallId=cabee_call_id
            )
            
    except httpx.RequestError as e:
        print(f"❌ HTTP Request Error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Request failed: {str(e)}")
    except Exception as e:
        print(f"❌ Unexpected Error: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")

@router.get("/config")
async def get_web_config():
    """Get the web-specific agent configuration"""
    return {
        "title": "Cromwell Cars Web Dispatcher",
        "overview": "Independent web interface for Cromwell Cars AI Dispatcher. This service has its own agent configuration separate from the Twilio phone service.",
        "callConfig": {
            "systemPrompt": ULTRAVOX_WEB_CALL_CONFIG["systemPrompt"],
            "model": ULTRAVOX_WEB_CALL_CONFIG["model"],
            "languageHint": "en",
            "selectedTools": ULTRAVOX_WEB_CALL_CONFIG["selectedTools"],
            "voice": ULTRAVOX_WEB_CALL_CONFIG["voice"],
            "temperature": ULTRAVOX_WEB_CALL_CONFIG["temperature"]
        }
    }