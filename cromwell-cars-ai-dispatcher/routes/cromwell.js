import express from 'express';
import 'dotenv/config';

const router = express.Router();

// Cromwell Cars API Configuration
const CROMWELL_API_BASE = 'https://online.ontimechauffeurs.co.uk/api';
const CABEE_API_BASE = 'https://capi.cabee-est.com/api';
// const CABEE_API_BASE = 'https://api.nettechltd.com/api';
const JWT_TOKEN = process.env.CABEE_JWT_TOKEN;

// ===== CALL ID REGISTRY =====
// Stores callId -> registrationTime for active calls
// bookCab will pop the most recent callId when saving a job
const activeCallRegistry = new Map(); // callId -> { registeredAt, used }

// Register a new call ID (called from Python webcall service when call is created)
router.post('/register-call', (req, res) => {
    const { call_id } = req.body;
    if (!call_id) {
        return res.status(400).json({ error: 'call_id is required' });
    }
    activeCallRegistry.set(call_id, { registeredAt: Date.now(), used: false });
    console.log(`📋 Registered call ID: ${call_id}. Registry size: ${activeCallRegistry.size}`);
    // Prune old entries (older than 2 hours)
    const cutoff = Date.now() - 2 * 60 * 60 * 1000;
    for (const [key, val] of activeCallRegistry.entries()) {
        if (val.registeredAt < cutoff) activeCallRegistry.delete(key);
    }
    res.json({ status: 'ok', call_id });
});

// Get the most recent registered call ID (internal use)
function getMostRecentCallId() {
    let best = null;
    let bestTime = 0;
    for (const [callId, meta] of activeCallRegistry.entries()) {
        if (meta.registeredAt > bestTime) {
            bestTime = meta.registeredAt;
            best = callId;
        }
    }
    return best;
}
// ===== END CALL ID REGISTRY =====


