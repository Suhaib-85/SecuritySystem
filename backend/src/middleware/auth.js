import jwt from 'jsonwebtoken';
import crypto from 'crypto';
import Device from '../models/Device.js';

export const verifyToken = async (req, res, next) => {
    const authHeader = req.header('Authorization');
    const token = authHeader?.split(' ')[1] || req.query.token;

    if (!token) return res.status(401).json({ error: 'Access denied. No token provided.' });

    try {
        const hashedToken = crypto.createHash('sha256').update(token).digest('hex');
        // Authenticate by key only. isActive is the system's ARMED/DISARMED state,
        // not a device-authorization flag — gating auth on it would (wrongly) reject
        // a registered device whenever the system is disarmed.
        const validDevice = await Device.findOne({ apiKeyHash: hashedToken });
        if (validDevice) {
            req.isPi = true;
            req.device = validDevice;
            return next();
        }
        const verified = jwt.verify(token, process.env.JWT_SECRET);
        req.user = verified;
        next();
    } catch (err) {
        res.status(400).json({ error: 'Invalid token.' });
    }
};