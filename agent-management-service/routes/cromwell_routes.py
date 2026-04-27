from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import List, Optional, Dict, Any, Union
import httpx
import os
import json
from datetime import datetime
import re

router = APIRouter()

@router.post("/register-call")
async def register_call_endpoint(request: Request):
    """Register a call in the internal python registry so bookCab can locate the call ID."""
    try:
        data = await request.json()
        call_id = data.get("call_id")
        cabee_call_id = data.get("cabee_call_id")
        
        if not call_id:
            raise HTTPException(status_code=400, detail="call_id is required")
        
        try:
            from registry import register_call
            register_call(call_id, cabee_call_id=cabee_call_id)
            if cabee_call_id:
                print(f"📌 Registered callId {call_id} with Cabee ID {cabee_call_id} via API")
            else:
                print(f"📌 Registered callId via API: {call_id}")
        except ImportError:
            print("⚠️ Warning: Could not import internal registry for API call")
            pass
            
        return {"status": "ok", "call_id": call_id, "cabee_call_id": cabee_call_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Cromwell Cars API Configuration
CROMWELL_API_BASE = 'https://online.ontimechauffeurs.co.uk/api'
CABEE_API_BASE = 'https://capi.cabee-est.com/api'
# CABEE_API_BASE = 'https://api.nettechltd.com/api'


# Pydantic models
class AddressValidationRequest(BaseModel):
    address_lines: Any  # Accept any type to handle AI mistakes
    postcode: Optional[str] = None
    building: Optional[str] = None

class PricingRequest(BaseModel):
    sourceAddress: str
    destinationAddress: str
    companyId: Union[str, int]
    vehicleTypeId: Optional[Union[str, int]] = "0"
    stops: Optional[Any] = None

class VehicleTypeRequest(BaseModel):
    companySlug: str

class BookingRequest(BaseModel):
    operation: Optional[str] = None
    companyId: Optional[Union[str, int]] = "99"
    companySlug: Optional[str] = None
    call_sid: Optional[str] = None
    jobNO: Optional[str] = None
    Phone: Optional[str] = None
    passengerName: Optional[str] = None
    passengerEmail: Optional[str] = None
    passengerPhone: Optional[str] = None
    passengerMobile: Optional[str] = None
    origin: Optional[str] = None
    destination: Optional[str] = None
    date: Optional[str] = None
    vehicleTypeId: Optional[str] = None
    customerPrice: Optional[str] = None
    passengers: Optional[str] = None
    bags: Optional[str] = None
    note: Optional[str] = None
    title: Optional[str] = None
    flight_Number: Optional[str] = None
    flight_Info: Optional[str] = None
    house_number: Optional[str] = None
    flat_info: Optional[str] = None
    stops: Optional[Any] = None
    driverPrice: Optional[str] = None
    eCommission: Optional[str] = None
    totalJobPrice: Optional[str] = None
    companyPrice: Optional[str] = None
    bookingFee: Optional[str] = None

def _extract_postcode(text: str) -> Optional[str]:
    if not text:
        return None
    match = re.search(r"\b([A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2})\b", text.upper())
    if not match:
        return None
    pc = match.group(1).strip().upper()
    if " " not in pc and len(pc) > 3:
        pc = f"{pc[:-3]} {pc[-3:]}"
    return pc

def _normalize_stops(stops: Any) -> List[str]:
    if not stops:
        return []
    if isinstance(stops, list):
        return [str(s).strip() for s in stops if str(s).strip()]
    if isinstance(stops, str):
        raw = stops.strip()
        if not raw:
            return []
        # Handle "'W14','W10'" style
        split_items = [x.strip().strip("'").strip('"') for x in raw.split(",")]
        return [x for x in split_items if x]
    return [str(stops)]

async def _save_job_record(
    call_sid: str,
    booking_result: Dict[str, Any],
    vehicle_type: Optional[str],
    cabee_call_id: Optional[str] = None
) -> None:
    base_url = os.getenv("TOOLS_BASE_URL", "https://agent.cabex.co.uk").rstrip("/")
    fallback_url = "http://127.0.0.1:5005"
    payload = {
        "call_sid": call_sid or "unknown",
        "cabee_call_id": cabee_call_id or "unknown",
        "jobNO": str(booking_result.get("jobNO") or ""),
        "bookingId": str(booking_result.get("id") or ""),
        "passengerName": booking_result.get("passengerName"),
        "customerPrice": str(booking_result.get("customerPrice") or ""),
        "date": booking_result.get("date"),
        "origin": booking_result.get("origin"),
        "destination": booking_result.get("destination"),
        "vehicleType": str(vehicle_type or "")
    }

    async with httpx.AsyncClient() as client:
        for root in [base_url, fallback_url]:
            try:
                resp = await client.post(
                    f"{root}/api/jobs",
                    json=payload,
                    timeout=10.0
                )
                if resp.is_success:
                    return
            except Exception:
                continue

async def _enrich_address_input(address_lines: List[str], postcode: Optional[str]) -> Dict[str, Any]:
    if postcode or not address_lines:
        return {"address_lines": address_lines, "postcode": postcode}

    first_line = str(address_lines[0]).strip()
    if not first_line:
        return {"address_lines": address_lines, "postcode": postcode}

    known_landmarks = {
        "natural history museum": "SW7 5BD",
        "heathrow airport terminal 5": "TW6 2GA",
        "buckingham palace": "SW1A 1AA"
    }
    mapped = known_landmarks.get(first_line.lower())
    if mapped:
        return {"address_lines": address_lines, "postcode": mapped}

    # Fallback: infer postcode for famous places via Nominatim when user gives only a landmark.
    try:
        async with httpx.AsyncClient() as client:
            geo = await client.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": first_line, "format": "jsonv2", "addressdetails": 1, "limit": 1},
                headers={"User-Agent": "voicebot-agent-management/1.0"},
                timeout=8.0
            )
            if geo.is_success:
                data = geo.json()
                if data:
                    addr = data[0].get("address", {})
                    inferred = _extract_postcode(addr.get("postcode", "")) or _extract_postcode(data[0].get("display_name", ""))
                    if inferred:
                        display_name = data[0].get("display_name", "")
                        second_line = display_name.split(",")[1].strip() if "," in display_name else None
                        enriched_lines = list(address_lines)
                        if second_line and second_line.lower() not in first_line.lower():
                            enriched_lines.append(second_line)
                        return {"address_lines": enriched_lines, "postcode": inferred}
    except Exception:
        pass

    return {"address_lines": address_lines, "postcode": postcode}

