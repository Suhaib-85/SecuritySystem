import { Readable } from 'stream';
import { gfsBucket } from '../config/db.js';
import Event from '../models/Event.js';
import mongoose from 'mongoose';
import jwt from 'jsonwebtoken';

export const uploadVideo = (req, res, io) => {
    if (!req.file) return res.status(400).json({ error: 'No file received' });

    const { sessionId, fileType } = req.body;
    const filename = `evidence_${Date.now()}_${req.file.originalname}`;
    const writeStream = gfsBucket.openUploadStream(filename, {
        contentType: req.file.mimetype
    });

    const readableStream = new Readable();
    readableStream.push(req.file.buffer);
    readableStream.push(null);
    readableStream.pipe(writeStream);

    writeStream.on('finish', async () => {
        try {
            const newEvent = new Event({
                type: fileType || 'video',
                message: fileType === 'image' ? 'Still Image Captured' : 'Video Recording Uploaded',
                filename,
                videoId: writeStream.id,
                sessionId: sessionId || 'unknown',
                fileType: fileType || 'video'
            });
            await newEvent.save();
            io.emit('new_event', newEvent);
            res.status(201).json({ message: 'Upload success', fileId: writeStream.id });
        } catch (dbErr) {
            res.status(500).json({ error: "Failed to save metadata" });
        }
    });

    writeStream.on('error', (err) => {
        console.error("GridFS Write Error:", err);
        res.status(500).json({ error: "Error uploading file to database" });
    });
};

export const streamVideo = async (req, res) => {
    try {
        // Verify JWT token from query parameter
        const token = req.query.token;
        if (!token) {
            return res.status(401).send("Authentication required");
        }

        jwt.verify(token, process.env.JWT_SECRET, (err, decoded) => {
            if (err) {
                return res.status(401).send("Invalid token");
            }
        });

        const _id = new mongoose.Types.ObjectId(req.params.id);
        
        // Find event to determine file type
        const event = await Event.findOne({ videoId: _id });
        
        const downloadStream = gfsBucket.openDownloadStream(_id);
        downloadStream.on('error', () => res.status(404).send("File not found"));
        
        // Set content type based on file type
        if (event && event.fileType === 'image') {
            res.set('Content-Type', 'image/jpeg');
        } else {
            res.set('Content-Type', 'video/mp4');
        }
        
        downloadStream.pipe(res);
    } catch (err) {
        res.status(400).send("Invalid ID format");
    }
};