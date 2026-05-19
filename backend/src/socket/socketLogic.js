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

        const device = await Device.findOne({ deviceId: 'pi_camera_front' });
        if (device) {
            socket.emit('state_update', { isActive: device.isActive });
        }

        socket.on('toggle_system', async (data) => {
            if (socket.isPi) return;

            // Added upsert to create the device if it doesn't exist, and fixed the deprecation warning
            const updatedDevice = await Device.findOneAndUpdate(
                { deviceId: 'pi_camera_front' },
                { isActive: data.isActive },
                { returnDocument: 'after', upsert: true }
            );

            console.log(`[POWER] System set to: ${updatedDevice.isActive ? 'ARMED' : 'DISARMED'}`);
            io.emit('state_update', { isActive: updatedDevice.isActive });
        });

        socket.on('pi_alert', async (data) => {
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