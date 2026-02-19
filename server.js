import { setServers } from 'dns';
setServers(['8.8.8.8', '8.8.4.4']);
import express from 'express';
import mongoose from 'mongoose';
import { createServer } from 'http';
import { Server } from 'socket.io';
import cors from 'cors';
import path from 'path';
import { fileURLToPath } from 'url';
import dotenv from 'dotenv';
import multer from 'multer';
import { Readable } from 'stream';
import { GridFSBucket } from 'mongodb';
dotenv.config();
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const app = express();
const server = createServer(app);
const io = new Server(server, { cors: { origin: "*" } });
app.use(cors());
app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));
const mongoURI = process.env.MONGO_URI;
let gfsBucket;
mongoose.connect(mongoURI)
    .then(() => {
        console.log('MongoDB Connected.');
        const db = mongoose.connection.db;
        gfsBucket = new GridFSBucket(db, { bucketName: 'uploads' });
    })
    .catch((err) => console.log('MongoDB Error:', err));
const EventSchema = new mongoose.Schema({
    timestamp: { type: Date, default: Date.now },
    type: { type: String, required: true },
    message: String,
    videoId: mongoose.Schema.Types.ObjectId,
    filename: String
});
const Event = mongoose.model('Event', EventSchema);
const storage = multer.memoryStorage();
const upload = multer({ storage });
// REPLACE your current app.post('/upload'...) with this:

app.post('/upload', upload.single('video'), (req, res) => {
    if (!req.file) {
        return res.status(400).json({ error: 'No file received' });
    }

    console.log('Uploading file to GridFS...');

    // 1. Generate Filename manually so we can access it later
    const filename = `evidence_${Date.now()}_${req.file.originalname}`;

    // 2. Create Write Stream
    const writeStream = gfsBucket.openUploadStream(filename, {
        contentType: req.file.mimetype
    });

    // 3. Pipe Buffer to Stream
    const readableStream = new Readable();
    readableStream.push(req.file.buffer);
    readableStream.push(null);
    readableStream.pipe(writeStream);

    // 4. Handle Finish (FIXED: No 'file' argument needed)
    writeStream.on('finish', async () => {
        // FIX: Access .id directly from the writeStream object
        console.log(`File Stored in GridFS! ID: ${writeStream.id}`);

        try {
            const newEvent = new Event({
                type: 'video',
                message: 'Video Recording Uploaded',
                filename: filename,       // Use the variable from step 1
                videoId: writeStream.id   // Use the stream's ID property
            });
            await newEvent.save();

            io.emit('new_event', newEvent);

            res.status(201).json({ message: 'Upload success', fileId: writeStream.id });
        } catch (dbErr) {
            console.error("Metadata Save Failed:", dbErr);
            res.status(500).json({ error: "Failed to save event metadata" });
        }
    });

    writeStream.on('error', (err) => {
        console.error("GridFS Write Error:", err);
        res.status(500).json({ error: "Error uploading file" });
    });
});
app.get('/video/:id', async (req, res) => {
    if (!gfsBucket) return res.status(500).send("DB not ready");
    try {
        const _id = new mongoose.Types.ObjectId(req.params.id);
        const downloadStream = gfsBucket.openDownloadStream(_id);
        downloadStream.on('error', () => res.status(404).send("Video not found"));
        res.set('Content-Type', 'video/mp4');
        downloadStream.pipe(res);
    } catch (err) {
        res.status(400).send("Invalid ID");
    }
});
app.get('/api/events', async (req, res) => {
    try {
        const events = await Event.find().sort({ timestamp: -1 }).limit(20);
        res.json(events);
    } catch (err) {
        res.status(500).json({ error: err.message });
    }
});
let SYSTEM_ACTIVE = false;
io.on('connection', (socket) => {
    console.log('Client connected:', socket.id);
    socket.emit('state_update', { isActive: SYSTEM_ACTIVE });
    socket.on('toggle_system', (data) => {
        SYSTEM_ACTIVE = data.isActive;
        console.log(`[COMMAND] System Set to: ${SYSTEM_ACTIVE}`);
        io.emit('state_update', { isActive: SYSTEM_ACTIVE });
    });
    socket.on('pi_alert', async (data) => {
        if (!SYSTEM_ACTIVE) return;
        console.log("ALERT RECEIVED from Pi");
        const newAlert = new Event({
            type: 'alert',
            message: "Person Detected!",
            timestamp: new Date()
        });
        await newAlert.save();
        io.emit('new_event', newAlert);
    });
});
server.listen(3000, () => {
    console.log('Server running at http://localhost:3000');
});