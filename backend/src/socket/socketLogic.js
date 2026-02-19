import jwt from 'jsonwebtoken';
import Event from '../models/Event.js';

let SYSTEM_ACTIVE = false;

export const setupSocketLogic = (io) => {

    io.use((socket, next) => {
        const token = socket.handshake.auth.token;

        if (!token) {
            return next(new Error('Authentication error: No token provided'));
        }

        if (token === process.env.MOCK_PI_SECRET) {
            socket.isPi = true;
            return next();
        }

        try {
            const verified = jwt.verify(token, process.env.JWT_SECRET);
            socket.user = verified;
            socket.isPi = false;
            return next();
        } catch (err) {
            return next(new Error('Authentication error: Invalid or expired token'));
        }
    });

    io.on('connection', (socket) => {
        const deviceType = socket.isPi ? "RASPBERRY PI" : "ADMIN DASHBOARD";
        console.log(`[CONN] ${deviceType} connected (ID: ${socket.id})`);

        socket.emit('state_update', { isActive: SYSTEM_ACTIVE });

        socket.on('toggle_system', (data) => {
            if (socket.isPi) return;

            SYSTEM_ACTIVE = data.isActive;
            console.log(`[POWER] System set to: ${SYSTEM_ACTIVE ? 'ARMED' : 'DISARMED'}`);

            io.emit('state_update', { isActive: SYSTEM_ACTIVE });
        });

        socket.on('pi_alert', async (data) => {
            if (!SYSTEM_ACTIVE) return;

            console.log(`[ALERT] Motion Detected! Logging to Database...`);

            const newAlert = new Event({
                type: 'alert',
                message: data.message || "Motion Detected: Intruder Alert",
                timestamp: new Date()
            });

            try {
                await newAlert.save();
                io.emit('new_event', newAlert);
            } catch (err) {
                console.error("Database Error: Could not save alert event", err);
            }
        });

        socket.on('disconnect', (reason) => {
            console.log(`[DISCONN] ${deviceType} disconnected (Reason: ${reason})`);
        });
    });
};