router.post('/validateAddress', async (req, res) => {
    const timestamp = new Date().toISOString();
    const callId = `addr_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;

    try {
        console.log('\n🔍 ===== ADDRESS VALIDATION TOOL CALLED =====');
        console.log(`📅 Timestamp: ${timestamp}`);
        console.log(`🆔 Call ID: ${callId}`);
        console.log('📥 INCOMING REQUEST:');
        console.log(JSON.stringify(req.body, null, 2));

        const { address_lines, postcode, building } = req.body;

        // Fix: Parse stringified address_lines array from AI
        let parsedAddressLines = address_lines;
        if (typeof address_lines === 'string') {
            try {
                // Parse JSON string like "[\"Address\"]" to actual array
                parsedAddressLines = JSON.parse(address_lines);
                console.log(`🔧 PARSED STRING TO ARRAY: ${address_lines} → ${JSON.stringify(parsedAddressLines)}`);
            } catch (e) {
                // If not valid JSON, treat as single address
                parsedAddressLines = [address_lines];
                console.log(`🔧 CONVERTED TO ARRAY: ${address_lines} → ${JSON.stringify(parsedAddressLines)}`);
            }
        } else if (!Array.isArray(address_lines)) {
            // Ensure it's always an array
            parsedAddressLines = address_lines ? [address_lines] : [];
            console.log(`🔧 MADE ARRAY: ${address_lines} → ${JSON.stringify(parsedAddressLines)}`);
        } else {
            console.log(`✅ ALREADY ARRAY: ${JSON.stringify(parsedAddressLines)}`);
        }

        const requestPayload = {
            address_lines: parsedAddressLines,
            postcode: postcode,

        };

        console.log('🌐 CALLING CROMWELL ADDRESS API:');
        console.log(`   URL: ${CROMWELL_API_BASE}/address/validate`);
        console.log(`   Method: POST`);
        console.log(`   Payload:`, JSON.stringify(requestPayload, null, 2));

        // Call the real Cromwell Cars address validation API
        const response = await fetch(`${CROMWELL_API_BASE}/address/validate`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(requestPayload)
        });

        console.log(`📡 API Response Status: ${response.status} ${response.statusText}`);

        if (!response.ok) {
            const errorText = await response.text();
            console.log(`❌ API Error: ${response.status}`);
            console.log(`❌ Error Details: ${errorText}`);
            throw new Error(`Address validation API error: ${response.status} - ${errorText}`);
        }

        const result = await response.json();
        console.log('📤 API RESPONSE DATA:');
        console.log(JSON.stringify(result, null, 2));

        console.log('✅ ADDRESS VALIDATION SUCCESS');
        console.log(`🔍 Found ${result.candidates ? result.candidates.length : 0} address candidates`);
        console.log(`🎯 Confidence: ${result.google_confidence || result.source || 'N/A'}`);

        if (result.candidates && result.candidates.length > 0) {
            console.log('📍 TOP ADDRESS CANDIDATE:');
            console.log(`   Formatted: ${result.candidates[0].formatted}`);
            console.log(`   Postcode: ${result.candidates[0].postcode}`);
        }

        console.log('📤 SENDING RESPONSE TO AI:');
        console.log(JSON.stringify(result, null, 2));
        console.log('🔍 ===== ADDRESS VALIDATION COMPLETE =====\n');

        res.json(result);
    } catch (error) {
        console.log('❌ ===== ADDRESS VALIDATION ERROR =====');
        console.log(`🆔 Call ID: ${callId}`);
        console.log(`📅 Timestamp: ${timestamp}`);
        console.log('💥 ERROR DETAILS:', error.message);
        console.log('📤 ERROR RESPONSE TO AI:', {
            success: false,
            error: 'Address validation failed',
            details: error.message
        });
        console.log('❌ ===== ADDRESS VALIDATION ERROR END =====\n');

        res.status(500).json({
            success: false,
            error: 'Address validation failed',
            details: error.message
        });
    }
});

// Check pricing using real API
// Helper to get agent data by slug
async function getAgentBySlug(slug) {
    try {
        if (!slug) return null;
        const agentServiceUrl = process.env.AGENT_MANAGEMENT_URL || 'http://localhost:5005';
        const response = await fetch(`${agentServiceUrl}/api/agents`);
        if (!response.ok) return null;
        const agents = await response.json();
        return agents.find(a => a.company_slug === slug);
    } catch (error) {
        console.error(`❌ Error fetching agent by slug: ${error.message}`);
        return null;
    }
}

// Helper to get API token for a specific company
async function getAgentApiToken(companyId) {
    try {
        // Default to env token if no companyId
        if (!companyId || companyId === '99') {
            return process.env.JWT_TOKEN;
        }

        // Call Agent Management Service to find agent with this company ID
        // Since we don't have a direct lookup by company ID, we'll fetch all and filter
        // In a production system, you'd want a specific endpoint for this
        const agentServiceUrl = process.env.AGENT_MANAGEMENT_URL || 'http://localhost:5005';
        const response = await fetch(`${agentServiceUrl}/api/agents`);

        if (!response.ok) {
            console.log(`⚠️ Failed to fetch agents: ${response.status}`);
            return process.env.JWT_TOKEN;
        }

        const agents = await response.json();
        const agent = agents.find(a => a.company_id === parseInt(companyId));

        if (agent && agent.api_Token) {
            console.log(`🔑 Found custom API token for company ${companyId}`);
            return agent.api_Token;
        }

        console.log(`ℹ️ No custom token found for company ${companyId}, using default`);
        return process.env.JWT_TOKEN;
    } catch (error) {
        console.error(`❌ Error fetching agent token: ${error.message}`);
        return process.env.JWT_TOKEN;
    }
}

router.post('/checkPricing', async (req, res) => {
    const timestamp = new Date().toISOString();
    const callId = `price_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;

    try {
        console.log('\n💰 ===== PRICING TOOL CALLED =====');
        console.log(`📅 Timestamp: ${timestamp}`);
        console.log(`🆔 Call ID: ${callId}`);
        console.log('📥 INCOMING REQUEST:');
        console.log(JSON.stringify(req.body, null, 2));

        const { sourceAddress, destinationAddress, companyId, vehicleTypeId, stops } = req.body;

        if (!companyId) {
            throw new Error('Company ID is required');
        }

        if (!sourceAddress || !destinationAddress) {
            throw new Error('Source and destination addresses are required');
        }

        const cid = companyId;
        const vid = vehicleTypeId || 0; // Default to 0 (all vehicles) if not provided

        let fromLoc = sourceAddress;
        if (stops && Array.isArray(stops) && stops.length > 0) {
            fromLoc += ',' + stops.join(',');
        }

        // Get dynamic token
        const apiToken = await getAgentApiToken(cid);

        const apiUrl = `https://capi.cabee-est.com/api/Tariff/external/CalculateTariffPriceForBotAviAddress?fromLocation=${encodeURIComponent(fromLoc)}&toLocation=${encodeURIComponent(destinationAddress)}&companyId=${cid}&vehicleTypeId=${vid}`;
        console.log('🌐 CALLING PRICING API:');
        console.log(`   URL: ${apiUrl}`);
        console.log(`   Method: GET`);

        const response = await fetch(apiUrl, {
            method: 'GET',
            headers: {
                'accept': '*/*',
                'Authorization': `Bearer ${apiToken}`
            }
        });

        console.log(`📡 API Response Status: ${response.status} ${response.statusText}`);

        if (!response.ok) {
            const errorText = await response.text();
            console.log(`❌ API Error: ${response.status}`);
            console.log(`❌ Error Details: ${errorText}`);
            throw new Error(`Pricing API error: ${response.status}`);
        }

        const result = await response.json();
        console.log('📤 API RESPONSE DATA:');
        console.log(JSON.stringify(result, null, 2));

        console.log('💰 ===== PRICING TOOL COMPLETE =====\n');

        res.json(result);
    } catch (error) {
        console.log('❌ ===== PRICING TOOL ERROR =====');
        console.log(`🆔 Call ID: ${callId}`);
        console.log(`📅 Timestamp: ${timestamp}`);
        console.log('💥 ERROR DETAILS:', error.message);
        console.log('📤 ERROR RESPONSE TO AI:', {
            success: false,
            error: 'Failed to get pricing',
            details: error.message
        });
        console.log('❌ ===== PRICING TOOL ERROR END =====\n');

        // Return a 200 OK with error details so the AI can handle it gracefully
        res.json({
            success: false,
            error: 'We are having problem getting the pricing at the moment. Please try again later.',
            details: error.message
        });
    }
});

// Get available vehicle types for a company
router.post('/getVehicleTypes', async (req, res) => {
    const timestamp = new Date().toISOString();
    try {
        console.log('\n🚗 ===== GET VEHICLE TYPES TOOL CALLED =====');
        console.log(`📅 Timestamp: ${timestamp}`);
        console.log('📥 INCOMING REQUEST:');
        console.log(JSON.stringify(req.body, null, 2));

        const { companySlug } = req.body;

        if (!companySlug) {
            throw new Error('Company slug is required');
        }

        // Look up agent by slug to get both api_Token and the numeric company_id
        const agent = await getAgentBySlug(companySlug);

        let apiToken = process.env.JWT_TOKEN;
        let apiCompanyId = null;

        if (agent) {
            apiToken = agent.api_Token || apiToken;
            apiCompanyId = agent.company_id;
            console.log(`🔑 Using custom API token for agent: ${agent.agent_name}`);
            console.log(`🏢 Using platform company ID: ${apiCompanyId}`);
        } else {
            console.log(`⚠️ Agent not found for slug: ${companySlug}`);
        }

        if (!apiCompanyId) {
            throw new Error(`No numeric company ID found for agent with slug: ${companySlug}`);
        }

        const apiUrl = `${CABEE_API_BASE}/VehicleType/GetCompanySelectedVechiletype/${apiCompanyId}`;
        // api_url = f"https://{CABEE_API_BASE}/api/Tariff/external/calculate-pricev2/{from_loc}/{to_loc}/{company_id}/{vehicle_type_id}?bot=true"

        console.log('🌐 CALLING VEHICLE TYPE API:');
        console.log(`   URL: ${apiUrl}`);
        console.log(`   Token: ${apiToken ? '✅ Found' : '⚠️ Missing'}`);

        const response = await fetch(apiUrl, {
            method: 'GET',
            headers: {
                'accept': 'text/plain',
                'Authorization': `Bearer ${apiToken}`
            }
        });

        console.log(`📡 API Response Status: ${response.status} ${response.statusText}`);

        if (!response.ok) {
            const errorText = await response.text();
            console.log(`❌ API Error: ${response.status} - ${errorText}`);
            throw new Error(`Vehicle type API error: ${response.status} - ${errorText}`);
        }

        const result = await response.json();
        console.log('📤 API RESPONSE DATA:');
        console.log(JSON.stringify(result, null, 2));
        console.log('🚗 ===== GET VEHICLE TYPES TOOL COMPLETE =====\n');

        res.json({
            success: true,
            vehicles: result
        });
    } catch (error) {
        console.log('❌ ===== GET VEHICLE TYPES TOOL ERROR =====');
        console.log('💥 ERROR DETAILS:', error.message);
        console.log('❌ ===== GET VEHICLE TYPES TOOL ERROR END =====\n');

        res.json({
            success: false,
            error: 'Unable to retrieve vehicle types at the moment.',
            details: error.message
        });
    }
});

