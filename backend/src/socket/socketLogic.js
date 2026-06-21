import jwt from 'jsonwebtoken';
import Event from '../models/Event.js';
import Device from '../models/Device.js';
import crypto from 'crypto';

export const setupSocketLogic = (io) => {

    io.use(async (socket, next) => {
        const token = socket.handshake.auth.token;

        if (!token) {
            return next(new Error('Authentication error: No token provided'));
        }

        try {
            const hashedToken = crypto.createHash('sha256').update(token).digest('hex');
            const validDevice = await Device.findOne({ apiKeyHash: hashedToken, isActive: true });
            if (validDevice) {
                socket.isPi = true;
                socket.device = validDevice;
                return next();
            }
            const verified = jwt.verify(token, process.env.JWT_SECRET);
            socket.user = verified;
            socket.isPi = false;
            return next();
        } catch (err) {
            return next(new Error('Authentication error: Invalid or expired token'));
        }
    });

    io.on('connection', async (socket) => {
        const deviceType = socket.isPi ? "RASPBERRY PI" : "ADMIN DASHBOARD";
        console.log(`[CONN] ${deviceType} connected (ID: ${socket.id})`);

        try {
            const device = await Device.findOne({ deviceId: 'pi_camera_front' });
            if (device) {
                socket.emit('state_update', { isActive: device.isActive });
            }
        } catch (err) {
            console.error('[CONN ERROR] Could not emit initial device state:', err);
        }

        socket.on('toggle_system', async (data) => {
            if (socket.isPi) return;

            try {
                // Added upsert to create the device if it doesn't exist, and fixed the deprecation warning
                const updatedDevice = await Device.findOneAndUpdate(
                    { deviceId: 'pi_camera_front' },
                    { isActive: data.isActive },
                    { returnDocument: 'after', upsert: true }
                );

                console.log(`[POWER] System set to: ${updatedDevice.isActive ? 'ARMED' : 'DISARMED'}`);
                io.emit('state_update', { isActive: updatedDevice.isActive });
            } catch (err) {
                // Socket.IO handlers are NOT covered by Express error handling.
                // Without this catch, a DB error here becomes an unhandled promise
                // rejection that can crash the entire server process.
                console.error('[POWER ERROR] Failed to toggle system state:', err);
            }
        });

        socket.on('pi_alert', async (data) => {
            try {
                // VERIFY ON ALERT: Check the database to ensure we are actually armed
                const currentDevice = await Device.findOne({ deviceId: 'pi_camera_front' });

                // If the device isn't found, or if it is currently disarmed, ignore the alert
                if (!currentDevice || !currentDevice.isActive) return;

                console.log(`[ALERT] Motion Detected! Logging to Database...`);

                const newAlert = new Event({
                    type: 'alert',
                    message: data.message || "Motion Detected: Intruder Alert",
                    location: data.location || "Unknown location",
                    sessionId: data.sessionId || 'Unknown session',
                    timestamp: Date.now(),
                    deviceId: socket.device ? socket.device.deviceId : 'pi_camera_front',
                    severity: 'alert',
                    status: 'new'
                });

                await newAlert.save();
                io.emit('new_event', newAlert);
            } catch (err) {
                // Socket.IO handlers bypass Express error handling — an uncaught DB
                // error here would become an unhandled rejection and can crash the
                // whole server. Contain it to this single alert.
                console.error("[ALERT ERROR] Could not process pi_alert:", err);
            }
        });

        socket.on('disconnect', (reason) => {
            console.log(`[DISCONN] ${deviceType} disconnected (Reason: ${reason})`);
        });
    });
};