def get_jwt_token():
    """Get JWT token from environment"""
    token = os.getenv("CABEE_JWT_TOKEN")
    if not token:
        raise HTTPException(status_code=500, detail="CABEE_JWT_TOKEN not configured")
    return token

async def resolve_cabee_token(company_id: Optional[str] = None, company_slug: Optional[str] = None) -> str:
    """Resolve Cabee JWT from env first, then from agent records."""
    env_token = os.getenv("CABEE_JWT_TOKEN")
    if env_token:
        return env_token

    configured_base_url = os.getenv("TOOLS_BASE_URL", "https://agent.cabex.co.uk").rstrip("/")
    fallback_base_url = "http://127.0.0.1:5005"
    base_urls = [configured_base_url, fallback_base_url]

    async with httpx.AsyncClient() as client:
        # Slug lookup is direct and preferred when available.
        if company_slug:
            for base_url in base_urls:
                try:
                    response = await client.get(
                        f"{base_url}/api/agents/slug/{company_slug}",
                        timeout=15.0
                    )
                    if response.is_success:
                        agent = response.json()
                        token = agent.get("api_Token")
                        if token:
                            return token
                except Exception:
                    continue

        # Fallback to company ID lookup via agents list.
        if company_id:
            for base_url in base_urls:
                try:
                    response = await client.get(f"{base_url}/api/agents", timeout=15.0)
                    if not response.is_success:
                        continue
                    agents = response.json()
                    wanted_id = str(company_id)
                    for agent in agents:
                        if str(agent.get("company_id")) == wanted_id and agent.get("api_Token"):
                            return agent["api_Token"]
                except Exception:
                    continue

    raise HTTPException(status_code=500, detail="Cabee JWT token not available (env or agent api_Token)")

def generate_call_id():
    """Generate a unique call ID for logging"""
    return f"call_{int(datetime.now().timestamp())}_{os.urandom(4).hex()}"

