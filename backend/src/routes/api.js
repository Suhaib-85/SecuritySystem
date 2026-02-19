import express from 'express';
import multer from 'multer';
import rateLimit from 'express-rate-limit';

import { login } from '../controllers/authController.js';
import { getEvents } from '../controllers/eventController.js';
import { uploadVideo, streamVideo } from '../controllers/videoController.js';

import { verifyToken } from '../middleware/auth.js';

const router = express.Router();

const storage = multer.memoryStorage();
const upload = multer({ storage });

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