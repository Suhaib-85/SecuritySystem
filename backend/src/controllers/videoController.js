import { Readable } from 'stream';
import { gfsBucket } from '../config/db.js';
import Event from '../models/Event.js';
import mongoose from 'mongoose';

export const uploadVideo = (req, res, io) => {
    if (!req.file) return res.status(400).json({ error: 'No file received' });

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
                type: 'video',
                message: 'Video Recording Uploaded',
                filename,
                videoId: writeStream.id
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
        const _id = new mongoose.Types.ObjectId(req.params.id);
        const downloadStream = gfsBucket.openDownloadStream(_id);
        downloadStream.on('error', () => res.status(404).send("Video not found"));
        res.set('Content-Type', 'video/mp4');
        downloadStream.pipe(res);
    } catch (err) {
        res.status(400).send("Invalid ID format");
    }
};