@router.post("/validateAddress")
async def validate_address(request: AddressValidationRequest):
    """Validate UK addresses using Cromwell Cars API with auto-retry on parameter errors"""
    
    call_id = generate_call_id()
    timestamp = datetime.now().isoformat()
    
    try:
        print(f"\n🔍 ===== ADDRESS VALIDATION TOOL CALLED =====")
        print(f"📅 Timestamp: {timestamp}")
        print(f"🆔 Call ID: {call_id}")
        print(f"📥 INCOMING REQUEST: {request.dict()}")
        
        # Parse address_lines if it's a string (auto-correct common AI mistakes)
        address_lines = request.address_lines
        if isinstance(address_lines, str):
            try:
                # Try to parse as JSON first
                address_lines = json.loads(address_lines)
                print(f"🔧 PARSED JSON STRING TO ARRAY: {request.address_lines} → {address_lines}")
            except json.JSONDecodeError:
                # If not JSON, treat as single address line
                address_lines = [address_lines]
                print(f"🔧 CONVERTED STRING TO ARRAY: {request.address_lines} → {address_lines}")
        
        # Ensure it's a proper list
        if not isinstance(address_lines, list):
            address_lines = [str(address_lines)]
            print(f"🔧 FORCED TO ARRAY: {address_lines}")
        
        enriched = await _enrich_address_input(address_lines, request.postcode)
        landmark_fallback_candidate = None
        first_line = str(address_lines[0]).strip() if address_lines else ""
        known_landmarks = {
            "natural history museum": "SW7 5BD",
            "heathrow airport terminal 5": "TW6 2GA",
            "buckingham palace": "SW1A 1AA"
        }
        if first_line.lower() in known_landmarks:
            pc = known_landmarks[first_line.lower()]
            landmark_fallback_candidate = {
                "formatted": f"{first_line}, London, {pc}",
                "postcode": pc,
                "source": "local_landmark_fallback"
            }

        request_payload = {
            "addressLines": enriched["address_lines"],
            "postcode": enriched["postcode"] or "",
            "building": getattr(request, "building", "") or ""
        }
        
        print(f"🌐 CALLING CABEE ADDRESS API:")
        print(f"   URL: https://capi.cabee-est.com/api/Job/validate")
        print(f"   Payload: {json.dumps(request_payload, indent=2)}")
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://capi.cabee-est.com/api/Job/validate",
                headers={"Content-Type": "application/json"},
                json=request_payload,
                timeout=30.0
            )
            
            print(f"📡 API Response Status: {response.status_code}")
            
            # Handle 422 validation errors by auto-correcting and retrying
            if response.status_code == 422:
                error_text = response.text
                print(f"⚠️ VALIDATION ERROR (422) - AUTO-CORRECTING: {error_text}")
                
                # Try alternative format - flatten address lines if they contain arrays
                if "list_type" in error_text and "address_lines" in error_text:
                    # Extract address components and flatten
                    flattened_lines = []
                    for line in address_lines:
                        if isinstance(line, list):
                            flattened_lines.extend([str(item) for item in line])
                        else:
                            flattened_lines.append(str(line))
                    
                    corrected_payload = {
                        "addressLines": flattened_lines,
                        "postcode": request.postcode or "",
                        "building": getattr(request, "building", "") or ""
                    }
                    
                    print(f"🔄 RETRYING WITH CORRECTED PAYLOAD: {json.dumps(corrected_payload, indent=2)}")
                    
                    # Retry with corrected format
                    retry_response = await client.post(
                        "https://capi.cabee-est.com/api/Job/validate",
                        headers={"Content-Type": "application/json"},
                        json=corrected_payload,
                        timeout=30.0
                    )
                    
                    if retry_response.is_success:
                        result = retry_response.json()
                        print(f"✅ AUTO-CORRECTION SUCCESSFUL")
                        # Map response to candidates structure for backward compatibility
                        normalized = result.get("normalized_address")
                        if normalized and normalized.get("formatted"):
                            result["candidates"] = [{"formatted": normalized["formatted"]}]
                        else:
                            result["candidates"] = []
                        print(f"📤 API RESPONSE DATA: {json.dumps(result, indent=2)}")
                        return result
                    else:
                        print(f"❌ RETRY ALSO FAILED: {retry_response.status_code}")
                        response = retry_response  # Use retry response for final error handling
            
            if not response.is_success:
                error_text = response.text
                print(f"❌ API Error: {response.status_code} - {error_text}")
                
                # Only show user-friendly errors for non-recoverable issues
                if response.status_code == 404:
                    return {
                        "success": False,
                        "error": "Address not found",
                        "candidates": []
                    }
                else:
                    # For other errors, return a generic message
                    if landmark_fallback_candidate:
                        return {
                            "success": True,
                            "candidates": [landmark_fallback_candidate],
                            "source": "local_landmark_fallback"
                        }
                    return {
                        "success": False,
                        "error": "Unable to validate address at the moment",
                        "candidates": []
                    }
            
            result = response.json()
            # Map new API response format to expected candidates structure
            normalized = result.get("normalized_address")
            if normalized and normalized.get("formatted"):
                result["candidates"] = [{"formatted": normalized["formatted"]}]
            elif "candidates" not in result:
                result["candidates"] = []
                
            print(f"📤 API RESPONSE DATA: {json.dumps(result, indent=2)}")
            print(f"✅ ADDRESS VALIDATION SUCCESS")
            print(f"🔍 Found {len(result.get('candidates', []))} address candidates")
            
            if result.get('candidates') and len(result['candidates']) > 0:
                print(f"📍 TOP ADDRESS CANDIDATE:")
                print(f"   Formatted: {result['candidates'][0].get('formatted')}")
                print(f"   Postcode: {result['candidates'][0].get('postcode')}")
            
            print(f"🔍 ===== ADDRESS VALIDATION COMPLETE =====\n")
            return result
            
    except httpx.RequestError as e:
        print(f"❌ ===== ADDRESS VALIDATION ERROR =====")
        print(f"🆔 Call ID: {call_id}")
        print(f"💥 REQUEST ERROR: {str(e)}")
        print(f"❌ ===== ADDRESS VALIDATION ERROR END =====\n")
        
        # Return user-friendly error instead of HTTP exception
        return {
            "success": False,
            "error": "Network error during address validation",
            "candidates": []
        }
    except Exception as e:
        print(f"❌ ===== ADDRESS VALIDATION ERROR =====")
        print(f"🆔 Call ID: {call_id}")
        print(f"💥 UNEXPECTED ERROR: {str(e)}")
        print(f"❌ ===== ADDRESS VALIDATION ERROR END =====\n")
        
        # Return user-friendly error instead of HTTP exception
        return {
            "success": False,
            "error": "Unable to validate address",
            "candidates": []
        }

@router.post("/checkPricing")
async def check_pricing(request: PricingRequest):
    """Check pricing using real API"""
    
    call_id = generate_call_id()
    timestamp = datetime.now().isoformat()
    
    try:
        print(f"\n💰 ===== PRICING TOOL CALLED =====")
        print(f"📅 Timestamp: {timestamp}")
        print(f"🆔 Call ID: {call_id}")
        print(f"📥 INCOMING REQUEST: {request.dict()}")
        
        # Use provided companyId
        company_id = request.companyId
        vehicle_type_id = getattr(request, 'vehicleTypeId', 0) or 0

        api_url = f"{CABEE_API_BASE}/Tariff/external/CalculateTariffPriceForBotAviAddress"
        query_params = {
            "fromLocation": request.sourceAddress,
            "toLocation": request.destinationAddress,
            "companyId": company_id,
            "vehicleTypeId": vehicle_type_id
        }

        # Use the standard _normalize_stops helper to ensure uniform handling
        normalized_stops = _normalize_stops(request.stops)
        if normalized_stops:
            # Cabee throws 500 error if passing commas inside single quotes, so replace them with spaces first
            cleaned_stops = []
            for stop in normalized_stops:
                cleaned = str(stop).replace(',', ' ')
                # Replace multiple spaces with a single space
                import re
                cleaned = re.sub(' +', ' ', cleaned).strip()
                cleaned_stops.append(f"'{cleaned}'")
            
            # Format as 'address1','address2' as required by the Cabee API
            query_params["stops"] = ",".join(cleaned_stops)

        print(f"🌐 CALLING PRICING API:")
        print(f"   URL: {api_url}")
        print(f"   Params: {query_params}")
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                api_url,
                params=query_params,
                headers={"accept": "*/*"},
                timeout=30.0
            )
            
            print(f"📡 API Response Status: {response.status_code}")
            
            if not response.is_success:
                print(f"❌ API Error: {response.status_code}")
                error_text = response.text
                print(f"❌ Error Details: {error_text}")
                
                error_msg = "We are having problem getting the pricing at the moment. Please try again later."
                if response.status_code == 500 and "Nullable object" in error_text:
                    error_msg = "The address or postcode seems invalid. Please ensure postcodes have a space (e.g. 'SW1 1DA')."
                
                return {
                    "success": False,
                    "error": error_msg,
                    "status": "api_error"
                }
            
            result = response.json()
            print(f"📤 API RESPONSE DATA: {json.dumps(result, indent=2)}")
            
            print(f"💰 ===== PRICING TOOL COMPLETE =====\n")
            return result
            
    except httpx.RequestError as e:
        print(f"❌ ===== PRICING TOOL ERROR =====")
        print(f"🆔 Call ID: {call_id}")
        print(f"💥 REQUEST ERROR: {str(e)}")
        print(f"❌ ===== PRICING TOOL ERROR END =====\n")
        
        return {
            "success": False,
            "error": "We are having problem getting the pricing at the moment. Please try again later.",
            "status": "network_error"
        }
    except Exception as e:
        print(f"❌ ===== PRICING TOOL ERROR =====")
        print(f"🆔 Call ID: {call_id}")
        print(f"💥 UNEXPECTED ERROR: {str(e)}")
        print(f"❌ ===== PRICING TOOL ERROR END =====\n")
        
        return {
            "success": False,
            "error": "We are having problem getting the pricing at the moment. Please try again later.",
            "status": "system_error"
        }

