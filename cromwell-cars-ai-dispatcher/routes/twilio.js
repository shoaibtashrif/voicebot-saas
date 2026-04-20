import express from 'express';
import twilio from 'twilio';
import 'dotenv/config';
import { createUltravoxCall } from '../ultravox-utils.js';
import { ULTRAVOX_CALL_CONFIG } from '../ultravox-config.js';

const client = twilio(process.env.TWILIO_ACCOUNT_SID, process.env.TWILIO_AUTH_TOKEN);
const destinationNumber = process.env.DESTINATION_PHONE_NUMBER;
const AGENT_MANAGEMENT_URL = process.env.AGENT_MANAGEMENT_URL || 'http://localhost:5005';
const router = express.Router();

// Hack: Dictionary to store Twilio CallSid and Ultravox Call ID mapping
// TODO replace this with something more durable
const activeCalls = new Map();

async function transferActiveCall(ultravoxCallId) {
    try {
        const callData = activeCalls.get(ultravoxCallId);
        if (!callData || !callData.twilioCallSid) {
            throw new Error('Call not found or invalid CallSid');
        }

        // First create a new TwiML to handle the transfer
        const twiml = new twilio.twiml.VoiceResponse();
        twiml.dial().number(destinationNumber);

        // Update the active call with the new TwiML
        const updatedCall = await client.calls(callData.twilioCallSid)
            .update({
                twiml: twiml.toString()
            });

        return {
            status: 'success',
            message: 'Call transfer initiated',
            callDetails: updatedCall
        };

    } catch (error) {
        console.error('Error transferring call:', error);
        throw {
            status: 'error',
            message: 'Failed to transfer call',
            error: error.message
        };
    }
}

// Fetch agent configuration from management service
async function getAgentConfig(agentId) {
    try {
        const response = await fetch(`${AGENT_MANAGEMENT_URL}/api/agents/${agentId}`);
        if (!response.ok) {
            throw new Error(`Agent ${agentId} not found`);
        }
        const agent = await response.json();

        // Fetch agent-specific prompt and tools
        const [promptRes, toolsRes] = await Promise.all([
            fetch(`${AGENT_MANAGEMENT_URL}/api/agents/${agentId}/prompt`),
            fetch(`${AGENT_MANAGEMENT_URL}/api/agents/${agentId}/tools`)
        ]);

        const promptData = await promptRes.json();
        const toolsData = await toolsRes.json();

        return {
            agent,
            systemPrompt: promptData.prompt,
            tools: toolsData.tools
        };
    } catch (error) {
        console.error(`Error fetching agent config: ${error.message}`);
        return null;
    }
}

