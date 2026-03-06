import { gfsBucket } from '../config/db.js';
import Event from '../models/Event.js';
import mongoose from 'mongoose';
import jwt from 'jsonwebtoken';
import fs from 'fs';

export const uploadVideo = (req, res, io) => {
    if (!req.file) return res.status(400).json({ error: 'No file received' });

    const { sessionId, fileType, edgeTimestamp } = req.body;
    const filename = `evidence_${Date.now()}_${req.file.originalname}`;
    const writeStream = gfsBucket.openUploadStream(filename, {
        contentType: req.file.mimetype
    });

    const readStream = fs.createReadStream(req.file.path);
    readStream.pipe(writeStream);

    writeStream.on('finish', async () => {
        try {
            // 1. Delete the temporary file from the server's disk
            fs.unlink(req.file.path, (err) => {
                if (err) console.error("Cleanup Error: Failed to delete temp file:", err);
            });
            const newEvent = new Event({
                type: fileType || 'video',
                message: fileType === 'image' ? 'Still Image Captured' : 'Video Recording Uploaded',
                filename,
                videoId: writeStream.id,
                sessionId: sessionId || 'unknown',
                fileType: fileType || 'video',
                timestamp: edgeTimestamp ? new Date(edgeTimestamp) : Date.now()
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
        fs.unlink(req.file.path, (err) => {
            if (err) console.error("Cleanup Error: Failed to delete temp file:", err);
        });
        res.status(500).json({ error: "Error uploading file to database" });
    });
};

export const streamVideo = async (req, res) => {
    try {
        // 1. Verify JWT token synchronously
        const token = req.query.token;
        if (!token) {
            return res.status(401).send("Authentication required");
        }

        try {
            jwt.verify(token, process.env.JWT_SECRET);
        } catch (err) {
            return res.status(401).send("Invalid token");
        }

        const _id = new mongoose.Types.ObjectId(req.params.id);

        // 2. Find event to determine file type
        const event = await Event.findOne({ videoId: _id });
        const contentType = event && event.fileType === 'image' ? 'image/jpeg' : 'video/mp4';

        // 3. Get the file metadata from GridFS to know the total file size
        const files = await gfsBucket.find({ _id }).toArray();
        if (!files || files.length === 0) {
            return res.status(404).send("File not found");
        }
        const fileSize = files[0].length;

        // 4. Handle HTML5 Video Range Requests
        const range = req.headers.range;

        if (range && contentType === 'video/mp4') {
            // Parse the requested byte range
            const parts = range.replace(/bytes=/, "").split("-");
            const start = parseInt(parts[0], 10);
            const end = parts[1] ? parseInt(parts[1], 10) : fileSize - 1;
            const chunksize = (end - start) + 1;

            // Respond with 206 Partial Content
            res.writeHead(206, {
                'Content-Range': `bytes ${start}-${end}/${fileSize}`,
                'Accept-Ranges': 'bytes',
                'Content-Length': chunksize,
                'Content-Type': contentType,
            });

            // Stream ONLY the requested chunk
            // Note: GridFS end byte is exclusive, so we add 1
            const downloadStream = gfsBucket.openDownloadStream(_id, { start, end: end + 1 });
            downloadStream.pipe(res);

        } else {
            // Standard 200 OK for images or browsers not requesting ranges
            res.writeHead(200, {
                'Content-Length': fileSize,
                'Content-Type': contentType,
            });

            const downloadStream = gfsBucket.openDownloadStream(_id);
            downloadStream.pipe(res);
        }

    } catch (err) {
        console.error("Streaming Error:", err);
        if (!res.headersSent) {
            res.status(400).send("Invalid ID format or streaming error");
        }
    }
};