@router.post("/updateBooking")
async def update_booking(request: BookingRequest):
    """Dedicated endpoint for updating bookings"""
    
    call_id = generate_call_id()
    timestamp = datetime.now().isoformat()
    jwt_token = await resolve_cabee_token(request.companyId, request.companySlug)
    
    try:
        print(f"\n✏️ ===== UPDATE BOOKING TOOL CALLED =====")
        print(f"📅 Timestamp: {timestamp}")
        print(f"🆔 Call ID: {call_id}")
        print(f"📥 INCOMING REQUEST: {request.dict()}")
        
        result = await handle_update_booking(request, jwt_token, call_id)
        
        print(f"📤 SENDING RESPONSE TO AI: {json.dumps(result, indent=2)}")
        print(f"✏️ ===== UPDATE BOOKING COMPLETE =====\n")
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ ===== UPDATE BOOKING TOOL ERROR =====")
        print(f"🆔 Call ID: {call_id}")
        print(f"💥 UNEXPECTED ERROR: {str(e)}")
        print(f"❌ ===== UPDATE BOOKING TOOL ERROR END =====\n")
        
        return {
            "status": "error",
            "booking_status": "system_error",
            "error": "System temporarily unavailable",
            "data": None
        }

@router.post("/bookCab")
async def book_cab(request: BookingRequest, http_request: Request):
    """Handle all booking operations using Cabee APIs"""
    
    call_id = generate_call_id()
    timestamp = datetime.now().isoformat()
    jwt_token = await resolve_cabee_token(request.companyId, request.companySlug)
    
    # 🆔 RETRIEVE CABEE CALL ID FROM QUERY PARAMS
    cabee_call_id = http_request.query_params.get("cabee_call_id")
    print(f"🆔 Tool called with Cabee Call ID: {cabee_call_id}")
    
    try:
        print(f"\n🚖 ===== BOOKING TOOL CALLED =====")
        print(f"📅 Timestamp: {timestamp}")
        print(f"🆔 Call ID: {call_id}")
        print(f"📥 INCOMING REQUEST: {request.dict()}")
        print(f"🎯 OPERATION: {request.operation}")
        
        if request.operation == "cabBooking":
            return await handle_create_booking(request, jwt_token, call_id, http_request, cabee_call_id)
        elif request.operation == "getBooking":
            return await handle_get_booking(request, jwt_token, call_id)
        elif request.operation == "updateBooking":
            return await handle_update_booking(request, jwt_token, call_id)
        elif request.operation == "cancelBooking":
            return await handle_cancel_booking(request, jwt_token, call_id)
        elif request.operation == "getDriverLocation":
            return await handle_get_driver_location(request, jwt_token, call_id)
        else:
            return {
                "status": "error",
                "booking_status": "invalid_operation",
                "error": "Invalid operation requested",
                "data": None
            }
            
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ ===== BOOKING TOOL ERROR =====")
        print(f"🆔 Call ID: {call_id}")
        print(f"🎯 Failed Operation: {request.operation}")
        print(f"💥 UNEXPECTED ERROR: {str(e)}")
        print(f"❌ ===== BOOKING TOOL ERROR END =====\n")
        
        # Return user-friendly error instead of HTTP exception
        return {
            "status": "error",
            "booking_status": "system_error",
            "error": "System temporarily unavailable",
            "data": None
        }