// Handle incoming calls with agent-specific configuration (by ID or slug)
router.post('/incoming/:agentIdentifier', async (req, res) => {
    const timestamp = new Date().toISOString();
    const callId = `call_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
    const agentIdentifier = req.params.agentIdentifier;

    try {
        console.log('\n📞 ===== INCOMING CALL RECEIVED (AGENT-SPECIFIC) =====');
        console.log(`📅 Timestamp: ${timestamp}`);
        console.log(`🆔 Call Tracking ID: ${callId}`);
        console.log(`🤖 Agent Identifier: ${agentIdentifier}`);
        console.log('📥 TWILIO WEBHOOK DATA:');
        console.log(JSON.stringify(req.body, null, 2));

        const twilioCallSid = req.body.CallSid;
        const fromNumber = req.body.From;
        const toNumber = req.body.To;

        console.log('📋 CALL DETAILS:');
        console.log(`   Twilio Call SID: ${twilioCallSid}`);
        console.log(`   From: ${fromNumber}`);
        console.log(`   To: ${toNumber}`);
        console.log(`   Call Status: ${req.body.CallStatus || 'Unknown'}`);
        console.log(`   Direction: ${req.body.Direction || 'Unknown'}`);

        // Fetch agent configuration (supports both ID and slug)
        console.log('🔍 FETCHING AGENT CONFIGURATION:');
        let agentConfig;

        // Check if it's a number (agent ID) or string (company slug)
        if (/^\d+$/.test(agentIdentifier)) {
            // It's an agent ID
            agentConfig = await getAgentConfig(agentIdentifier);
        } else {
            // It's a company slug - fetch by slug
            try {
                const response = await fetch(`${AGENT_MANAGEMENT_URL}/api/agents/slug/${agentIdentifier}`);
                if (!response.ok) {
                    throw new Error(`Agent with slug '${agentIdentifier}' not found`);
                }
                const agent = await response.json();
                agentConfig = await getAgentConfig(agent.id);
            } catch (error) {
                throw new Error(`Failed to find agent by slug '${agentIdentifier}': ${error.message}`);
            }
        }

        if (!agentConfig) {
            throw new Error(`Failed to load configuration for agent ${agentIdentifier}`);
        }

        console.log(`✅ Agent: ${agentConfig.agent.agent_name}`);
        console.log(`   Company: ${agentConfig.agent.company_name}`);
        console.log(`   Slug: ${agentConfig.agent.company_slug}`);

        // 💰 CHECK COMPANY BALANCE
        let timeLimitSeconds = 600; // Default 10 mins fallback
        try {
            const balanceRes = await fetch(`${AGENT_MANAGEMENT_URL}/api/companies/${agentConfig.agent.company_slug}/balance`);
            const balanceData = await balanceRes.json();

            const balance = balanceData.balance_amount ?? 0;
            const rate = balanceData.call_rate ?? 0.5;

            console.log(`💰 BALANCE CHECK: ${balance} units available (Rate: ${rate}/min)`);

            if (balance <= 0) {
                console.warn(`⛔ INSUFFICIENT BALANCE: Company ${agentConfig.agent.company_slug} has 0 balance.`);

                // Reject call with TwiML
                const twiml = new twilio.twiml.VoiceResponse();
                twiml.say({ voice: 'alice' }, "We are sorry, but this service has insufficient balance to complete your call. Please contact support.");
                twiml.hangup();

                res.type('text/xml');
                return res.send(twiml.toString());
            }

            // Calculate pro-rated time limit using balance_amount and call_rate
            timeLimitSeconds = Math.floor((balance / rate) * 60);
            console.log(`⏱️ TIME LIMIT SET: ${timeLimitSeconds} seconds (${balance} units at ${rate}/min)`);

        } catch (error) {
            console.error(`⚠️ Error checking balance: ${error.message}. Proceeding with default limit.`);
        }

        // 🆔 GENERATE DYNAMIC CABEE CALL ID
        const cabeeCallId = `C-${Math.random().toString(36).substr(2, 8).toUpperCase()}`;
        console.log(`🆔 Generated Dynamic Cabee Call ID for Twilio Call: ${cabeeCallId}`);

        // 💉 INJECT CABEE CALL ID INTO TOOLS
        const injectedTools = agentConfig.tools.map(tool => {
            try {
                if (tool.temporaryTool) {
                    if (!tool.temporaryTool.dynamicParameters) {
                        tool.temporaryTool.dynamicParameters = [];
                    }
                    
                    tool.temporaryTool.dynamicParameters.push({
                        name: "cabee_call_id",
                        location: "PARAMETER_LOCATION_QUERY",
                        schema: {
                            type: "string",
                            default: cabeeCallId,
                            description: "Internal Session ID"
                        },
                        required: true
                    });
                    console.log(`Injecting cabee_call_id into dynamicParameters for: ${tool.temporaryTool.modelToolName}`);
                }
            } catch (err) {
                console.warn(`Could not inject Cabee ID into tool: ${err.message}`);
            }
            return tool;
        });

        // Create custom call config for this agent
        const customCallConfig = {
            ...ULTRAVOX_CALL_CONFIG,
            systemPrompt: agentConfig.systemPrompt,
            selectedTools: injectedTools,
            maxDuration: `${timeLimitSeconds}s`,
            timeExceededMessage: "I'm sorry, but your company's balance has run out. Please contact your administrator to add funds. Goodbye!",
            medium: { "twilio": {} },
            recordingEnabled: true
        };

        // Apply agent-specific voice if configured
        if (agentConfig.agent.voice) {
            customCallConfig.voice = agentConfig.agent.voice;
            console.log(`🔊 Using agent voice: ${agentConfig.agent.voice}`);
        }

        console.log('🤖 CREATING ULTRAVOX AI CALL:');
        console.log(`   Agent: ${agentConfig.agent.agent_name}`);
        console.log(`   Time Limit: ${timeLimitSeconds}s`);
        console.log(`   Tools Count: ${injectedTools.length}`);

        // Create the Ultravox call
        const response = await createUltravoxCall(customCallConfig);

        console.log('✅ ULTRAVOX CALL CREATED:');
        console.log(`   Ultravox Call ID: ${response.callId}`);
        console.log(`   Join URL: ${response.joinUrl}`);

        activeCalls.set(response.callId, {
            twilioCallSid: twilioCallSid,
            fromNumber: fromNumber,
            toNumber: toNumber,
            timestamp: timestamp,
            trackingId: callId,
            agentIdentifier: agentIdentifier,
            agentId: agentConfig.agent.id,
            cabeeCallId: cabeeCallId
        });

        // 📝 REGISTER CALL IN CROMWELL REGISTRY (CRITICAL FOR TOOL CALL SYNC)
        try {
            console.log(`📋 Registering call ${response.callId} with Cabee ID ${cabeeCallId} in Cromwell registry...`);
            const toolsBaseUrl = process.env.TOOLS_BASE_URL || 'http://localhost:3000';
            await fetch(`${toolsBaseUrl}/cromwell/register-call`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    call_id: response.callId,
                    cabee_call_id: cabeeCallId
                })
            });
        } catch (regError) {
            console.error('⚠️ Error registering call in Cromwell registry:', regError.message);
        }

        // DEBUG: Log tools and companyId to verify dynamic values
        console.log(`🔍 DEBUG: Agent Company ID is: ${agentConfig.agent.company_id}`);
        const bookCabTool = agentConfig.tools.find(t => t.temporaryTool?.modelToolName === 'BookCab');
        if (bookCabTool) {
            const coIdParam = bookCabTool.temporaryTool.dynamicParameters.find(p => p.name === 'companyId');
            console.log(`🔍 DEBUG: BookCab tool companyId default: ${coIdParam?.schema?.default}`);
        }

        // 📝 LOG CALL TO MANAGEMENT SERVICE (CRITICAL FOR SYNC)
        try {
            console.log(`📝 Logging Twilio call ${response.callId} to management service...`);
            const logResponse = await fetch(`${AGENT_MANAGEMENT_URL}/api/agents/${agentConfig.agent.id}/log-call`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    call_id: response.callId,
                    cabee_call_id: cabeeCallId,
                    status: 'initiated',
                    caller_number: fromNumber
                })
            });
            if (logResponse.ok) {
                console.log('✅ Twilio call logged successfully');
            } else {
                console.error(`⚠️ Failed to log Twilio call: ${logResponse.statusText}`);
            }
        } catch (logError) {
            console.error('⚠️ Error logging Twilio call:', logError.message);
        }

        console.log('📝 CALL MAPPING STORED:');
        console.log(`   Twilio SID → Ultravox ID: ${twilioCallSid} → ${response.callId}`);
        console.log(`   Agent: ${agentConfig.agent.agent_name} (ID: ${agentConfig.agent.id}, Slug: ${agentIdentifier})`);
        console.log(`   Active Calls Count: ${activeCalls.size}`);

        // Create TwiML with Status Callback
        const twiml = new twilio.twiml.VoiceResponse();
        const connect = twiml.connect();

        // Use hardcoded HTTPS public URL to ensure Twilio can reach us
        const domain = 'agent.cabex.co.uk';
        const callbackUrl = `https://${domain}/twilio/status-callback?slug=${agentConfig.agent.company_slug}&agent_id=${agentConfig.agent.id}&ultravox_call_id=${response.callId}`;

        console.log(`🔗 Setting Public Status Callback: ${callbackUrl}`);

        const stream = connect.stream({
            url: response.joinUrl,
            name: 'ultravox',
            statusCallback: callbackUrl
        });

        // Also add a general call status callback to capture full call duration if stream ends early
        // Note: This needs to be set on the <Number> or incoming call config usually, but <Stream> statusCallback 
        // helps us track the AI duration specifically.

        const twimlString = twiml.toString();
        console.log('📤 TWIML RESPONSE:');
        console.log(twimlString);

        console.log(`🎯 AI CALL INITIATED - Customer connected to ${agentConfig.agent.agent_name}`);
        console.log('📞 ===== INCOMING CALL SETUP COMPLETE =====\n');

        res.type('text/xml');
        res.send(twimlString);

    } catch (error) {
        console.error('Error handling incoming call:', error);
        const twiml = new twilio.twiml.VoiceResponse();
        twiml.say({ voice: 'alice' }, "Sorry, the application encountered an error.");
        res.type('text/xml');
        res.send(twiml.toString());
    }
});

