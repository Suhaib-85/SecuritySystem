import jwt from 'jsonwebtoken';
import crypto from 'crypto';
import Device from '../models/Device.js';

export const verifyToken = async (req, res, next) => {
    const authHeader = req.header('Authorization');
    const token = authHeader?.split(' ')[1] || req.query.token;

    if (!token) return res.status(401).json({ error: 'Access denied. No token provided.' });

    try {
        const hashedToken = crypto.createHash('sha256').update(token).digest('hex');
        const validDevice = await Device.findOne({ apiKeyHash: hashedToken, isActive: true });
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