async def handle_create_booking(request: BookingRequest, jwt_token: str, call_id: str, http_request: Request, cabee_call_id: str = None):
    """Handle cab booking creation"""
    
    print(f"\n📝 === CREATE BOOKING OPERATION ===")
    
    # 🔍 RESOLVE AUTHENTIC CALL IDENTIFIER (CABEE CALL ID IS PRIMARY)
    final_call_sid_for_cabee = cabee_call_id or "unknown"
    
    # If no Cabee ID was passed via URL, try to resolve it from the Ultravox call ID in headers
    if final_call_sid_for_cabee == "unknown":
        ultravox_call_sid = (
            http_request.headers.get("x-ultravox-call-id")
            or http_request.headers.get("x-call-id")
            or http_request.headers.get("X-Call-ID")
        )
        if ultravox_call_sid:
            try:
                from registry import active_call_registry
                # Check our local registry for a mapped Cabee ID
                entry = active_call_registry.get(ultravox_call_sid)
                if entry and entry.get("cabee_call_id"):
                    final_call_sid_for_cabee = entry["cabee_call_id"]
                    print(f"✅ Resolved Cabee ID from headers mapping: {final_call_sid_for_cabee}")
                else:
                    # If still unknown, use the Ultravox ID as fallback
                    final_call_sid_for_cabee = ultravox_call_sid
                    print(f"🔄 Fallback: Using Ultravox ID as Call SID: {final_call_sid_for_cabee}")
            except Exception as e:
                final_call_sid_for_cabee = ultravox_call_sid
                print(f"⚠️ Error resolving mapping: {e}. Using Ultravox ID.")
    
    print(f"🎯 CABEE CALL SID FOR PAYLOAD: {final_call_sid_for_cabee}")
    
    # Extract authentic Ultravox call ID for internal tracking if needed
    ultravox_call_sid = (
        http_request.headers.get("x-ultravox-call-id")
        or http_request.headers.get("x-call-id")
        or http_request.headers.get("X-Call-ID")
        or "unknown"
    )
    
    # Vehicle type mapping
    vehicle_type_mapping = {
        'standard': 68,
        'estate': 69,
        'mpv': 70,
        'MPV': 70,
        'luxury': 71,
        'executive': 71
    }
    
    # Get numeric vehicle type ID
    numeric_vehicle_type_id = 68  # default to standard
    if request.vehicleTypeId:
        # If it's a direct number (like 415), use it directly
        if str(request.vehicleTypeId).isdigit():
            numeric_vehicle_type_id = int(request.vehicleTypeId)
        else:
            # Otherwise try to map from string
            vehicle_type_lower = str(request.vehicleTypeId).lower()
            numeric_vehicle_type_id = vehicle_type_mapping.get(
                vehicle_type_lower, 
                68
            )
    
    # Use the user-provided phone number, prioritize passengerPhone over Phone
    user_phone = request.passengerPhone or request.Phone
    if not user_phone:
        print(f"⚠️ WARNING: No phone number provided, using fallback")
        user_phone = '03000000000'
    else:
        # Strip all non-numeric characters
        user_phone = ''.join(filter(str.isdigit, user_phone))
    
    print(f"📞 Using phone number: {user_phone}")
    
    booking_data = {
        "id": 0,
        "jobNO": "string",
        "date": request.date or datetime.now().isoformat(),
        "passengerName": request.passengerName,
        "passengerPhone": user_phone,
        "passengerMobile": user_phone,
        "passengerEmail": request.passengerEmail,
        "passengers": int(request.passengers) if request.passengers else 1,
        "bags": int(request.bags) if request.bags else 0,
        "note": request.note or '',
        "companyId": int(request.companyId) if request.companyId else 99,
        "driver_id": None,
        "paymentMethod_id": 1,
        "driverPrice": float(request.driverPrice) if request.driverPrice else (float(request.customerPrice) if request.customerPrice else 0),
        "customerPrice": float(request.customerPrice) if request.customerPrice else 0,
        "eCommission": float(request.eCommission) if request.eCommission else 0,
        "companyPrice": float(request.companyPrice) if request.companyPrice else (float(request.customerPrice) if request.customerPrice else 0),
        "bookingFee": float(request.bookingFee) if request.bookingFee else 0,
        "totalJobPrice": float(request.totalJobPrice) if request.totalJobPrice else (float(request.customerPrice) if request.customerPrice else 0),
        "duration": 0,
        "distance": 0,
        "jobSource": 3,
        "jobcase": 0,
        "vehicleTypeId": numeric_vehicle_type_id,
        "origin": request.origin,
        "destination": request.destination,
        "stops": _normalize_stops(request.stops),
        "title": request.title or "",
        "flight_Number": request.flight_Number or "",
        "flight_Info": request.flight_Info or "",
        "house_number": request.house_number or "",
        "flat_info": request.flat_info or "",
        "call_sid": final_call_sid_for_cabee
    }
    
    print(f"🌐 CALLING CABEE CREATE BOOKING API:")
    print(f"   URL: {CABEE_API_BASE}/Job/CreateOnlineJobv2AviAddress (fallback CreateOnlineJob)")
    print(f"   Vehicle Type Mapping: \"{request.vehicleTypeId}\" → {numeric_vehicle_type_id}")
    print(f"   Phone Number Used: {user_phone}")
    print(f"   Payload: {json.dumps(booking_data, indent=2)}")
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{CABEE_API_BASE}/Job/CreateOnlineJobv2AviAddress",
            headers={
                "accept": "text/plain",
                "Content-Type": "text/json",
                "Authorization": f"Bearer {jwt_token}"
            },
            json=booking_data,
            timeout=30.0
        )
        
        print(f"📡 Cabee Response Status: {response.status_code}")
        
        if not response.is_success:
            # Fallback to legacy endpoint for compatibility
            response = await client.post(
                f"{CABEE_API_BASE}/Job/CreateOnlineJob",
                headers={
                    "accept": "text/plain",
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {jwt_token}"
                },
                json=booking_data,
                timeout=30.0
            )

        if not response.is_success:
            error_text = response.text
            print(f"❌ CREATE BOOKING ERROR: {response.status_code} - {error_text}")
            
            # Return user-friendly error instead of HTTP exception
            return {
                "status": "error",
                "booking_status": "failed",
                "error": "Unable to create booking at the moment",
                "data": None
            }
        
        result = response.json()
        print(f"📤 CREATE BOOKING SUCCESS: {json.dumps(result, indent=2)}")
        print(f"✅ New Job Number: {result.get('jobNO')}")

        print(f"📡 SAVING JOB RECORD - Call ID: {final_call_sid_for_cabee}")

        await _save_job_record(ultravox_call_sid, result, request.vehicleTypeId, final_call_sid_for_cabee)
        
        response_data = {
            "status": "success",
            "booking_status": "confirmed",
            "error": None,
            "data": {
                "jobNO": result.get("jobNO"),
                "bookingId": result.get("id"),
                "passengerName": result.get("passengerName"),
                "customerPrice": result.get("customerPrice"),
                "date": result.get("date"),
                "origin": result.get("origin"),
                "destination": result.get("destination"),
                "vehicleType": request.vehicleTypeId,
                "phoneNumber": user_phone
            }
        }
        
        print(f"📤 SENDING RESPONSE TO AI: {json.dumps(response_data, indent=2)}")
        return response_data