// 📞 New Endpoint: Handle Call Status / Stream Status for Deduction (Supports GET and POST)
router.all('/status-callback', async (req, res) => {
    try {
        const { slug, agent_id, ultravox_call_id } = req.query;
        console.log(`📉 STATUS CALLBACK RECEIVED for ${slug}. Ultravox Call: ${ultravox_call_id}`);

        let callDurationSeconds = 0;

        // 1. TRY ULTRAVOX FIRST (as requested by user for accurate billed duration)
        if (ultravox_call_id) {
            try {
                // Small delay to let Ultravox finish processing
                await new Promise(r => setTimeout(r, 2000));

                const uvUrl = `https://api.ultravox.ai/api/calls/${ultravox_call_id}`;
                console.log(`🔍 Fetching billedDuration from Ultravox: ${uvUrl}`);

                const response = await fetch(uvUrl, {
                    headers: { 'X-API-Key': process.env.ULTRAVOX_API_KEY }
                });

                if (response.ok) {
                    const data = await response.json();
                    console.log(`📊 Ultravox Call Data: BilledDuration=${data.billedDuration}, EndReason=${data.endOfCallReason}`);

                    if (data.billedDuration) {
                        // billedDuration is a string like "12.34s" or "60s". We need to parse it.
                        callDurationSeconds = parseFloat(data.billedDuration);
                    }
                } else {
                    console.warn(`⚠️ Failed to fetch Ultravox call details: ${response.status}`);
                }
            } catch (err) {
                console.error("❌ Error fetching from Ultravox:", err.message);
            }
        }

        // 2. FALLBACK TO TWILIO IF ULTRAVOX FAILED
        if (callDurationSeconds === 0) {
            console.log("🔄 Falling back to Twilio duration...");
            if (req.body.CallDuration) {
                callDurationSeconds = parseInt(req.body.CallDuration);
            } else if (req.body.Duration) {
                callDurationSeconds = parseInt(req.body.Duration);
            } else if (req.body.CallSid) {
                try {
                    const call = await client.calls(req.body.CallSid).fetch();
                    if (call.duration) {
                        callDurationSeconds = parseInt(call.duration);
                    }
                } catch (e) {
                    console.error("Failed to fetch Twilio call details:", e);
                }
            }
        }

        if (callDurationSeconds > 0 && agent_id) {
            console.log(`💸 DEDUCTING for ${callDurationSeconds}s (Agent ID: ${agent_id})`);

            // Use the unified log-call endpoint for immediate deduction
            try {
                const logResponse = await fetch(`${AGENT_MANAGEMENT_URL}/api/agents/${agent_id}/log-call`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        call_id: ultravox_call_id,
                        cabee_call_id: cabeeCallId,
                        status: 'completed',
                        duration: Math.floor(callDurationSeconds)
                    })
                });
                if (logResponse.ok) {
                    console.log('✅ Immediate deduction successful');
                } else {
                    console.error(`⚠️ Failed to trigger immediate deduction: ${logResponse.statusText}`);
                }
            } catch (err) {
                console.error("❌ Error notifying log-call:", err.message);
            }
        } else {
            console.log("⚠️ No duration to deduct or missing agent_id");
        }

    } catch (error) {
        console.error("Error processing status callback:", error);
    }

    res.sendStatus(200);
});

