import jwt from 'jsonwebtoken';

export const verifyToken = (req, res, next) => {
    const authHeader = req.header('Authorization');
    const token = authHeader?.split(' ')[1] || req.query.token;

    if (!token) return res.status(401).json({ error: 'Access denied. No token provided.' });

    if (token === process.env.MOCK_PI_SECRET) {
        req.isPi = true;
        return next();
    }

    try {
        const verified = jwt.verify(token, process.env.JWT_SECRET);
        req.user = verified;
        next();
    } catch (err) {
        res.status(400).json({ error: 'Invalid token.' });
    }
};