async def handle_get_booking(request: BookingRequest, jwt_token: str, call_id: str):
    """Handle getting booking details with job number cleaning"""
    
    print(f"📋 === GET BOOKING OPERATION ===")
    
    # Clean job number by removing dashes (A2-62 → A262)
    clean_job_no = None
    if request.jobNO:
        clean_job_no = request.jobNO.replace("-", "")
        if clean_job_no != request.jobNO:
            print(f"🔧 CLEANED JOB NUMBER: {request.jobNO} → {clean_job_no}")
        url = f"{CABEE_API_BASE}/Job/GetOnlineJobForBot?jobNo={clean_job_no}"
    elif request.Phone:
        url = f"{CABEE_API_BASE}/Job/GetOnlineJobForBot?phoneNumber={request.Phone}"
    else:
        return {
            "status": "error",
            "booking_status": "invalid_request",
            "error": "Either job number or phone number is required",
            "data": None
        }
    
    print(f"📤 GET BOOKING REQUEST: {url}")
    
    async with httpx.AsyncClient() as client:
        response = await client.get(
            url,
            headers={
                "accept": "text/plain",
                "Authorization": f"Bearer {jwt_token}"
            },
            timeout=30.0
        )
        
        print(f"📡 Get Response Status: {response.status_code}")
        
        if response.status_code == 404:
            return {
                "status": "error",
                "booking_status": "not_found",
                "error": "Booking not found",
                "data": None
            }
        
        if not response.is_success:
            error_text = response.text
            print(f"❌ GET BOOKING ERROR: {error_text}")
            
            # Return user-friendly error instead of HTTP exception
            return {
                "status": "error",
                "booking_status": "api_error",
                "error": "Unable to retrieve booking at the moment",
                "data": None
            }
        
        result = response.json()
        print(f"✅ GET BOOKING SUCCESS: {json.dumps(result, indent=2)}")
        
        return {
            "status": "success",
            "booking_status": "found",
            "error": None,
            "data": result
        }

async def handle_update_booking(request: BookingRequest, jwt_token: str, call_id: str):
    """Handle booking updates using UpdateOnlineJobV2 endpoint"""
    
    print(f"✏️ === UPDATE BOOKING OPERATION ===")
    
    if not request.jobNO:
        return {
            "status": "error",
            "booking_status": "invalid_request",
            "error": "Job number is required to update booking",
            "data": None
        }
    
    # Clean job number by removing dashes (A2-62 → A262)
    clean_job_no = request.jobNO.replace("-", "")
    if clean_job_no != request.jobNO:
        print(f"🔧 CLEANED JOB NUMBER: {request.jobNO} → {clean_job_no}")
    
    # Map vehicle types to API IDs if provided as string
    numeric_vehicle_type_id = 0
    if request.vehicleTypeId:
        if isinstance(request.vehicleTypeId, str):
            vehicle_type_mapping = {
                'standard': 68,
                'estate': 69,
                'mpv': 70,
                'MPV': 70,
                'luxury': 71,
                'executive': 71
            }
            vehicle_type_lower = request.vehicleTypeId.lower()
            numeric_vehicle_type_id = vehicle_type_mapping.get(vehicle_type_lower) or int(request.vehicleTypeId) if request.vehicleTypeId.isdigit() else 0
        else:
            numeric_vehicle_type_id = int(request.vehicleTypeId)
    
    # Build update payload matching backend API format
    # Note: Based on testing, the API may require JobNO (capital) in body even with jobno in query
    update_data = {
        "JobNO": clean_job_no,
        "date": request.date if request.date else None,
        "passengerName": request.passengerName if request.passengerName else None,
        "passengerPhone": request.passengerPhone or request.Phone if (request.passengerPhone or request.Phone) else None,
        "passengerMobile": request.passengerMobile or request.passengerPhone or request.Phone if (request.passengerMobile or request.passengerPhone or request.Phone) else None,
        "passengerEmail": request.passengerEmail if request.passengerEmail else None,
        "passengers": int(request.passengers) if request.passengers else None,
        "bags": int(request.bags) if request.bags else None,
        "note": request.note if request.note else None,
        "companyId": 0,
        "driver_id": 0,
        "paymentMethod_id": 0,
        "driverPrice": float(request.customerPrice) if request.customerPrice else 0,
        "customerPrice": float(request.customerPrice) if request.customerPrice else 0,
        "duration": 0,
        "distance": 0,
        "jobSource": 0,
        "jobcase": 0,
        "vehicleTypeId": numeric_vehicle_type_id,
        "origin": request.origin if request.origin else None,
        "destination": request.destination if request.destination else None,
        "companyPrice": 0,
        "eCommission": 0,
        "bookingFee": 0,
        "totalJobPrice": 0,
        "title": request.title if request.title else None,
        "flight_Number": request.flight_Number if request.flight_Number else None,
        "flight_Info": request.flight_Info if request.flight_Info else None,
        "house_number": request.house_number if request.house_number else None,
        "flat_info": request.flat_info if request.flat_info else None
    }
    
    # Remove None values to keep payload clean
    update_data = {k: v for k, v in update_data.items() if v is not None}
    
    # Build URL with jobno as query parameter
    # Using UpdateOnlineJobV2 as specified by backend team
    update_url = f"{CABEE_API_BASE}/Job/UpdateOnlineJobV2?jobno={clean_job_no}"
    
    # Note: Based on API testing, the endpoint may have validation issues on production
    # If UpdateOnlineJobV2 fails, we can fall back to UpdateJob endpoint
    
    print(f"🌐 CALLING CABEE UPDATE BOOKING API:")
    print(f"   URL: {update_url}")
    print(f"   Method: PUT")
    print(f"   Payload: {json.dumps(update_data, indent=2)}")
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.put(
                update_url,
                headers={
                    "accept": "text/plain",
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {jwt_token}"
                },
                json=update_data,
                timeout=30.0
            )
            
            print(f"📡 Update Response Status: {response.status_code}")
            
            if not response.is_success:
                error_text = response.text
                print(f"❌ UPDATE ERROR: {error_text}")
                
                # Return user-friendly error instead of HTTP exception
                return {
                    "status": "error",
                    "booking_status": "update_failed",
                    "error": f"Unable to update booking: {error_text}",
                    "data": None
                }
            
            # Try to parse as JSON, fallback to text
            try:
                result = response.json()
            except:
                result = {"message": response.text}
            
            print(f"✅ UPDATE SUCCESS: {json.dumps(result, indent=2)}")
            
            return {
                "status": "success",
                "booking_status": "updated",
                "error": None,
                "data": result
            }
            
        except httpx.TimeoutException:
            print(f"❌ UPDATE TIMEOUT")
            return {
                "status": "error",
                "booking_status": "timeout",
                "error": "Request timed out while updating booking",
                "data": None
            }
        except Exception as e:
            print(f"❌ UPDATE EXCEPTION: {str(e)}")
            return {
                "status": "error",
                "booking_status": "system_error",
                "error": f"System error: {str(e)}",
                "data": None
            }