// Handle incoming calls from Twilio (default fallback)
router.post('/incoming', async (req, res) => {
    const timestamp = new Date().toISOString();
    const callId = `call_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;

    try {
        console.log('\n📞 ===== INCOMING CALL RECEIVED =====');
        console.log(`📅 Timestamp: ${timestamp}`);
        console.log(`🆔 Call Tracking ID: ${callId}`);
        console.log('📥 TWILIO WEBHOOK DATA:');
        console.log(JSON.stringify(req.body, null, 2));

        const twilioCallSid = req.body.CallSid;
        const fromNumber = req.body.From;
        const toNumber = req.body.To;

        console.log('📋 CALL DETAILS:');
        console.log(`   Twilio Call SID: ${twilioCallSid}`);
        console.log(`   From: ${fromNumber}`);
        console.log(`   To: ${toNumber}`);
        console.log(`   Call Status: ${req.body.CallStatus || 'Unknown'}`);
        console.log(`   Direction: ${req.body.Direction || 'Unknown'}`);

        console.log('🤖 CREATING ULTRAVOX AI CALL:');
        console.log('   Config: Alex - Cromwell Cars Dispatcher (Default)');
        console.log('   Tools: address_validate, checkPricing, BookCab');

        // Create the Ultravox call
        const response = await createUltravoxCall(ULTRAVOX_CALL_CONFIG);

        console.log('✅ ULTRAVOX CALL CREATED:');
        console.log(`   Ultravox Call ID: ${response.callId}`);
        console.log(`   Join URL: ${response.joinUrl}`);

        activeCalls.set(response.callId, {
            twilioCallSid: twilioCallSid,
            fromNumber: fromNumber,
            toNumber: toNumber,
            timestamp: timestamp,
            trackingId: callId
        });

        console.log('📝 CALL MAPPING STORED:');
        console.log(`   Twilio SID → Ultravox ID: ${twilioCallSid} → ${response.callId}`);
        console.log(`   Active Calls Count: ${activeCalls.size}`);

        const twiml = new twilio.twiml.VoiceResponse();
        const connect = twiml.connect();
        connect.stream({
            url: response.joinUrl,
            name: 'ultravox'
        });

        const twimlString = twiml.toString();
        console.log('📤 TWIML RESPONSE:');
        console.log(twimlString);

        console.log('🎯 AI CALL INITIATED - Customer connected to Alex');
        console.log('📞 ===== INCOMING CALL SETUP COMPLETE =====\n');

        res.type('text/xml');
        res.send(twimlString);

    } catch (error) {
        console.log('❌ ===== INCOMING CALL ERROR =====');
        console.log(`🆔 Call Tracking ID: ${callId}`);
        console.log(`📅 Timestamp: ${timestamp}`);
        console.log('💥 ERROR DETAILS:', error.message);

        const twiml = new twilio.twiml.VoiceResponse();
        twiml.say('Sorry, there was an error connecting your call.');

        console.log('📤 ERROR TWIML RESPONSE:', twiml.toString());
        console.log('❌ ===== INCOMING CALL ERROR END =====\n');

        res.type('text/xml');
        res.send(twiml.toString());
    }
});

// Handle transfer of calls to another number
router.post('/transferCall', async (req, res) => {
    const { callId } = req.body;
    console.log(`Request to transfer call with callId: ${callId}`);

    try {
        const result = await transferActiveCall(callId);
        res.json(result);
    } catch (error) {
        res.status(500).json(error);
    }
});

router.get('/active-calls', (req, res) => {
    const calls = Array.from(activeCalls.entries()).map(([ultravoxCallId, data]) => ({
        ultravoxCallId,
        ...data
    }));
    res.json(calls);
});

export { router };
