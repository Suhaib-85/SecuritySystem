import { gfsBucket } from '../config/db.js';
import Event from '../models/Event.js';
import mongoose from 'mongoose';
import jwt from 'jsonwebtoken';
import fs from 'fs';

export const uploadVideo = async (req, res, io) => {
    if (!req.file) return res.status(400).json({ error: 'No file metadata mapped.' });

    const { sessionId, fileType, edgeTimestamp } = req.body;
    const filename = `evidence_${Date.now()}_${req.file.originalname}`;

    // Secure async wrapper context
    try {
        await new Promise((resolve, reject) => {
            const writeStream = gfsBucket.openUploadStream(filename, { contentType: req.file.mimetype });
            const readStream = fs.createReadStream(req.file.path);

            // Catch explicit read errors to fix memory leak vulnerabilities
            readStream.on('error', (err) => {
                fs.unlink(req.file.path, () => { });
                reject(err);
            });
            writeStream.on('error', (err) => {
                fs.unlink(req.file.path, () => { });
                reject(err);
            });

            writeStream.on('finish', async () => {
                fs.unlink(req.file.path, (err) => {
                    if (err) console.error("Cache clean leak:", err);
                });

                // Standard native date mapping 
                let cleanTimestamp = new Date();
                if (edgeTimestamp) {
                    const parsedDate = new Date(edgeTimestamp);
                    if (!isNaN(parsedDate.getTime())) cleanTimestamp = parsedDate;
                }

                try {
                    const updatedEvent = await Event.findOneAndUpdate(
                        { sessionId: sessionId || 'unknown' },
                        {
                            $set: {
                                type: 'media',
                                message: fileType === 'image' ? 'Intruder Image Captured' : 'Intruder Video Recorded',
                                filename: filename,
                                videoId: writeStream.id,
                                fileType: fileType || 'video',
                                severity: 'alert',
                                deviceId: req.body.deviceId || 'pi_camera_front'
                            },
                            $setOnInsert: { timestamp: cleanTimestamp, status: 'new' }
                        },
                        { returnDocument: 'after', upsert: true }
                    );

                    io.emit('new_event', updatedEvent);
                    res.status(201).json({ message: 'Upload success', fileId: writeStream.id });
                    resolve();
                } catch (dbErr) {
                    reject(dbErr);
                }
            });

            readStream.pipe(writeStream);
        });
    } catch (err) {
        console.error("Pipeline failure execution context:", err);
        if (!res.headersSent) res.status(500).json({ error: err.message });
    }
};

export const streamVideo = async (req, res) => {
    const token = req.query.token;
    if (!token) return res.status(401).json({ error: "Authorization required" });

    try {
        jwt.verify(token, process.env.JWT_SECRET);
    } catch (err) {
        return res.status(401).json({ error: "Invalid token validation lifecycle" });
    }

    const _id = new mongoose.Types.ObjectId(req.params.id);
    const event = await Event.findOne({ videoId: _id });
    const contentType = event && event.fileType === 'image' ? 'image/jpeg' : 'video/mp4';

    const files = await gfsBucket.find({ _id }).toArray();
    if (!files || files.length === 0) return res.status(404).json({ error: "Target asset missing" });

    const fileSize = files[0].length;
    const range = req.headers.range;

    if (range && contentType === 'video/mp4') {
        const parts = range.replace(/bytes=/, "").split("-");
        const start = parseInt(parts[0], 10);
        const end = parts[1] ? parseInt(parts[1], 10) : fileSize - 1;
        const chunksize = (end - start) + 1;

        res.writeHead(206, {
            'Content-Range': `bytes ${start}-${end}/${fileSize}`,
            'Accept-Ranges': 'bytes',
            'Content-Length': chunksize,
            'Content-Type': contentType,
        });

        gfsBucket.openDownloadStream(_id, { start, end: end + 1 }).pipe(res);
    } else {
        res.writeHead(200, { 'Content-Length': fileSize, 'Content-Type': contentType });
        gfsBucket.openDownloadStream(_id).pipe(res);
    }
};