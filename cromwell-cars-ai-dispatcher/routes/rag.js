import express from 'express';
import 'dotenv/config';
import { corpusLookup } from '../ultravox-utils.js';

const router = express.Router();

// Knowledge Base lookup
router.post('/kblookup', async (req, res) => {
  try {
      console.log('KB lookup request received');
      const { ...corpusQueryData } = req.body;

      // If no Fixie API key is configured, return error
      if (!process.env.FIXIE_API_KEY || process.env.FIXIE_API_KEY === 'dummy_key') {
        console.log('Fixie corpus not configured - knowledge base service disabled');
        res.status(501).json({
          success: false,
          error: 'Knowledge base service not available',
          message: 'Fixie corpus integration not configured for this system'
        });
        return;
      }

      const results = await corpusLookup(corpusQueryData.query);
      res.status(200).json({ results });

  } catch (error) {
      console.error('Error handling KB lookup:', error);
      res.status(500).json({ error: 'Knowledge base lookup failed' });
  }
});

export { router };
