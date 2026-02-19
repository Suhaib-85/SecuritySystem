import Event from '../models/Event.js';

export const getEvents = async (req, res) => {
    try {
        const events = await Event.find().sort({ timestamp: -1 }).limit(20);
        res.json(events);
    } catch (err) {
        res.status(500).json({ error: err.message });
    }
};