// Handle all booking operations using real Cabee APIs
router.post('/bookCab', async (req, res) => {
    const timestamp = new Date().toISOString();
    const callId = `booking_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;

    try {
        console.log('\n🚖 ===== BOOKING TOOL CALLED =====');
        console.log(`📅 Timestamp: ${timestamp}`);
        console.log(`🆔 Call ID: ${callId}`);
        console.log('📥 ALL REQUEST HEADERS:');
        console.log(JSON.stringify(req.headers, null, 2));
        console.log('📥 INCOMING REQUEST:');
        console.log(JSON.stringify(req.body, null, 2));

        const { operation, call_sid, jobNO, Phone, pickupAddress, dropoffAddress, passengerName, passengerEmail, passengerPhone, passengers, vehicleTypeId, origin, destination, date, customerPrice, driverPrice, bags, note, companyId, eCommission, totalJobPrice, companyPrice, bookingFee, stops } = req.body;

        console.log(`🎯 OPERATION: ${operation}`);
        console.log(`📋 Key Parameters:`);
        if (jobNO) console.log(`   Job Number: ${jobNO}`);
        if (Phone || passengerPhone) console.log(`   Phone: ${passengerPhone || Phone}`);
        if (passengerName) console.log(`   Passenger: ${passengerName}`);
        if (origin) console.log(`   From: ${origin}`);
        if (destination) console.log(`   To: ${destination}`);

        switch (operation) {
            case 'cabBooking':
                console.log('\n📝 === CREATE BOOKING OPERATION ===');

                // Map vehicle types to API IDs (based on your working example)
                const vehicleTypeMapping = {
                    'standard': 68,
                    'estate': 69,
                    'mpv': 70,
                    'MPV': 70,
                    'luxury': 71,
                    'executive': 71
                };

                // Get numeric vehicle type ID
                let numericVehicleTypeId = 68; // default to standard
                if (vehicleTypeId) {
                    // If it's a direct number (like 415), use it directly
                    if (!isNaN(vehicleTypeId)) {
                        numericVehicleTypeId = parseInt(vehicleTypeId);
                    } else {
                        // Otherwise try to map from string (e.g. "standard" -> 68)
                        const vehicleTypeLower = vehicleTypeId.toString().toLowerCase();
                        numericVehicleTypeId = vehicleTypeMapping[vehicleTypeLower] || 68;
                    }
                }

                // Determine the best phone number to use
                let finalPhone = passengerPhone || Phone || '03000000000';

                // Strip all non-numeric characters (spaces, dashes, etc.)
                if (finalPhone) {
                    finalPhone = finalPhone.replace(/\D/g, '');
                }

                const bookingData = {
                    id: 0,
                    jobNO: "string", // API will assign actual job number
                    date: date || new Date().toISOString(),
                    passengerName: passengerName,
                    passengerPhone: finalPhone,
                    passengerMobile: finalPhone,
                    passengerEmail: passengerEmail,
                    passengers: parseInt(passengers) || 1,
                    bags: parseInt(bags) || 0,
                    note: note || '',
                    companyId: companyId ? parseInt(companyId) : 99,
                    paymentMethod_id: 1, // Required field - set to 1 instead of null
                    driverPrice: parseFloat(driverPrice) || parseFloat(customerPrice) || 0,
                    customerPrice: parseFloat(customerPrice) || 0,
                    eCommission: parseFloat(eCommission) || 0,
                    companyPrice: parseFloat(companyPrice) || parseFloat(customerPrice) || 0,
                    bookingFee: parseFloat(bookingFee) || 0,
                    totalJobPrice: parseFloat(totalJobPrice) || parseFloat(customerPrice) || 0,
                    duration: 0, // Will be calculated by system
                    distance: 0, // Will be calculated by system
                    jobSource: 3,
                    jobcase: 0, // Set to 3 to match working payload
                    vehicleTypeId: numericVehicleTypeId, // Now numeric
                    origin: origin,
                    destination: destination,
                    stops: Array.isArray(stops) ? stops : (stops ? [stops] : []),
                    title: "",
                    flight_Number: "",
                    flight_Info: "",
                    house_number: "",
                    flat_info: ""
                };

                // Get dynamic token
                const createApiToken = await getAgentApiToken(companyId);

                console.log('🌐 CALLING CABEE CREATE BOOKING API:');
                console.log(`   URL: ${CABEE_API_BASE}/Job/CreateOnlineJobv2AviAddress`);
                console.log(`   Method: POST`);
                console.log(`   Headers: Authorization: Bearer ${createApiToken?.substring(0, 20)}...`);
                console.log(`   Vehicle Type Mapping: "${vehicleTypeId}" → ${numericVehicleTypeId}`);
                console.log(`   Phone Number: ${finalPhone}`);
                console.log(`   Payload:`, JSON.stringify(bookingData, null, 2));

                const createResponse = await fetch(`${CABEE_API_BASE}/Job/CreateOnlineJobv2AviAddress`, {
                    method: 'POST',
                    headers: {
                        'accept': 'text/plain',
                        'Content-Type': 'text/json',
                        'Authorization': `Bearer ${createApiToken}`
                    },
                    body: JSON.stringify(bookingData)
                });

                console.log(`📡 Cabee Response Status: ${createResponse.status} ${createResponse.statusText}`);

                if (!createResponse.ok) {
                    const errorText = await createResponse.text();
                    console.log(`❌ CREATE BOOKING ERROR:`);
                    console.log(`   Status: ${createResponse.status}`);
                    console.log(`   Error Text: ${errorText}`);
                    throw new Error(`Create booking API error: ${createResponse.status} - ${errorText}`);
                }

                const createResult = await createResponse.json();
                console.log('📤 CREATE BOOKING SUCCESS:');
                console.log(JSON.stringify(createResult, null, 2));
                console.log(`✅ New Job Number: ${createResult.jobNO}`);
                console.log(`👤 Passenger: ${createResult.passengerName}`);
                console.log(`💰 Price: £${createResult.customerPrice}`);

                const responseData = {
                    status: "success",  // Changed from success: true to be more explicit
                    booking_status: "confirmed",  // Added to be explicit about booking state
                    error: null,  // Explicitly showing no error
                    data: {
                        jobNO: createResult.jobNO,
                        bookingId: createResult.id,
                        passengerName: createResult.passengerName,
                        customerPrice: createResult.customerPrice,
                        date: createResult.date,
                        origin: createResult.origin,
                        destination: createResult.destination,
                        vehicleType: vehicleTypeId
                    }
                };

                // Save job to local database
                try {
                    // Priority: 1) AI passed call_sid param, 2) registry lookup, 3) request headers, 4) unknown
                    const registryCallId = getMostRecentCallId();
                    const ultravox_call_id = call_sid || registryCallId || req.headers['x-ultravox-call-id'] || req.headers['x-call-id'] || "unknown";
                    console.log(`📡 SAVING JOB TO DB for Call SID: ${ultravox_call_id} (source: ${call_sid ? 'body param' : registryCallId ? 'registry' : 'fallback'})`);
                    const jobDbPayload = {
                        call_sid: ultravox_call_id,
                        jobNO: String(createResult.jobNO),
                        bookingId: String(createResult.id),
                        passengerName: createResult.passengerName,
                        customerPrice: String(createResult.customerPrice),
                        date: createResult.date,
                        origin: createResult.origin,
                        destination: createResult.destination,
                        vehicleType: String(vehicleTypeId)
                    };

                    const agentServiceUrl = process.env.AGENT_MANAGEMENT_URL || 'http://localhost:5005';
                    fetch(`${agentServiceUrl}/api/jobs`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(jobDbPayload)
                    }).then(res => res.json())
                        .then(data => console.log('✅ Job saved to DB:', data))
                        .catch(e => console.error("❌ Error saving job to DB HTTP call:", e));
                } catch (e) {
                    console.error("❌ Error saving job to DB block:", e);
                }

                console.log('📤 SENDING RESPONSE TO AI:');
                console.log(JSON.stringify(responseData, null, 2));

                res.json(responseData);
                break;

            case 'getBooking':
                console.log('Getting booking details...');

                // let getBookingUrl = `${CABEE_API_BASE}/Job/GetOnlineJobs`;
                // if (jobNO) {
                //     getBookingUrl += `?${jobNO}`;
                // } else if (Phone) {
                //     getBookingUrl += `/GetOnlineJobs/${Phone}`;
                // }
                let getBookingUrl;

                if (jobNO) {
                    // CORRECT: Add parameter name
                    getBookingUrl = `${CABEE_API_BASE}/Job/GetOnlineJobs?jobNO=${jobNO}`;
                } else if (Phone) {
                    // CORRECT: Use phoneNumber parameter
                    getBookingUrl = `${CABEE_API_BASE}/Job/GetOnlineJobs?phoneNumber=${Phone}`;
                } else {
                    throw new Error('Either job number or phone number is required to get booking details');
                }

                // Get dynamic token
                const getApiToken = await getAgentApiToken(companyId);

                console.log(`📤 GET BOOKING REQUEST: ${getBookingUrl}`);

                const getResponse = await fetch(getBookingUrl, {
                    method: 'GET',
                    headers: {
                        'accept': 'text/plain',
                        'Authorization': `Bearer ${getApiToken}`
                    }
                });

                console.log(`📡 Get Response Status: ${getResponse.status} ${getResponse.statusText}`);

                if (!getResponse.ok) {
                    const errorText = await getResponse.text();
                    console.log(`❌ GET BOOKING ERROR:`, errorText);
                    if (getResponse.status === 404) {
                        res.status(404).json({
                            status: "error",
                            booking_status: "not_found",
                            error: "Booking not found",
                            data: null
                        });
                        return;
                    }
                    throw new Error(`Get booking API error: ${getResponse.status} - ${errorText}`);
                }

                const getResult = await getResponse.json();
                console.log('✅ GET BOOKING SUCCESS:', JSON.stringify(getResult, null, 2));

                res.json({
                    status: "success",
                    booking_status: "found",
                    error: null,
                    data: getResult
                });
                break;

            case 'updateBooking':
                console.log('Updating booking...');

                // Try to get company ID early if possible to use correct token
                const updateCompanyId = companyId ? parseInt(companyId) : 99;
                const updateToken = await getAgentApiToken(updateCompanyId);

                // First, try to get the existing booking to use current values
                console.log(`🔍 GETTING EXISTING BOOKING: ${jobNO}`);
                // Use the token for the specific company if known, otherwise typical flow
                const bookCabGetUrl = `${CABEE_API_BASE}/Job/GetOnlineJobs?jobNO=${encodeURIComponent(jobNO)}`;

                let existingBooking = null;

                try {
                    const bookCabGetResponse = await fetch(bookCabGetUrl, {
                        method: 'GET',
                        headers: {
                            'accept': 'text/plain',
                            // Try using the computed token.
                            'Authorization': `Bearer ${updateToken}`
                        }
                    });

                    console.log(`📡 Get Response Status: ${bookCabGetResponse.status} ${bookCabGetResponse.statusText}`);

                    if (bookCabGetResponse.ok) {
                        const bookCabGetResult = await bookCabGetResponse.json();
                        existingBooking = Array.isArray(bookCabGetResult) ? bookCabGetResult[0] : bookCabGetResult;
                        if (existingBooking) {
                            console.log('✅ EXISTING BOOKING FOUND:', JSON.stringify(existingBooking, null, 2));
                        } else {
                            console.log('⚠️ Booking list empty.');
                        }
                    } else {
                        const errorText = await bookCabGetResponse.text();
                        console.log(`⚠️ GET BOOKING FAILED: ${errorText}`);
                    }
                } catch (e) {
                    console.log(`⚠️ ERROR GETTING BOOKING: ${e.message}`);
                }

                // If not found, we proceed with the data we have from the user
                if (!existingBooking) {
                    console.log(`⚠️ Booking not found via GET. Proceeding with provided data for update.`);
                    existingBooking = {
                        id: 0, // Unknown ID
                        jobNO: jobNO,
                        date: new Date().toISOString(),
                        companyId: updateCompanyId,
                        // Fill defaults for required fields to avoid 400
                        passengerName: passengerName || "Unknown",
                        passengerEmail: passengerEmail || "",
                        passengers: passengers ? parseInt(passengers) : 1,
                        bags: bags ? parseInt(bags) : 0,
                        origin: origin || "",
                        destination: destination || "",
                        // Other defaults
                        driverPrice: 0,
                        customerPrice: 0,
                        vehicleTypeId: 0
                    };
                }

                // Handle vehicleTypeId parsing
                let finalVehicleTypeId = existingBooking.vehicleTypeId || 0;
                if (vehicleTypeId) {
                    const parsed = parseInt(vehicleTypeId);
                    if (!isNaN(parsed)) finalVehicleTypeId = parsed;
                }

                // Build update payload
                const updateData = {
                    id: existingBooking.id || 0,
                    jobNO: jobNO, // Key field

                    // Date: if provided, use it. Else use existing.
                    date: date ? new Date(date).toISOString() : existingBooking.date,

                    passengerName: passengerName || existingBooking.passengerName,
                    passengerPhone: Phone || passengerPhone || existingBooking.passengerPhone || existingBooking.passengerMobile || "",
                    passengerMobile: Phone || passengerPhone || existingBooking.passengerMobile || existingBooking.passengerPhone || "",
                    passengerEmail: passengerEmail || existingBooking.passengerEmail || "",

                    passengers: passengers ? parseInt(passengers) : (existingBooking.passengers || 1),
                    bags: bags ? parseInt(bags) : (existingBooking.bags || 0),
                    note: note || existingBooking.note || "",

                    companyId: companyId ? parseInt(companyId) : (existingBooking.companyId || updateCompanyId),

                    driver_id: existingBooking.driver_id || 0,
                    paymentMethod_id: existingBooking.paymentMethod_id || 0,

                    // Reset prices to 0 during update to trigger backend recalculation
                    driverPrice: driverPrice ? parseFloat(driverPrice) : 0,
                    customerPrice: customerPrice ? parseFloat(customerPrice) : 0,

                    duration: existingBooking.duration || 0,
                    distance: existingBooking.distance || 0,
                    jobSource: existingBooking.jobSource || 0,
                    jobcase: existingBooking.jobcase || 0,

                    vehicleTypeId: finalVehicleTypeId,

                    origin: origin || existingBooking.origin || "",
                    destination: destination || existingBooking.destination || "",
                    stops: Array.isArray(stops) ? stops : (stops ? [stops] : (existingBooking.stops || [])),

                    companyPrice: 0,
                    eCommission: 0,
                    bookingFee: 0,
                    totalJobPrice: 0,

                    title: existingBooking.title || "",
                    flight_Number: existingBooking.flight_Number || "",
                    flight_Info: existingBooking.flight_Info || "",
                    house_number: existingBooking.house_number || "",
                    flat_info: existingBooking.flat_info || ""
                };

                const updateUrl = `${CABEE_API_BASE}/Tariff/UpdateOnlineJobForchatbotAviAddress?jobno=${encodeURIComponent(jobNO)}`;

                console.log('🌐 CALLING CABEE UPDATE ONLINE JOB V2 API:');
                console.log(`   URL: ${updateUrl}`);
                console.log(`   Payload:`, JSON.stringify(updateData, null, 2));

                const updateResponse = await fetch(updateUrl, {
                    method: 'PUT',
                    headers: {
                        'accept': 'text/plain',
                        'Content-Type': 'text/json', // Using text/json as seen in user curl/code
                        'Authorization': `Bearer ${updateToken}`
                    },
                    body: JSON.stringify(updateData)
                });

                console.log(`📡 Update Response Status: ${updateResponse.status} ${updateResponse.statusText}`);

                if (!updateResponse.ok) {
                    const errorText = await updateResponse.text();
                    console.log(`❌ UPDATE ERROR:`, errorText);
                    throw new Error(`Update booking API error: ${updateResponse.status} - ${errorText}`);
                }

                // The response might be the object or text
                let updateResult;
                try {
                    updateResult = await updateResponse.json();
                } catch (e) {
                    // Sometimes APIs return text for success or partial objects
                    const text = await updateResponse.text();
                    updateResult = {
                        message: "Update successful",
                        raw_response: text,
                        jobNO: jobNO
                    };
                }

                console.log('✅ UPDATE SUCCESS:', JSON.stringify(updateResult, null, 2));

                res.json({
                    status: "success",
                    booking_status: "updated",
                    error: null,
                    data: updateResult
                });
                break;

            case 'cancelBooking':
                console.log('Cancelling booking...');

                let cancelUrl = `${CABEE_API_BASE}/Job/CancelJob`;

                // Add query parameters to URL
                if (jobNO) {
                    cancelUrl += `?jobNo=${encodeURIComponent(jobNO)}`;
                    console.log(`🔍 Cancelling by job number: ${jobNO}`);
                } else if (Phone) {
                    cancelUrl += `?mobile=${encodeURIComponent(Phone)}`;
                    console.log(`🔍 Cancelling by phone: ${Phone}`);
                } else {
                    const errorMsg = 'Either job number or phone number is required to cancel booking';
                    console.log(`❌ ${errorMsg}`);
                    throw new Error(errorMsg);
                }

                // Add companyId as query parameter
                const cancelCompanyId = companyId || 99;
                cancelUrl += `&companyId=${cancelCompanyId}`;

                // Get dynamic token
                const cancelApiToken = await getAgentApiToken(cancelCompanyId);

                console.log('📤 CANCEL REQUEST URL:', cancelUrl);

                const cancelResponse = await fetch(cancelUrl, {
                    method: 'POST',
                    headers: {
                        'accept': 'text/plain',
                        'Authorization': `Bearer ${cancelApiToken}`
                    },
                    body: '' // Empty body like the working curl command
                });

                console.log(`📡 Cancel Response Status: ${cancelResponse.status} ${cancelResponse.statusText}`);

                if (!cancelResponse.ok) {
                    const errorText = await cancelResponse.text();
                    console.log(`❌ CANCEL ERROR:`, errorText);
                    throw new Error(`Cancel booking API error: ${cancelResponse.status} - ${errorText}`);
                }

                const cancelResult = await cancelResponse.text();
                console.log('✅ CANCEL RESPONSE TEXT:', cancelResult);

                // Determine if cancellation was successful based on response text
                let booking_status = "cancelled";
                let status = "success";
                let error = null;

                if (cancelResult.includes("Not Found") ||
                    cancelResult.includes("not found") ||
                    cancelResult.includes("NotFound") ||
                    cancelResult.toLowerCase().includes("error")) {
                    booking_status = "not_found";
                    status = "error";
                    error = "Booking not found";
                }

                console.log(`🎯 FINAL STATUS: ${status}, BOOKING_STATUS: ${booking_status}`);

                res.json({
                    status: status,
                    booking_status: booking_status,
                    error: error,
                    data: {
                        jobNO: jobNO,
                        result: cancelResult
                    }
                });
                break;

            case 'getDriverLocation':
                console.log('Getting driver location...');

                if (!jobNO) {
                    const errorMsg = 'Job number is required to get driver location';
                    console.log(`❌ ${errorMsg}`);
                    throw new Error(errorMsg);
                }

                console.log(`📤 LOCATION REQUEST: Job ${jobNO}`);

                // Use the working V2 endpoint from your cURL
                const locationUrl = `${CABEE_API_BASE}/Drivers/GetDriverLocationV2/${encodeURIComponent(jobNO)}`;

                console.log('🌐 CALLING CABEE DRIVER LOCATION API (V2):');
                console.log(`   URL: ${locationUrl}`);
                console.log(`   Method: GET`);
                console.log(`   Headers: Authorization: Bearer ${JWT_TOKEN?.substring(0, 20)}...`);

                const locationResponse = await fetch(locationUrl, {
                    method: 'GET',
                    headers: {
                        'accept': 'text/plain',
                        'Authorization': `Bearer ${JWT_TOKEN}`
                    }
                });

                console.log(`📡 Location Response Status: ${locationResponse.status} ${locationResponse.statusText}`);

                if (!locationResponse.ok) {
                    const errorText = await locationResponse.text();
                    console.log(`❌ LOCATION ERROR:`, errorText);

                    if (locationResponse.status === 404) {
                        res.status(404).json({
                            status: "error",
                            booking_status: "driver_not_found",
                            error: "Driver location not available or not assigned yet",
                            data: null
                        });
                        return;
                    }

                    throw new Error(`Get driver location API error: ${locationResponse.status} - ${errorText}`);
                }

                const locationResult = await locationResponse.json();
                console.log('✅ LOCATION SUCCESS (V2):', JSON.stringify(locationResult, null, 2));

                res.json({
                    status: "success",
                    booking_status: "driver_located",
                    error: null,
                    data: {
                        jobNO: jobNO,
                        // The API returns: { datetime, lat, lng, id }
                        location: locationResult
                    }
                });
                break;


            default:
                res.status(400).json({ success: false, error: 'Invalid operation' });
        }
    } catch (error) {
        console.log('❌ ===== BOOKING TOOL ERROR =====');
        console.log(`🆔 Call ID: ${callId}`);
        console.log(`📅 Timestamp: ${timestamp}`);
        console.log(`🎯 Failed Operation: ${req.body.operation || 'unknown'}`);
        console.log('💥 ERROR DETAILS:', error.message);
        console.log('📤 ERROR RESPONSE TO AI:', {
            success: false,
            error: 'Booking operation failed',
            details: error.message
        });
        console.log('❌ ===== BOOKING TOOL ERROR END =====\n');

        res.status(500).json({
            success: false,
            error: 'Booking operation failed',
            details: error.message
        });
    }

    console.log('🚖 ===== BOOKING TOOL COMPLETE =====\n');
});




router.post('/getDriverLocation', async (req, res) => {
    const timestamp = new Date().toISOString();
    const callId = `location_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;

    try {
        console.log('\n📍 ===== GET DRIVER LOCATION TOOL CALLED =====');
        console.log(`📅 Timestamp: ${timestamp}`);
        console.log(`🆔 Call ID: ${callId}`);
        console.log('📥 INCOMING REQUEST:');
        console.log(JSON.stringify(req.body, null, 2));

        const { jobNO } = req.body;

        if (!jobNO) {
            throw new Error('Job number is required to get driver location');
        }

        console.log(`📤 LOCATION REQUEST: Job ${jobNO}`);

        // Use the same working V2 endpoint from the cURL
        const locationUrl = `${CABEE_API_BASE}/Drivers/GetDriverLocationV2/${encodeURIComponent(jobNO)}`;

        console.log('🌐 CALLING CABEE DRIVER LOCATION API (V2):');
        console.log(`   URL: ${locationUrl}`);
        console.log(`   Method: GET`);
        console.log(`   Headers: Authorization: Bearer ${JWT_TOKEN?.substring(0, 20)}...`);

        // Get dynamic token - we don't have companyId here usually, so we might need to rely on default or fetch booking first
        // For now, let's try to use default if no companyId passed, or update tool to pass it
        // Assuming we can use default for location check or we need to look it up
        const locationApiToken = process.env.JWT_TOKEN;

        const locationResponse = await fetch(locationUrl, {
            method: 'GET',
            headers: {
                'accept': 'text/plain',
                'Authorization': `Bearer ${locationApiToken}`
            }
        });

        console.log(`📡 Location Response Status: ${locationResponse.status} ${locationResponse.statusText}`);

        if (!locationResponse.ok) {
            const errorText = await locationResponse.text();
            console.log(`❌ LOCATION ERROR:`, errorText);

            if (locationResponse.status === 404) {
                res.status(404).json({
                    status: "error",
                    booking_status: "driver_not_found",
                    error: "Driver location not available or not assigned yet",
                    data: null
                });
                return;
            }
            throw new Error(`Get driver location API error: ${locationResponse.status} - ${errorText}`);
        }

        const locationResult = await locationResponse.json();
        console.log('✅ LOCATION SUCCESS (V2):', JSON.stringify(locationResult, null, 2));

        console.log('📤 SENDING RESPONSE TO AI:');
        const responseData = {
            status: "success",
            booking_status: "driver_located",
            error: null,
            data: {
                jobNO: jobNO,
                // Directly passing the { datetime, lat, lng, id } object
                location: locationResult
            }
        };
        console.log(JSON.stringify(responseData, null, 2));
        console.log('📍 ===== GET DRIVER LOCATION COMPLETE =====\n');

        res.json(responseData);
    } catch (error) {
        console.log('❌ ===== GET DRIVER LOCATION ERROR =====');
        console.log(`🆔 Call ID: ${callId}`);
        console.log(`📅 Timestamp: ${timestamp}`);
        console.log('💥 ERROR DETAILS:', error.message);
        console.log('📤 ERROR RESPONSE TO AI:', {
            status: "error",
            error: 'Failed to get driver location',
            details: error.message
        });
        console.log('❌ ===== GET DRIVER LOCATION ERROR END =====\n');

        res.status(500).json({
            status: "error",
            error: 'Failed to get driver location',
            details: error.message
        });
    }
});



