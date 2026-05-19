// backend/src/controllers/eventController.js
import Event from '../models/Event.js';
import { gfsBucket } from '../config/db.js';
import mongoose from 'mongoose';

export const getEvents = async (req, res, next) => {
    try {
        const events = await Event.find().sort({ timestamp: -1 });
        res.json(events);
    } catch (err) { next(err); }
};

export const updateEventStatus = async (req, res, next) => {
    const { status } = req.body;
    if (!['new', 'reviewed', 'archived'].includes(status)) {
        return res.status(400).json({ error: "Invalid status state mapped." });
    }
    try {
        const updatedEvent = await Event.findByIdAndUpdate(req.params.id, { status }, { returnDocument: 'after' });
        if (!updatedEvent) return res.status(404).json({ error: "Document location not found." });
        res.json(updatedEvent);
    } catch (err) { next(err); }
};

export const deleteEvent = async (req, res, next) => {
    try {
        const event = await Event.findById(req.params.id);
        if (!event) return res.status(404).json({ error: "Target record absent." });

        if (event.videoId) {
            try {
                await gfsBucket.delete(new mongoose.Types.ObjectId(event.videoId));
            } catch (err) {
                console.error("GridFS file unlink failure core tracking:", err);
            }
        }
        await Event.findByIdAndDelete(req.params.id);
        res.json({ message: 'Event successfully decoupled.' });
    } catch (err) { next(err); }
};