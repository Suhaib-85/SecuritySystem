import express from 'express';
import multer from 'multer';
import rateLimit from 'express-rate-limit';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

import { login } from '../controllers/authController.js';
import { getEvents } from '../controllers/eventController.js';
import { uploadVideo, streamVideo } from '../controllers/videoController.js';
import { verifyToken } from '../middleware/auth.js';

const router = express.Router();

// --- DISK STORAGE CONFIGURATION ---
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const TEMP_UPLOAD_DIR = path.join(__dirname, '../temp_uploads');

// Ensure the temporary directory exists when the server starts
if (!fs.existsSync(TEMP_UPLOAD_DIR)) {
    fs.mkdirSync(TEMP_UPLOAD_DIR, { recursive: true });
}

const storage = multer.diskStorage({
    destination: (req, file, cb) => {
        cb(null, TEMP_UPLOAD_DIR);
    },
    filename: (req, file, cb) => {
        // Keep the temporary filename unique to prevent overlaps
        cb(null, `temp_${Date.now()}_${file.originalname}`);
    }
});

const upload = multer({ storage });
// ----------------------------------

const loginLimiter = rateLimit({
    windowMs: 15 * 60 * 1000, // 15 minutes
    max: 5,
    message: { error: "Too many login attempts. System locked for 15 minutes." }
});

router.post('/login', loginLimiter, login);
router.get('/events', verifyToken, getEvents);

router.post('/upload', verifyToken, upload.single('video'), (req, res) => {
    const io = req.app.get('socketio');
    uploadVideo(req, res, io);
});

router.get('/video/:id', streamVideo);

export default router;