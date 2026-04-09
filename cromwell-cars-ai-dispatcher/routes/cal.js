// Original version courtesy of John George
// https://github.com/askjohngeorge/ai-dialer/blob/13a0c206a69ddafd0a3a4db06a9c483d20b16cc8/src/lib/cal.ts#L4

import express from 'express';
import 'dotenv/config';

const router = express.Router();

// Environment variable validation - REAL API REQUIRED
if (!process.env.CALCOM_API_KEY || process.env.CALCOM_API_KEY === 'dummy_key') {
  console.error('CALCOM API MISSING: Real Cal.com API key required for production use');
}
if (!process.env.CALCOM_EVENT_TYPE_ID || process.env.CALCOM_EVENT_TYPE_ID === '123') {
  console.error('CALCOM EVENT TYPE MISSING: Real Cal.com event type ID required for production use');
}

const BASE_URL = 'https://api.cal.com/v2';

const config = {
  apiKey: process.env.CALCOM_API_KEY,
  eventTypeId: parseInt(process.env.CALCOM_EVENT_TYPE_ID, 10),
};

async function getAvailability(days = 5) {
  try {
    const startTime = new Date().toISOString();
    const endTime = new Date(Date.now() + days * 24 * 60 * 60 * 1000).toISOString();
    
    const params = new URLSearchParams({
      startTime,
      endTime,
      eventTypeId: config.eventTypeId.toString(),
    });
    
    const url = `${BASE_URL}/slots/available?${params}`;

    console.log('Fetching availability from:', url);
    
    const response = await fetch(url, {
      headers: {
        Authorization: `Bearer ${config.apiKey}`,
        'Content-Type': 'application/json',
      },
    });

    if (!response.ok) {
      const errorText = await response.text();
      console.error('Failed to fetch availability:', {
        status: response.status,
        statusText: response.statusText,
        error: errorText,
      });
      throw new Error(`Failed to fetch availability: ${response.statusText}`);
    }

    const data = await response.json();
    console.log('Availability response:', JSON.stringify(data, null, 2));
    
    // Basic validation of response structure
    if (data.status !== 'success' || !data.data?.slots) {
      throw new Error('Invalid response format');
    }
    
    // Convert the date-grouped slots into a flat array
    const slots = Object.values(data.data.slots).flat();
    
    return {
      success: true,
      availability: { slots }
    };
  } catch (error) {
    console.error('Failed to fetch availability:', error);
    return {
      success: false,
      error: 'Failed to fetch availability'
    };
  }
}

// Handle requests for looking up spots on the calendar
router.post('/checkAvailability', async (req, res) => {
  console.log('Cal.com API not configured - feature disabled');
  res.status(501).json({
    success: false,
    error: 'Calendar service not available',
    message: 'Cal.com integration not configured for this system'
  });
});

export { router };