async def handle_cancel_booking(request: BookingRequest, jwt_token: str, call_id: str):
    """Handle booking cancellation with job number cleaning"""
    
    print(f"❌ === CANCEL BOOKING OPERATION ===")
    
    # Clean job number by removing dashes (A2-62 → A262)
    clean_job_no = None
    if request.jobNO:
        clean_job_no = request.jobNO.replace("-", "")
        if clean_job_no != request.jobNO:
            print(f"🔧 CLEANED JOB NUMBER: {request.jobNO} → {clean_job_no}")
        url = f"{CABEE_API_BASE}/Job/CancelJob?jobNo={clean_job_no}&companyId=99"
        print(f"🔍 Cancelling by job number: {clean_job_no}")
    elif request.Phone:
        url = f"{CABEE_API_BASE}/Job/CancelJob?mobile={request.Phone}&companyId=99"
        print(f"🔍 Cancelling by phone: {request.Phone}")
    else:
        return {
            "status": "error",
            "booking_status": "invalid_request",
            "error": "Either job number or phone number is required to cancel booking",
            "data": None
        }
    
    print(f"📤 CANCEL REQUEST URL: {url}")
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            url,
            headers={
                "accept": "text/plain",
                "Authorization": f"Bearer {jwt_token}"
            },
            content="",
            timeout=30.0
        )
        
        print(f"📡 Cancel Response Status: {response.status_code}")
        
        if not response.is_success:
            error_text = response.text
            print(f"❌ CANCEL ERROR: {error_text}")
            
            # Return user-friendly error instead of HTTP exception
            return {
                "status": "error",
                "booking_status": "api_error",
                "error": "Unable to cancel booking at the moment",
                "data": None
            }
        
        cancel_result = response.text
        print(f"✅ CANCEL RESPONSE TEXT: {cancel_result}")
        
        # Determine if cancellation was successful
        if any(phrase in cancel_result.lower() for phrase in ["not found", "notfound", "error"]):
            booking_status = "not_found"
            status = "error"
            error = "Booking not found"
        else:
            booking_status = "cancelled"
            status = "success"
            error = None
        
        print(f"🎯 FINAL STATUS: {status}, BOOKING_STATUS: {booking_status}")
        
        return {
            "status": status,
            "booking_status": booking_status,
            "error": error,
            "data": {
                "result": cancel_result,
                "jobNO": clean_job_no or request.jobNO
            }
        }

async def handle_get_driver_location(request: BookingRequest, jwt_token: str, call_id: str):
    """Handle getting driver location with job number cleaning"""
    
    print(f"📍 === GET DRIVER LOCATION OPERATION ===")
    
    if not request.jobNO:
        return {
            "status": "error",
            "booking_status": "invalid_request",
            "error": "Job number is required to get driver location",
            "data": None
        }
    
    # Clean job number by removing dashes (A2-62 → A262)
    clean_job_no = request.jobNO.replace("-", "")
    if clean_job_no != request.jobNO:
        print(f"🔧 CLEANED JOB NUMBER: {request.jobNO} → {clean_job_no}")
    
    print(f"📤 LOCATION REQUEST: Job {clean_job_no}")
    
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{CABEE_API_BASE}/Job/GetDriverCurrentLocationForJob/{clean_job_no}",
            headers={
                "accept": "text/plain",
                "Authorization": f"Bearer {jwt_token}"
            },
            timeout=30.0
        )
        
        print(f"📡 Location Response Status: {response.status_code}")
        
        if response.status_code == 404:
            return {
                "status": "error",
                "booking_status": "driver_not_found",
                "error": "Driver location not available or not assigned yet",
                "data": None
            }
        
        if not response.is_success:
            error_text = response.text
            print(f"❌ LOCATION ERROR: {error_text}")
            
            # Return user-friendly error instead of HTTP exception
            return {
                "status": "error",
                "booking_status": "api_error",
                "error": "Unable to get driver location at the moment",
                "data": None
            }
        
        result = response.json()
        print(f"✅ LOCATION SUCCESS: {json.dumps(result, indent=2)}")
        
        return {
            "status": "success",
            "booking_status": "driver_located",
            "error": None,
            "data": {
                "jobNO": clean_job_no,
                "location": result
            }
        }