router.post('/updateBooking', async (req, res) => {
    const timestamp = new Date().toISOString();
    const callId = `update_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;

    try {
        console.log('\n✏️ ===== UPDATE BOOKING TOOL CALLED =====');
        console.log(`📅 Timestamp: ${timestamp}`);
        console.log(`🆔 Call ID: ${callId}`);
        console.log('📥 INCOMING REQUEST:');
        console.log(JSON.stringify(req.body, null, 2));

        const {
            jobNO,
            passengerName,
            passengerPhone,       // ✅ from body
            passengerMobile,      // ✅ from body
            passengerEmail,
            origin,
            destination,
            date,
            vehicleTypeId,
            passengers,
            bags,
            note,
            customerPrice,
            driverPrice,
            companyId // Extract companyId if avaliable
        } = req.body;

        if (!jobNO) {
            throw new Error('Job number is required to update booking');
        }

        // Try to get company ID early if possible to use correct token
        const updateCompanyId = companyId ? parseInt(companyId) : 99;
        const updateToken = await getAgentApiToken(updateCompanyId);

        // 1) Get existing booking
        console.log(`🔍 GETTING EXISTING BOOKING: ${jobNO}`);
        // Use the token for the specific company if known, otherwise typical flow
        const dedicatedGetUrl = `${CABEE_API_BASE}/Job/GetOnlineJobs?jobNO=${encodeURIComponent(jobNO)}`;

        let existingBooking = null;

        try {
            const dedicatedGetResponse = await fetch(dedicatedGetUrl, {
                method: 'GET',
                headers: {
                    'accept': 'text/plain',
                    // Try using the computed token.
                    'Authorization': `Bearer ${updateToken}`
                }
            });

            console.log(`📡 Get Response Status: ${dedicatedGetResponse.status} ${dedicatedGetResponse.statusText}`);

            if (dedicatedGetResponse.ok) {
                const dedicatedGetResult = await dedicatedGetResponse.json();
                existingBooking = Array.isArray(dedicatedGetResult) ? dedicatedGetResult[0] : dedicatedGetResult;
                if (existingBooking) {
                    console.log('✅ EXISTING BOOKING FOUND:', JSON.stringify(existingBooking, null, 2));
                } else {
                    console.log('⚠️ Booking list empty.');
                }
            } else {
                const errorText = await dedicatedGetResponse.text();
                console.log(`⚠️ GET BOOKING FAILED: ${errorText}`);
            }
        } catch (e) {
            console.log(`⚠️ ERROR GETTING BOOKING: ${e.message}`);
        }

        // If not found, we proceed with the data we have from the user
        if (!existingBooking) {
            console.log(`⚠️ Booking not found via GET. Proceeding with provided data for update.`);
            existingBooking = {
                id: 0, // Unknown ID
                jobNO: jobNO,
                date: new Date().toISOString(),
                companyId: updateCompanyId,
                // Fill defaults for required fields to avoid 400
                passengerName: passengerName || "Unknown",
                passengerEmail: passengerEmail || "",
                passengers: passengers ? parseInt(passengers) : 1,
                bags: bags ? parseInt(bags) : 0,
                origin: origin || "",
                destination: destination || "",
                // Other defaults
                driverPrice: 0,
                customerPrice: 0,
                vehicleTypeId: 0,
                // Ensure other fields are present to prevent null pointers if API expects them
                passengerPhone: passengerPhone || "",
                passengerMobile: passengerMobile || ""
            };
        }

        // 2) Vehicle type mapping if needed
        let numericVehicleTypeId = 0;
        if (vehicleTypeId) {
            const vehicleTypeMapping = {
                'standard': 68,
                'estate': 69,
                'mpv': 70,
                'MPV': 70,
                'luxury': 71,
                'executive': 71
            };
            if (typeof vehicleTypeId === 'string') {
                const vehicleTypeLower = vehicleTypeId.toLowerCase();
                numericVehicleTypeId = vehicleTypeMapping[vehicleTypeLower] || parseInt(vehicleTypeId) || 0;
            } else {
                numericVehicleTypeId = parseInt(vehicleTypeId) || 0;
            }
        } else {
            numericVehicleTypeId = existingBooking.vehicleTypeId || 0;
        }

        // 3) Build update payload as close to working curl as possible
        const updateData = {
            id: existingBooking.id || 0,
            jobNO: jobNO,
            date: date ? new Date(date).toISOString() : existingBooking.date,
            passengerName: passengerName || existingBooking.passengerName,
            passengerPhone: passengerPhone || existingBooking.passengerPhone || existingBooking.passengerMobile || "",
            passengerMobile: passengerMobile
                || existingBooking.passengerMobile
                || passengerPhone
                || existingBooking.passengerPhone
                || "",
            passengerEmail: passengerEmail || existingBooking.passengerEmail || "",
            passengers: (passengers !== undefined && passengers !== null)
                ? parseInt(passengers)
                : (existingBooking.passengers || 1),
            bags: (bags !== undefined && bags !== null)
                ? parseInt(bags)
                : (existingBooking.bags || 0),
            note: note || existingBooking.note || "",
            companyId: existingBooking.companyId || updateCompanyId,
            driver_id: existingBooking.driver_id || 0,
            paymentMethod_id: existingBooking.paymentMethod_id || 0,
            // Reset prices to 0 during update to trigger backend recalculation
            driverPrice: (driverPrice !== undefined && driverPrice !== null)
                ? parseFloat(driverPrice)
                : 0,
            customerPrice: (customerPrice !== undefined && customerPrice !== null)
                ? parseFloat(customerPrice)
                : 0,
            duration: existingBooking.duration || 0,
            distance: existingBooking.distance || 0,
            jobSource: existingBooking.jobSource || 3,   // ✅ critical for Cabee to find job
            jobcase: existingBooking.jobcase || 0,
            vehicleTypeId: numericVehicleTypeId,
            origin: origin || existingBooking.origin || "",
            destination: destination || existingBooking.destination || "",
            companyPrice: 0,
            eCommission: 0,
            bookingFee: 0,
            totalJobPrice: 0,
            title: existingBooking.title || "",
            flight_Number: existingBooking.flight_Number || "",
            flight_Info: existingBooking.flight_Info || "",
            house_number: existingBooking.house_number || "",
            flat_info: existingBooking.flat_info || ""
        };

        const updateUrl = `${CABEE_API_BASE}/Tariff/UpdateOnlineJobForchatbot?jobno=${encodeURIComponent(jobNO)}`;

        console.log('🌐 CALLING CABEE UPDATE ONLINE JOB V2 API (EXACT CURL FORMAT):');
        console.log(`   URL: ${updateUrl}`);
        console.log(`   Method: PUT`);
        console.log(`   Headers: Content-Type: text/json`);
        console.log(`   Payload:`, JSON.stringify(updateData, null, 2));

        const dedicatedUpdateResponse = await fetch(updateUrl, {
            method: 'PUT',
            headers: {
                'accept': 'text/plain',
                'Content-Type': 'text/json',
                'Authorization': `Bearer ${updateToken}`
            },
            body: JSON.stringify(updateData)
        });

        console.log(`📡 Update Response Status: ${dedicatedUpdateResponse.status} ${dedicatedUpdateResponse.statusText}`);

        if (!dedicatedUpdateResponse.ok) {
            const errorText = await dedicatedUpdateResponse.text();
            console.log(`❌ UPDATE ERROR:`, errorText);
            throw new Error(`Update booking API error: ${dedicatedUpdateResponse.status} - ${errorText}`);
        }

        let updateResult;
        try {
            updateResult = await dedicatedUpdateResponse.json();
        } catch (e) {
            const text = await dedicatedUpdateResponse.text();
            updateResult = {
                message: "Update successful",
                raw_response: text,
                jobNO: jobNO
            };
        }

        console.log('✅ UPDATE SUCCESS:', JSON.stringify(updateResult, null, 2));

        const responseData = {
            status: "success",
            booking_status: "updated",
            error: null,
            data: updateResult
        };

        console.log('📤 SENDING RESPONSE TO AI:');
        console.log(JSON.stringify(responseData, null, 2));
        console.log('✏️ ===== UPDATE BOOKING COMPLETE =====\n');

        res.json(responseData);
    } catch (error) {
        console.log('❌ ===== UPDATE BOOKING ERROR =====');
        console.log(`🆔 Call ID: ${callId}`);
        console.log(`📅 Timestamp: ${timestamp}`);
        console.log('💥 ERROR DETAILS:', error.message);
        console.log('📤 ERROR RESPONSE TO AI:', {
            status: "error",
            error: 'Failed to update booking',
            details: error.message
        });
        console.log('❌ ===== UPDATE BOOKING ERROR END =====\n');

        res.status(500).json({
            status: "error",
            error: 'Failed to update booking',
            details: error.message
        });
    }
});



export { router };
