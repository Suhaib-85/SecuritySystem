import express from 'express';
import multer from 'multer';
import rateLimit from 'express-rate-limit';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

import { login } from '../controllers/authController.js';
import { getEvents, updateEventStatus, deleteEvent } from '../controllers/eventController.js';
import { uploadVideo, streamVideo } from '../controllers/videoController.js';
import { verifyToken } from '../middleware/auth.js';

const router = express.Router();
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const TEMP_UPLOAD_DIR = path.join(__dirname, '../temp_uploads');

if (!fs.existsSync(TEMP_UPLOAD_DIR)) fs.mkdirSync(TEMP_UPLOAD_DIR, { recursive: true });

const storage = multer.diskStorage({
    destination: (req, file, cb) => cb(null, TEMP_UPLOAD_DIR),
    filename: (req, file, cb) => cb(null, `temp_${Date.now()}_${file.originalname}`)
});

const upload = multer({
    storage,
    limits: { fileSize: 50 * 1024 * 1024 },
    fileFilter: (req, file, cb) => {
        if (['video/webm', 'video/mp4', 'image/jpeg', 'image/png'].includes(file.mimetype)) {
            cb(null, true);
        } else {
            cb(new Error(`Forbidden payload mimetype: ${file.mimetype}`));
        }
    }
});

const apiLimiter = rateLimit({ windowMs: 1 * 60 * 1000, max: 150, message: { error: "API rate limit reached." } });
const uploadLimiter = rateLimit({ windowMs: 1 * 60 * 1000, max: 45, message: { error: "Upload velocity threshold reached." } });

// SPECIALIZED MEDIA STREAM LIMITER (Prevents HTML5 429 Errors)
const videoStreamLimiter = rateLimit({
    windowMs: 1 * 60 * 1000,
    max: 2500,
    message: { error: "Streaming buffer limits reached." }
});

router.post('/login', rateLimit({ windowMs: 15 * 60 * 1000, max: 5 }), login);
router.get('/events', verifyToken, apiLimiter, getEvents);
router.patch('/events/:id/status', verifyToken, apiLimiter, updateEventStatus);
router.delete('/events/:id', verifyToken, apiLimiter, deleteEvent);
router.post('/upload', verifyToken, uploadLimiter, upload.single('video'), (req, res, next) => {
    uploadVideo(req, res, req.app.get('socketio')).catch(next);
});
router.get('/video/:id', videoStreamLimiter, (req, res, next) => {
    streamVideo(req, res).catch(next);
});

export default router;