@router.post("/getVehicleTypes")
async def get_vehicle_types(request: VehicleTypeRequest):
    """Get available vehicle types for a company by its slug"""
    
    timestamp = datetime.now().isoformat()
    try:
        print(f"\n🚗 ===== GET VEHICLE TYPES TOOL CALLED =====")
        print(f"📅 Timestamp: {timestamp}")
        print(f"📥 INCOMING REQUEST: {request.dict()}")
        
        if not request.companySlug:
            return {
                "success": False,
                "error": "Unable to retrieve vehicle types at the moment.",
                "details": "Company slug is required"
            }

        # 1. Look up agent by slug to resolve API token and numeric company ID.
        # Try configured URL first, then local service fallback to avoid nginx/html responses.
        configured_base_url = os.getenv("TOOLS_BASE_URL", "https://agent.cabex.co.uk").rstrip("/")
        fallback_base_url = "http://127.0.0.1:5005"
        lookup_urls = [
            f"{configured_base_url}/api/agents/slug/{request.companySlug}",
            f"{fallback_base_url}/api/agents/slug/{request.companySlug}"
        ]
        
        async with httpx.AsyncClient() as client:
            agent = None
            lookup_error = None

            for lookup_url in lookup_urls:
                try:
                    print(f"🔎 Looking up agent by slug at: {lookup_url}")
                    agent_response = await client.get(lookup_url, timeout=15.0)

                    if not agent_response.is_success:
                        lookup_error = f"Agent lookup failed ({agent_response.status_code})"
                        continue

                    try:
                        agent = agent_response.json()
                    except ValueError:
                        text_preview = (agent_response.text or "")[:180]
                        lookup_error = f"Agent lookup returned non-JSON: {text_preview}"
                        continue

                    break
                except Exception as lookup_exc:
                    lookup_error = str(lookup_exc)
                    continue

            if not agent:
                print(f"⚠️ Agent lookup failed for slug: {request.companySlug} ({lookup_error})")
                return {
                    "success": False,
                    "error": "Unable to retrieve vehicle types at the moment.",
                    "details": f"Agent not found for slug: {request.companySlug}. {lookup_error or ''}".strip()
                }

            # Prefer agent-specific token for this company, fallback to platform token.
            api_token = agent.get("api_Token") or os.getenv("CABEE_JWT_TOKEN")
            api_company_id = agent.get("company_id")

            if not api_token:
                return {
                    "success": False,
                    "error": "Unable to retrieve vehicle types at the moment.",
                    "details": "Missing Cabee JWT token (CABEE_JWT_TOKEN)"
                }
            
            if not api_company_id:
                print(f"⚠️ No numeric company ID found for agent: {request.companySlug}")
                return {
                    "success": False,
                    "error": "Unable to retrieve vehicle types at the moment.",
                    "details": f"No numeric company ID found for agent with slug: {request.companySlug}"
                }

            # 2. Call Cabee API
            api_url = f"{CABEE_API_BASE}/VehicleType/GetCompanySelectedVechiletype/{api_company_id}"
            print(f"🌐 CALLING VEHICLE TYPE API:")
            print(f"   URL: {api_url}")
            
            response = await client.get(
                api_url,
                headers={
                    "accept": "text/plain",
                    "Authorization": f"Bearer {api_token}"
                },
                timeout=30.0
            )
            
            print(f"📡 API Response Status: {response.status_code}")

            content_type = (response.headers.get("content-type") or "").lower()
            if "text/html" in content_type:
                text_preview = (response.text or "")[:200]
                return {
                    "success": False,
                    "error": "Unable to retrieve vehicle types at the moment.",
                    "details": f"Vehicle type API returned HTML (likely invalid/expired token or upstream route issue): {text_preview}"
                }
            
            if not response.is_success:
                error_text = response.text
                print(f"❌ API Error: {response.status_code} - {error_text}")
                return {
                    "success": False,
                    "error": "Unable to retrieve vehicle types at the moment.",
                    "details": f"Vehicle type API error: {response.status_code} - {error_text}"
                }
            
            try:
                result = response.json()
            except ValueError:
                text_preview = (response.text or "")[:200]
                return {
                    "success": False,
                    "error": "Unable to retrieve vehicle types at the moment.",
                    "details": f"Vehicle type API returned non-JSON: {text_preview}"
                }

            print(f"📤 API RESPONSE DATA: Found {len(result)} vehicle types")
            print(f"🚗 ===== GET VEHICLE TYPES TOOL COMPLETE =====\n")
            
            return {
                "success": True,
                "vehicles": result
            }
            
    except Exception as e:
        print(f"❌ ===== GET VEHICLE TYPES TOOL ERROR =====")
        print(f"💥 ERROR DETAILS: {str(e)}")
        print(f"❌ ===== GET VEHICLE TYPES TOOL ERROR END =====\n")
        return {
            "success": False,
            "error": "Unable to retrieve vehicle types at the moment.",
            "details": str(e)
        }